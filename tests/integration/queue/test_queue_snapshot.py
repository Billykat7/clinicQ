"""The queue snapshot against a real PostgreSQL + PostGIS and a real Redis (Issue 36).

There is no ticket table until Issue 39, so the source of truth here is :class:`TrueCounts`: a
reader with the same signature as :func:`~src.modules.queues.live.read_waiting_counts`, whose numbers
a test changes the way a join or a call-next would. Everything between it and the search result is
the real code: the Redis cache (one ``MGET``), the ``site_queue_snapshot`` table, the write-through
hook, the reconciliation sweep and its scheduler entry point.

One test per acceptance criterion:

* a discovery list of **20 clinics issues one snapshot read**, not 20 counts;
* **joining updates the snapshot within 1 second**;
* a **cold or flushed cache** produces correct (slower) results, never stale ones;
* the reconciliation job **repairs a deliberately corrupted snapshot**;
* the **cache hit rate is observable**;
* **staleness is bounded** and shown in seconds.

Marked ``postgres`` and ``redis``; each test's keys live under a namespace of its own and are
removed afterwards, so nothing else on the server is touched.
"""

import time as clock
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from datetime import timedelta
from functools import partial
from types import SimpleNamespace
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import AppEnvironment, SiteSector, SnapshotReadOutcome
from src.commons.geo import Coordinates
from src.commons.ids import new_id
from src.commons.time import now_sast
from src.core import scheduler
from src.core.config import Settings
from src.database.models import Queue, SiteQueueSnapshot
from src.main import create_app
from src.modules.discovery.service import find_nearby_sites
from src.modules.queue import snapshot
from src.modules.queue.snapshot import (
    NoSnapshotCache,
    RedisSnapshotCache,
    cache_stats,
    cached_waiting_counts,
    on_queue_changed,
    reconcile_snapshots,
    set_snapshot_cache,
)
from src.modules.queues.live import QueueReading
from src.web.discover import measured_queue_label
from tests.factories import QueueFactory
from tests.integration.discovery.conftest import JOHANNESBURG, add_verified_clinic

pytestmark = [pytest.mark.postgres, pytest.mark.redis]

_CLINICS = 20


class TrueCounts:
    """The source of truth a ticket table will be: ``{queue id: waiting}``, and how often it is asked."""

    def __init__(self) -> None:
        self.waiting: dict[str, int] = {}
        self.calls = 0
        self.queues_asked = 0

    def __call__(
        self, db: Session, queues: Collection[Queue]
    ) -> Mapping[str, QueueReading]:
        del db
        self.calls += 1
        self.queues_asked += len(queues)
        return {
            queue.id: QueueReading(self.waiting.get(queue.id), as_of=now_sast())
            for queue in queues
        }


