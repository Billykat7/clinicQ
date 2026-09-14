"""The queue snapshot: live queue lengths for many clinics in one read (Issue 36).

Discovery shows a live length for every clinic on a page. Counting tickets per queue per request
would make a list of twenty clinics cost twenty counts, growing with the directory; a snapshot makes
it one read. Two stores, each with one job:

* **Redis** holds a short-lived copy of each queue's snapshot under ``clinicq:qsnap:<queue id>``.
  A page reads all its queues with **one** ``MGET`` (:meth:`RedisSnapshotCache.get_many`).
* **``site_queue_snapshot``** holds the durable copy, written when a queue changes
  (:func:`refresh_snapshots`, write-through) and by the reconciliation sweep.

**The rule that matters: a cold or flushed cache is slow, never wrong.** :func:`cached_waiting_counts`
serves a cached snapshot only while it is younger than ``QUEUE_SNAPSHOT_MAX_AGE_SECONDS``. A queue
Redis does not have, or has too old, is read from the table if the row is fresh enough, and
otherwise counted afresh from the source of truth
(:func:`~src.modules.queues.live.read_waiting_counts`); either way the answer is written back to
Redis for the next reader. An unreachable Redis is a miss on every key, logged once per outage, never
an error page. So flushing Redis can only make the next search slower.

**Staleness is bounded and visible.** Every reading carries ``as_of``, the moment the count was
taken, and the pages show a measured length with its age in seconds.

**Write-through, not invalidation by deletion.** When a ticket joins, is called, cancels or is marked
a no-show (Issues 40, 41, 43, 44), the code that changed it calls :func:`on_queue_changed` in the same
request. The snapshot is recounted and written to both stores before the response, so the next read
anywhere sees the change: well inside a second. Deleting the key instead would leave a window in
which a concurrent reader repopulated it with the old count.

**Drift is repaired by a sweep**, :func:`reconcile_snapshots`, which the scheduler runs as
``run_queue_snapshot_reconciliation`` under a PostgreSQL advisory lock (open decision 1: a ``run_*``
sweep in ``src/core/scheduler.py``, not an ``arq`` worker). It recounts every active queue and
repairs any snapshot, in either store, that disagrees.

**Observable.** ``clinicq_queue_snapshot_reads_total{outcome}`` counts cache hits, table fallbacks
and recounts, and ``clinicq_queue_snapshot_repairs_total`` counts what the sweep fixed; both are on
``/metrics`` with the request metrics (Issue 14). :func:`cache_stats` reads them in-process.

Since Issue 39 the source counts real tickets (today's ``waiting`` ones); the snapshot machinery did
not change for it, which was the point of building it against the reader contract first.
"""

import json
import logging
import threading
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Protocol

from prometheus_client import REGISTRY, Counter
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import SnapshotReadOutcome
from src.commons.time import now_sast, stored_sast
from src.core.config import Settings, get_settings
from src.core.site_scope import published_select
from src.database.models import Queue, SiteQueueSnapshot
from src.modules.queues.live import (
    NOT_MEASURED,
    QueueReading,
    WaitingCountReader,
    read_waiting_counts,
)

logger = logging.getLogger(__name__)

#: Prefix of every key this module writes, so a shared Redis can host other tenants safely.
KEY_NAMESPACE: Final = "clinicq:qsnap:"
#: After a failed Redis call, how long every read skips Redis before trying it again.
RETRY_AFTER_SECONDS: Final = 30

READS = Counter(
    "clinicq_queue_snapshot_reads_total",
    "Queue snapshot lookups by outcome: served from Redis, from the table, or recounted.",
    ["outcome"],
)
REPAIRS = Counter(
    "clinicq_queue_snapshot_repairs_total",
    "Snapshots the reconciliation sweep found out of step with a fresh count and repaired.",
)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One queue's snapshot, as both stores hold it."""

    queue_id: str
    site_id: str
    waiting: int | None
    average_wait_minutes: int | None
    updated_at: datetime

    def reading(self) -> QueueReading:
        """What a discovery reader answers for this queue."""
        return QueueReading(waiting=self.waiting, as_of=self.updated_at)

    def encode(self) -> str:
        """The compact JSON Redis stores."""
        return json.dumps(
            {
                "s": self.site_id,
                "w": self.waiting,
                "a": self.average_wait_minutes,
                "t": self.updated_at.isoformat(),
            },
            separators=(",", ":"),
        )

    @classmethod
    def decode(cls, queue_id: str, raw: str | bytes) -> Snapshot | None:
        """Read a stored value back; anything unreadable is a miss, never an error."""
        try:
            data = json.loads(raw)
            return cls(
                queue_id=queue_id,
                site_id=str(data["s"]),
                waiting=None if data["w"] is None else int(data["w"]),
                average_wait_minutes=None if data["a"] is None else int(data["a"]),
                updated_at=datetime.fromisoformat(data["t"]),
            )
        except ValueError, KeyError, TypeError:
            return None

    def same_figures(self, other: Snapshot) -> bool:
        """Whether two snapshots say the same thing, whenever each was taken."""
        return (self.waiting, self.average_wait_minutes, self.site_id) == (
            other.waiting,
            other.average_wait_minutes,
            other.site_id,
        )


class SnapshotCache(Protocol):
    """Where snapshots are cached. Every method degrades to a miss or a no-op; none raises."""

    def get_many(self, queue_ids: Sequence[str]) -> dict[str, Snapshot]:
        """The cached snapshots among ``queue_ids``, in **one** round trip."""
        ...

    def set_many(self, snapshots: Collection[Snapshot], ttl_seconds: int) -> None:
        """Cache ``snapshots`` for ``ttl_seconds``."""
        ...

    def flush(self) -> int:
        """Remove every snapshot this cache holds; return how many keys went."""
        ...


class NoSnapshotCache:
    """The cache of a deployment with no Redis: every read is a miss, so every read is correct."""

    def get_many(self, queue_ids: Sequence[str]) -> dict[str, Snapshot]:
        """Nothing is cached."""
        del queue_ids
        return {}

    def set_many(self, snapshots: Collection[Snapshot], ttl_seconds: int) -> None:
        """Nothing to store."""
        del snapshots, ttl_seconds

    def flush(self) -> int:
        """Nothing to remove."""
        return 0