class CountingRedis:
    """A real Redis client that also counts the commands a test cares about."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client
        self.mget_calls = 0

    def mget(self, keys: list[str]) -> list[Any]:
        self.mget_calls += 1
        return self._client.mget(keys)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


@contextmanager
def statements(engine: Engine) -> Iterator[list[str]]:
    """Every SQL statement ``engine`` runs inside the block."""
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.fixture
def world(migrated_engine: Engine, redis_server_url: str) -> Iterator[SimpleNamespace]:
    """Twenty verified clinics near Johannesburg, two queues each, a real Redis and the true counts."""
    factory = sessionmaker(bind=migrated_engine, autoflush=False)
    with factory() as db:
        for n in range(_CLINICS):
            site_id = add_verified_clinic(
                db,
                slug=f"snapshot-clinic-{n}",
                name=f"Snapshot Clinic {n}",
                sector=SiteSector.PUBLIC,
                location=Coordinates(
                    latitude=-26.20 - n * 0.004, longitude=28.04 + n * 0.004
                ),
            )
            for order in range(2):
                QueueFactory.create(db, site_id=site_id, display_order=order)
        db.commit()
        queues = list(db.execute(select(Queue).order_by(Queue.site_id)).scalars())

    client = CountingRedis(
        redis.Redis.from_url(redis_server_url, decode_responses=True)
    )
    cache = RedisSnapshotCache(client, namespace=f"clinicq:qsnap:test:{new_id()}:")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret="m5-snapshot-test-secret-min-32-characters",
        queue_snapshot_ttl_seconds=60,
        queue_snapshot_max_age_seconds=30,
    )
    truth = TrueCounts()
    for n, queue in enumerate(queues):
        truth.waiting[queue.id] = n % 7
    reader = partial(
        cached_waiting_counts, source=truth, cache=cache, settings=settings
    )
    set_snapshot_cache(cache)
    yield SimpleNamespace(
        engine=migrated_engine,
        session=factory,
        queues=queues,
        cache=cache,
        redis=client,
        truth=truth,
        reader=reader,
        settings=settings,
    )
    cache.flush()
    set_snapshot_cache(None)


def _search(world: SimpleNamespace, **extra: Any) -> Any:
    """The discovery list of twenty clinics, read through the snapshot."""
    with world.session() as db:
        return find_nearby_sites(
            db,
            JOHANNESBURG,
            radius_m=20_000,
            limit=_CLINICS,
            reader=world.reader,
            **extra,
        )


def _lengths(result: Any) -> dict[str, list[int | None]]:
    """``{clinic slug: [waiting per queue]}``, the figures a patient sees."""
    return {
        clinic.slug: [q.waiting for q in clinic.queues] for clinic in result.clinics
    }


def _expected(world: SimpleNamespace) -> dict[str, list[int | None]]:
    """What the source of truth says right now, in the same shape."""
    with world.session() as db:
        truth = find_nearby_sites(
            db,
            JOHANNESBURG,
            radius_m=20_000,
            limit=_CLINICS,
            reader=partial(
                cached_waiting_counts,
                source=world.truth,
                cache=NoSnapshotCache(),
                settings=world.settings.model_copy(
                    update={"queue_snapshot_max_age_seconds": 1}
                ),
                moment=now_sast()
                + timedelta(hours=1),  # every stored row too old to use
            ),
        )
    return _lengths(truth)


# --------------------------------------------------------------------------------------
# Twenty clinics, one snapshot read
# --------------------------------------------------------------------------------------


def test_a_list_of_twenty_clinics_issues_one_snapshot_read_not_twenty_counts(
    world: SimpleNamespace,
) -> None:
    """Warm: one MGET, no count, no snapshot-table query. Cold: one count for all 40 queues, not 20."""
    cold_calls = world.truth.calls
    cold = _search(world)
    assert len(cold.clinics) == _CLINICS
    # Cold cache and empty table: the source was asked once, for every queue on the page together.
    assert world.truth.calls - cold_calls == 1
    assert world.truth.queues_asked == 2 * _CLINICS

    world.redis.mget_calls = 0
    calls_before = world.truth.calls
    with statements(world.engine) as seen:
        warm = _search(world)
    assert world.redis.mget_calls == 1, "one snapshot read for the whole page"
    assert world.truth.calls == calls_before, "no count on a warm page"
    assert not [s for s in seen if "site_queue_snapshot" in s]
    assert _lengths(warm) == _lengths(cold) == _expected(world)


# --------------------------------------------------------------------------------------
# Write-through within a second
# --------------------------------------------------------------------------------------


def test_joining_a_queue_updates_the_snapshot_within_one_second(
    world: SimpleNamespace,
) -> None:
    """A join changes the count; the hook runs; the very next read, anywhere, sees it from the cache."""
    _search(world)  # warm
    queue = world.queues[0]
    before = world.truth.waiting[queue.id]
    world.truth.waiting[queue.id] = before + 1  # a patient joined

    started = clock.perf_counter()
    with world.session() as db:
        on_queue_changed(
            db,
            db.get(Queue, queue.id),
            source=world.truth,
            cache=world.cache,
            settings=world.settings,
        )
        db.commit()
    elapsed = clock.perf_counter() - started

    calls = world.truth.calls
    with world.session() as db:
        seen = world.reader(db, [db.get(Queue, queue.id)])
    assert elapsed < 1.0, f"write-through took {elapsed:.3f} s"
    assert seen[queue.id].waiting == before + 1
    assert world.truth.calls == calls, (
        "served from the cache the hook wrote, not recounted"
    )
    with world.session() as db:
        stored = db.get(SiteQueueSnapshot, queue.id)
    assert stored is not None and stored.waiting == before + 1
    print(f"\nwrite-through: {elapsed * 1000:.1f} ms")  # noqa: T201


# --------------------------------------------------------------------------------------
# A cold or flushed cache is slow, never wrong
# --------------------------------------------------------------------------------------


def test_a_flushed_cache_gives_correct_results_never_stale_ones(
    world: SimpleNamespace,
) -> None:
    """Warm the cache, change every count behind its back, flush Redis, search: the new counts."""
    warm = _search(world)
    for queue in world.queues:
        world.truth.waiting[queue.id] += (
            10  # the world moved on; the cache still holds the old figures
        )
    with world.session() as db:
        for queue in world.queues[
            :5
        ]:  # five changes went through the write-through path
            on_queue_changed(
                db,
                queue,
                source=world.truth,
                cache=world.cache,
                settings=world.settings,
            )
        db.commit()

    removed = world.cache.flush()
    started = clock.perf_counter()
    cold = _search(world)
    cold_ms = (clock.perf_counter() - started) * 1000
    started = clock.perf_counter()
    rewarmed = _search(world)
    warm_ms = (clock.perf_counter() - started) * 1000

    assert removed == 2 * _CLINICS
    assert _lengths(cold) == _expected(world)
    assert _lengths(cold) != _lengths(warm)
    assert _lengths(rewarmed) == _lengths(cold)
    report = f"\nflushed {removed} keys; cold search {cold_ms:.1f} ms, then warm {warm_ms:.1f} ms"
    print(report)  # noqa: T201


def test_an_unreachable_redis_gives_correct_results(world: SimpleNamespace) -> None:
    """Redis on a port nothing listens on: every read is a miss, and the figures are still right."""
    dead = RedisSnapshotCache(
        redis.Redis.from_url(
            "redis://127.0.0.1:1/0", socket_connect_timeout=0.2, socket_timeout=0.2
        )
    )
    reader = partial(
        cached_waiting_counts, source=world.truth, cache=dead, settings=world.settings
    )
    with world.session() as db:
        result = find_nearby_sites(
            db, JOHANNESBURG, radius_m=20_000, limit=_CLINICS, reader=reader
        )
    assert _lengths(result) == _expected(world)


def test_a_snapshot_older_than_the_bound_is_never_served(
    world: SimpleNamespace,
) -> None:
    """A cached figure 31 seconds old at a 30-second bound is recounted, not shown."""
    queue = world.queues[0]
    old = snapshot.Snapshot(
        queue.id, queue.site_id, 999, None, now_sast() - timedelta(seconds=31)
    )
    world.cache.set_many([old], 60)
    with world.session() as db:
        seen = world.reader(db, [queue])
    assert seen[queue.id].waiting == world.truth.waiting[queue.id]
    assert seen[queue.id].as_of is not None
    assert now_sast() - seen[queue.id].as_of < timedelta(
        seconds=world.settings.queue_snapshot_max_age_seconds
    )


# --------------------------------------------------------------------------------------
# Reconciliation repairs corruption
# --------------------------------------------------------------------------------------


def test_the_reconciliation_job_repairs_deliberately_corrupted_snapshots(
    world: SimpleNamespace,
) -> None:
    """Corrupt a table row and a Redis key by hand; one sweep puts both back to the true count."""
    _search(world)
    with world.session() as db:
        reconcile_snapshots(
            db, source=world.truth, cache=world.cache, settings=world.settings
        )
        db.commit()
    row_queue, key_queue = world.queues[0], world.queues[1]
    with world.session() as db:
        db.execute(
            update(SiteQueueSnapshot)
            .where(SiteQueueSnapshot.queue_id == row_queue.id)
            .values(waiting=999)
        )
        db.commit()
    world.cache.set_many(
        [snapshot.Snapshot(key_queue.id, key_queue.site_id, 42, None, now_sast())], 60
    )

    with world.session() as db:
        repaired = reconcile_snapshots(
            db, source=world.truth, cache=world.cache, settings=world.settings
        )
        db.commit()
        again = reconcile_snapshots(
            db, source=world.truth, cache=world.cache, settings=world.settings
        )
        db.commit()
        row = db.get(SiteQueueSnapshot, row_queue.id)

    assert repaired == 2 and again == 0
    assert row is not None and row.waiting == world.truth.waiting[row_queue.id]
    assert (
        world.cache.get_many([key_queue.id])[key_queue.id].waiting
        == world.truth.waiting[key_queue.id]
    )


def test_the_scheduled_sweep_runs_under_the_advisory_lock_and_repairs(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_queue_snapshot_reconciliation``, as the scheduler calls it, repairs a corrupted row.

    It reads the default source, the direct read, which says "not measured" until Issue 39, so a
    row corrupted to 999 is repaired to ``None``. While another connection holds the lock, it does
    nothing.
    """
    queue = world.queues[0]
    with world.session() as db:
        db.merge(
            SiteQueueSnapshot(
                queue_id=queue.id,
                site_id=queue.site_id,
                waiting=999,
                updated_at=now_sast(),
            )
        )
        db.commit()

    @contextmanager
    def test_db() -> Iterator[Session]:
        with world.session() as db:
            yield db
            db.commit()

    monkeypatch.setattr(scheduler, "get_db_context", test_db)
    with world.engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(336)"))
        assert scheduler.run_queue_snapshot_reconciliation(world.settings) == 0
        holder.execute(text("SELECT pg_advisory_unlock(336)"))
    assert scheduler.run_queue_snapshot_reconciliation(world.settings) >= 1
    with world.session() as db:
        repaired = db.get(SiteQueueSnapshot, queue.id)
    assert repaired is not None and repaired.waiting is None