class RedisSnapshotCache:
    """Snapshots in Redis, degrading to a miss while the store is unreachable.

    The degraded state is logged once on entry and once on recovery, as the rate-limit backend does
    (:mod:`src.core.rate_limit_backend`): a cache that logs per request during a Redis blip is its own
    incident.

    **A failure opens a short circuit.** After one failed call the cache does not touch Redis again
    for :data:`RETRY_AFTER_SECONDS`; every read in that window is an immediate miss. Without it, each
    search during an outage would wait out a connection timeout before counting from the database,
    which turns "slower" into "seconds per page". Found by the 200 ms test, run against a Redis URL
    that did not resolve.
    """

    def __init__(
        self,
        client: Any,
        *,
        namespace: str = KEY_NAMESPACE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Wrap a ``redis.Redis``-shaped client. ``namespace`` prefixes every key."""
        self._client = client
        self._namespace = namespace
        self._clock = clock
        self._degraded = False
        self._retry_at = 0.0
        self._lock = threading.Lock()

    def _key(self, queue_id: str) -> str:
        return f"{self._namespace}{queue_id}"

    def _open(self) -> bool:
        """Whether the circuit is open: a recent failure, so Redis is not tried yet."""
        return self._degraded and self._clock() < self._retry_at

    def _failed(self, exc: Exception) -> None:
        with self._lock:
            self._retry_at = self._clock() + RETRY_AFTER_SECONDS
            if self._degraded:
                return
            self._degraded = True
        logger.warning(
            "Queue snapshot cache unreachable (%s: %s); counting from the database, and not "
            "trying Redis again for %d s. Reads are slower, not wrong.",
            type(exc).__name__,
            exc,
            RETRY_AFTER_SECONDS,
        )

    def _recovered(self) -> None:
        with self._lock:
            if not self._degraded:
                return
            self._degraded = False
        logger.info("Queue snapshot cache reachable again.")

    def get_many(self, queue_ids: Sequence[str]) -> dict[str, Snapshot]:
        """One ``MGET`` for every queue asked about."""
        if not queue_ids or self._open():
            return {}
        try:
            values = self._client.mget([self._key(queue_id) for queue_id in queue_ids])
        except Exception as exc:  # any transport failure is a miss
            self._failed(exc)
            return {}
        self._recovered()
        found: dict[str, Snapshot] = {}
        for queue_id, raw in zip(queue_ids, values, strict=True):
            if (
                raw is not None
                and (snapshot := Snapshot.decode(queue_id, raw)) is not None
            ):
                found[queue_id] = snapshot
        return found

    def set_many(self, snapshots: Collection[Snapshot], ttl_seconds: int) -> None:
        """One pipeline of ``SET ... EX`` for every snapshot."""
        if not snapshots or self._open():
            return
        try:
            pipe = self._client.pipeline(transaction=False)
            for snapshot in snapshots:
                pipe.set(
                    self._key(snapshot.queue_id), snapshot.encode(), ex=ttl_seconds
                )
            pipe.execute()
        except Exception as exc:
            self._failed(exc)
            return
        self._recovered()

    def flush(self) -> int:
        """Delete every key under the namespace (``SCAN``, never ``KEYS`` or ``FLUSHDB``)."""
        removed = 0
        if self._open():
            return removed
        try:
            batch: list[str] = []
            for key in self._client.scan_iter(match=f"{self._namespace}*", count=500):
                batch.append(key)
                if len(batch) == 500:
                    removed += int(self._client.delete(*batch))
                    batch = []
            if batch:
                removed += int(self._client.delete(*batch))
        except Exception as exc:
            self._failed(exc)
        return removed


_cache: SnapshotCache | None = None
_cache_lock = threading.Lock()


def snapshot_cache(settings: Settings | None = None) -> SnapshotCache:
    """The process-wide cache, built on first use from ``REDIS_URL`` (none configured: no cache).

    A bad URL or a missing driver degrades to :class:`NoSnapshotCache` with a warning rather than
    stopping the application: the snapshot is an optimisation, and every read stays correct without
    it.
    """
    global _cache
    if _cache is not None:
        return _cache
    with _cache_lock:
        if _cache is None:
            cfg = settings or get_settings()
            _cache = _build_cache(cfg)
    return _cache


def _build_cache(cfg: Settings) -> SnapshotCache:
    """Construct the cache the settings select."""
    if not cfg.redis_url:
        return NoSnapshotCache()
    try:
        import redis  # lazily, like the rate limiter: a deployment without Redis needs no driver
    except ImportError:
        logger.warning(
            "REDIS_URL is set but the redis package is missing; no snapshot cache."
        )
        return NoSnapshotCache()
    try:
        client = redis.Redis.from_url(
            cfg.redis_url,
            socket_connect_timeout=cfg.redis_probe_timeout_seconds,
            socket_timeout=cfg.redis_probe_timeout_seconds,
            decode_responses=True,
        )
    except ValueError as exc:
        logger.warning(
            "REDIS_URL is not a usable Redis URL (%s); no snapshot cache.", exc
        )
        return NoSnapshotCache()
    return RedisSnapshotCache(client)


def set_snapshot_cache(cache: SnapshotCache | None) -> None:
    """Replace (or clear, with ``None``) the process-wide cache: the test and reconfigure seam."""
    global _cache
    with _cache_lock:
        _cache = cache


def _fresh(updated_at: datetime, moment: datetime, max_age_seconds: int) -> bool:
    """Whether a snapshot taken at ``updated_at`` may still be shown at ``moment``."""
    return moment - updated_at <= timedelta(seconds=max_age_seconds)


def _count(
    db: Session,
    queues: Collection[Queue],
    source: WaitingCountReader,
    moment: datetime,
) -> list[Snapshot]:
    """A fresh snapshot of each queue from the source of truth, taken at ``moment``."""
    readings = source(db, queues)
    return [
        Snapshot(
            queue_id=queue.id,
            site_id=queue.site_id,
            waiting=readings.get(queue.id, NOT_MEASURED).waiting,
            average_wait_minutes=None,  # the estimator's (Issue 42)
            updated_at=moment,
        )
        for queue in queues
    ]


def cached_waiting_counts(
    db: Session,
    queues: Collection[Queue],
    *,
    source: WaitingCountReader = read_waiting_counts,
    cache: SnapshotCache | None = None,
    settings: Settings | None = None,
    moment: datetime | None = None,
) -> Mapping[str, QueueReading]:
    """The discovery reader: every queue's length in one cache read, correct when the cache is cold.

    Order of preference for each queue, each step only for what the previous one did not answer:
    a fresh Redis snapshot (one ``MGET`` for all), a fresh ``site_queue_snapshot`` row (one query for
    all), and a fresh count from ``source`` (one call for all). What the second and third steps find
    is written back to Redis. Nothing older than ``QUEUE_SNAPSHOT_MAX_AGE_SECONDS`` is ever returned.

    It has the :data:`~src.modules.queues.live.WaitingCountReader` signature, so it is passed to
    :func:`~src.modules.discovery.service.find_nearby_sites` unchanged.
    """
    cfg = settings or get_settings()
    store = cache or snapshot_cache(cfg)
    moment = moment or now_sast()
    queue_list = list(queues)
    if not queue_list:
        return {}
    answers: dict[str, QueueReading] = {}

    cached = store.get_many([queue.id for queue in queue_list])
    for queue_id, snapshot in cached.items():
        if _fresh(snapshot.updated_at, moment, cfg.queue_snapshot_max_age_seconds):
            answers[queue_id] = snapshot.reading()
    READS.labels(outcome=SnapshotReadOutcome.CACHE_HIT.value).inc(len(answers))

    missing = [queue for queue in queue_list if queue.id not in answers]
    warm: list[Snapshot] = []
    if missing:
        rows = db.execute(
            published_select(
                SiteQueueSnapshot, {queue.site_id for queue in missing}
            ).where(SiteQueueSnapshot.queue_id.in_([queue.id for queue in missing]))
        ).scalars()
        for row in rows:
            updated_at = stored_sast(row.updated_at)
            if _fresh(updated_at, moment, cfg.queue_snapshot_max_age_seconds):
                snapshot = Snapshot(
                    row.queue_id,
                    row.site_id,
                    row.waiting,
                    row.average_wait_minutes,
                    updated_at,
                )
                answers[row.queue_id] = snapshot.reading()
                warm.append(snapshot)
        READS.labels(outcome=SnapshotReadOutcome.TABLE.value).inc(len(warm))

    still_missing = [queue for queue in missing if queue.id not in answers]
    if still_missing:
        counted = _count(db, still_missing, source, moment)
        for snapshot in counted:
            answers[snapshot.queue_id] = snapshot.reading()
        warm.extend(counted)
        READS.labels(outcome=SnapshotReadOutcome.RECOUNTED.value).inc(len(counted))

    store.set_many(warm, cfg.queue_snapshot_ttl_seconds)
    return answers


def _write(db: Session, snapshots: Collection[Snapshot]) -> None:
    """Upsert snapshots into ``site_queue_snapshot`` (``merge`` on the primary key). Caller commits."""
    for snapshot in snapshots:
        db.merge(
            SiteQueueSnapshot(
                queue_id=snapshot.queue_id,
                site_id=snapshot.site_id,
                waiting=snapshot.waiting,
                average_wait_minutes=snapshot.average_wait_minutes,
                updated_at=snapshot.updated_at,
            )
        )
    db.flush()


def refresh_snapshots(
    db: Session,
    queues: Collection[Queue],
    *,
    source: WaitingCountReader = read_waiting_counts,
    cache: SnapshotCache | None = None,
    settings: Settings | None = None,
    moment: datetime | None = None,
) -> list[Snapshot]:
    """Write-through: recount ``queues`` and write the result to the table and the cache.

    The table write joins the caller's transaction (the caller commits, with the ticket change that
    caused it), and the cache write happens at once, so the next read anywhere sees the new length.
    """
    cfg = settings or get_settings()
    store = cache or snapshot_cache(cfg)
    counted = _count(db, list(queues), source, moment or now_sast())
    _write(db, counted)
    store.set_many(counted, cfg.queue_snapshot_ttl_seconds)
    return counted


def on_queue_changed(
    db: Session,
    queue: Queue,
    *,
    source: WaitingCountReader = read_waiting_counts,
    cache: SnapshotCache | None = None,
    settings: Settings | None = None,
) -> Snapshot:
    """The hook a join, call-next, cancel or no-show calls for the queue it changed (Issues 40–44)."""
    return refresh_snapshots(
        db, [queue], source=source, cache=cache, settings=settings
    )[0]


def reconcile_snapshots(
    db: Session,
    *,
    source: WaitingCountReader = read_waiting_counts,
    cache: SnapshotCache | None = None,
    settings: Settings | None = None,
    moment: datetime | None = None,
) -> int:
    """Recount every active queue and repair any snapshot, in either store, that disagrees.

    A system sweep across every clinic, so it reads all queues rather than one clinic's (listed in
    the site-scope guard's decisions). Returns how many queues needed a repair. The caller commits.
    """
    cfg = settings or get_settings()
    store = cache or snapshot_cache(cfg)
    moment = moment or now_sast()
    queues = list(
        db.execute(
            select(Queue).where(Queue.is_active.is_(True), Queue.is_deleted.is_(False))
        ).scalars()
    )
    if not queues:
        return 0
    truth = {
        snapshot.queue_id: snapshot for snapshot in _count(db, queues, source, moment)
    }
    stored = {
        row.queue_id: row
        for row in db.execute(
            select(SiteQueueSnapshot).where(SiteQueueSnapshot.queue_id.in_(list(truth)))
        ).scalars()
    }
    cached = store.get_many(list(truth))

    drifted: list[Snapshot] = []
    unrecorded: list[Snapshot] = []
    for queue_id, fresh in truth.items():
        row = stored.get(queue_id)
        hit = cached.get(queue_id)
        # An expired cache key is not drift, and neither is a queue with no row yet: that one is
        # simply recorded. Drift is a stored figure that disagrees with the count.
        row_drifted = row is not None and not fresh.same_figures(
            Snapshot(
                queue_id,
                row.site_id,
                row.waiting,
                row.average_wait_minutes,
                fresh.updated_at,
            )
        )
        cache_drifted = hit is not None and not fresh.same_figures(hit)
        if row_drifted or cache_drifted:
            drifted.append(fresh)
        elif row is None:
            unrecorded.append(fresh)
    # Only what changed is written to the table: one merge per repaired or new queue, not per queue.
    _write(db, drifted + unrecorded)
    # Every fresh count goes to the cache in one pipeline, which also keeps it warm between changes.
    store.set_many(list(truth.values()), cfg.queue_snapshot_ttl_seconds)
    if drifted:
        REPAIRS.inc(len(drifted))
        logger.info("Queue snapshot sweep repaired %d snapshot(s).", len(drifted))
    return len(drifted)


def cache_stats() -> dict[str, float]:
    """The read and repair counters of this process, for a test or an operator's shell."""
    stats = {
        outcome.value: REGISTRY.get_sample_value(
            "clinicq_queue_snapshot_reads_total", {"outcome": outcome.value}
        )
        or 0.0
        for outcome in SnapshotReadOutcome
    }
    stats["repairs"] = (
        REGISTRY.get_sample_value("clinicq_queue_snapshot_repairs_total") or 0.0
    )
    return stats