def test_the_sweep_is_registered_with_the_scheduler() -> None:
    """One job, on the configured interval, replacing rather than duplicating itself on restart."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        scheduler_enabled=True,
        queue_snapshot_reconcile_seconds=45,
    )
    running = scheduler.start_scheduler(settings)
    try:
        assert running is not None
        job = running.get_job("queue_snapshot_reconciliation")
        assert job is not None
        assert job.func is scheduler.run_queue_snapshot_reconciliation
        assert job.trigger.interval == timedelta(seconds=45)
    finally:
        scheduler.shutdown_scheduler()
        scheduler._scheduler = None


# --------------------------------------------------------------------------------------
# Observable, and shown in seconds
# --------------------------------------------------------------------------------------


def test_the_cache_hit_rate_is_observable(world: SimpleNamespace) -> None:
    """The counters move with the reads, and ``/metrics`` exposes them."""
    before = cache_stats()
    _search(world)  # cold: recounts
    _search(world)  # warm: hits
    after = cache_stats()
    assert (
        after[SnapshotReadOutcome.RECOUNTED.value]
        - before[SnapshotReadOutcome.RECOUNTED.value]
        == 2 * _CLINICS
    )
    assert (
        after[SnapshotReadOutcome.CACHE_HIT.value]
        - before[SnapshotReadOutcome.CACHE_HIT.value]
        == 2 * _CLINICS
    )

    app = create_app(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            environment=AppEnvironment.DEVELOPMENT,
            metrics_enabled=True,
            jwt_secret="m5-snapshot-test-secret-min-32-characters",
        )
    )
    body = TestClient(app).get("/metrics").text
    assert 'clinicq_queue_snapshot_reads_total{outcome="cache_hit"}' in body
    assert "clinicq_queue_snapshot_repairs_total" in body


def test_a_measured_length_is_shown_with_its_age_in_seconds(
    world: SimpleNamespace,
) -> None:
    """What the list and the detail page print next to a counted queue."""
    result = _search(world)
    queue = next(q for clinic in result.clinics for q in clinic.queues if q.waiting)
    label = measured_queue_label(queue.waiting, queue.as_of, now_sast())
    assert label.startswith(f"{queue.waiting} ")
    assert label.endswith(" s ago") and ", counted " in label
    assert (
        measured_queue_label(None, queue.as_of, now_sast())
        == "Queue length not reported yet"
    )
