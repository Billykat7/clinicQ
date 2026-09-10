"""Grant usage collection: an in-process buffer and its flush (Issue #176, M29).

``docs/architecture/rbac-decision-transparency.md`` §6. Records **which grant, how recently, roughly
how often** — and nothing else — so an operator pruning a role has evidence instead of nerve.

Why a buffer at all: the three ``ensure_*`` chokepoints in :mod:`src.core.rbac` are on the hot path
of every authorized request. A row per request would be far too hot, and — worse — would make
authorization depend on a write, so a slow or failing usage table could turn a read into a 500. So
collection is:

1. **In-process and coalesced.** One entry per ``(role, resource, verb)`` per flush window, holding
   the latest timestamp and a hit count. Recording is a dict update under a short lock; there is no
   database work on the request path at all, which is what makes the "collection adds no per-request
   query" test in ``tests/unit/security/test_permission_usage.py`` pass.
2. **Flushed by an APScheduler job** (:func:`src.core.scheduler.run_permission_usage_flush`) under
   the same ``_advisory_lock()`` guard the existing sweeps use, so several app instances do not
   fight over the upsert.
3. **Lossy on shutdown, deliberately.** A dropped buffer costs at most a few minutes of freshness on
   a ``last_used_at``. This is **advisory data for pruning decisions, never an audit trail**, and it
   must never be presented as one. Crucially the loss direction is safe: a lost buffer makes a grant
   look *older* than it is, never *newer*, so it can never manufacture a wrong "never used".

**Attribution under multi-role assignment.** A request resolves the union of the caller's active
roles (Issue #136), and the resolver returns one verb without saying which role supplied it. Rather
than pay for a per-role resolution on the hot path, a hit is recorded against **every** active role
of the caller. That over-reports in the one safe direction: a grant that is carrying real traffic is
never marked unused. The worklist is for deciding what to *look at*, and a false "still in use" costs
an operator a second glance, while a false "never used" costs a revoked grant in production.
"""

import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.s3_logging import APP_TIMEZONE
from src.database.models import (
    USAGE_WINDOW_ID,
    PermissionUsage,
    PermissionUsageWindow,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UsageKey:
    """The grant a hit is attributed to — the table's primary key, in memory."""

    role: str
    resource: str
    verb: str


@dataclass(slots=True)
class _Entry:
    """One buffered grant's coalesced state: how many hits, and the most recent one."""

    hits: int
    last_used_at: datetime


#: The process-wide buffer. Guarded by :data:`_lock`, which is held only for the dict update — never
#: across a database call, so a slow flush can never block a request.
_buffer: dict[UsageKey, _Entry] = {}
_lock = threading.Lock()


def record_allow(
    roles: Iterable[str],
    resource: str,
    verb: str,
    *,
    now: datetime | None = None,
) -> None:
    """Buffer one **allowed** decision against each of ``roles`` (Issue #176).

    Called from the ``ensure_*`` chokepoints after they have decided to allow. A no-op — returning
    before touching the lock — when ``PERMISSION_USAGE_ENABLED`` is false, so a deployment that does
    not want the feature pays nothing for it and does not have to patch code.

    Never raises into the caller: this is telemetry hanging off an authorization decision that has
    already been made, and a bug here must not turn an allowed request into a 500.
    """
    if not get_settings().permission_usage_enabled:
        return
    try:
        stamp = now or datetime.now(APP_TIMEZONE)
        with _lock:
            for role in roles:
                key = UsageKey(role=role, resource=resource, verb=verb)
                entry = _buffer.get(key)
                if entry is None:
                    _buffer[key] = _Entry(hits=1, last_used_at=stamp)
                else:
                    entry.hits += 1
                    entry.last_used_at = stamp
    except (
        Exception
    ):  # pragma: no cover - defensive; telemetry must never break a request
        logger.warning("permission usage: buffering failed; hit dropped", exc_info=True)


def buffered() -> dict[UsageKey, _Entry]:
    """Return a copy of the current buffer — for tests and diagnostics, never for a decision."""
    with _lock:
        return {
            key: _Entry(entry.hits, entry.last_used_at)
            for key, entry in _buffer.items()
        }


def reset_buffer() -> None:
    """Drop the buffer without flushing it (test isolation; a lost buffer is acceptable)."""
    with _lock:
        _buffer.clear()


def _drain() -> dict[UsageKey, _Entry]:
    """Atomically take everything buffered so far, leaving an empty buffer behind.

    Swap-and-return rather than read-then-clear: a hit recorded while the flush is writing lands in
    the *new* buffer and is picked up next window, instead of being silently dropped.
    """
    global _buffer
    with _lock:
        drained, _buffer = _buffer, {}
    return drained


def ensure_window(db: Session, *, now: datetime | None = None) -> datetime:
    """Return when usage collection began here, creating the marker row if it is missing.

    The migration seeds this at upgrade time; this covers a database created by ``create_all``
    (the test suite) and any deployment whose row was removed. Without it the worklist would show
    "never used in 90 days" with no way to tell a dead grant from a young window.
    """
    row = db.get(PermissionUsageWindow, USAGE_WINDOW_ID)
    if row is None:
        row = PermissionUsageWindow(
            id=USAGE_WINDOW_ID, started_at=now or datetime.now(APP_TIMEZONE)
        )
        db.add(row)
        db.flush()
    return row.started_at


def collection_started_at(db: Session) -> datetime | None:
    """Return when collection began, or ``None`` when nothing has recorded a window yet.

    Read-only: a *reader* (the permissions matrix) must not create the marker, or simply opening the
    console on a fresh deployment would stamp a window that collection had not actually started.
    """
    return db.execute(
        select(PermissionUsageWindow.started_at).where(
            PermissionUsageWindow.id == USAGE_WINDOW_ID
        )
    ).scalar_one_or_none()


def flush(db: Session, *, now: datetime | None = None) -> int:
    """Write the buffer into ``permission_usage`` and return the number of grants touched.

    Upsert semantics, expressed portably (a read of the existing rows, then updates and inserts)
    rather than through a dialect-specific ``ON CONFLICT``: the table is bounded by the grant count,
    a window's worth of distinct grants is small, and the same code has to run on the SQLite test
    dialect as on PostgreSQL.

    ``last_used_at`` moves forward only. Two instances flushing overlapping windows can arrive out
    of order, and a stored timestamp must never go backwards — that would be the one way this table
    could manufacture a wrong answer rather than a stale one.
    """
    drained = _drain()
    if not drained:
        return 0
    ensure_window(db, now=now)
    existing = {
        (row.role, row.resource, row.verb): row
        for row in db.execute(
            select(PermissionUsage).where(
                PermissionUsage.role.in_({key.role for key in drained})
            )
        ).scalars()
    }
    for key, entry in drained.items():
        row = existing.get((key.role, key.resource, key.verb))
        if row is None:
            db.add(
                PermissionUsage(
                    role=key.role,
                    resource=key.resource,
                    verb=key.verb,
                    last_used_at=entry.last_used_at,
                    hit_count=entry.hits,
                )
            )
            continue
        row.hit_count = int(row.hit_count or 0) + entry.hits
        stored = row.last_used_at
        if stored is not None and stored.tzinfo is None and entry.last_used_at.tzinfo:
            stored = stored.replace(tzinfo=entry.last_used_at.tzinfo)
        if stored is None or entry.last_used_at > stored:
            row.last_used_at = entry.last_used_at
    return len(drained)


def revoke_usage(db: Session, role: str, resource: str) -> None:
    """Delete ``role``'s usage rows for ``resource`` when its grant is revoked (Issue #176).

    Retention needs no sweep — the table is bounded by the grant count — but a row that outlives its
    grant is worse than untidy: re-granting the same cell later would inherit a stale "last used"
    from a grant that no longer exists, which is exactly the kind of misleading evidence this table
    exists to replace.
    """
    db.execute(
        delete(PermissionUsage).where(
            PermissionUsage.role == role, PermissionUsage.resource == resource
        )
    )


def revoke_role_usage(db: Session, role: str) -> None:
    """Delete every usage row for ``role`` — used when the role itself is deleted."""
    db.execute(delete(PermissionUsage).where(PermissionUsage.role == role))


@dataclass(frozen=True, slots=True)
class GrantUsage:
    """A grant's rolled-up usage, as the permissions matrix renders it."""

    last_used_at: datetime | None
    hit_count: int


def usage_for_role(db: Session, role: str) -> dict[str, GrantUsage]:
    """Return ``{resource_key: usage}`` for ``role``, rolled up over the resource tree.

    A hit is recorded against the resource the **request** named, which is not always the resource
    the grant sits on: a grant on ``leases`` authorizes a request for ``lease.details`` by cascading
    down the tree. Attributing the hit to the deciding ancestor would mean resolving *which* level
    decided on the hot path, which is precisely the intermediate state Issue #174 keeps behind an
    opt-in trace.

    So the roll-up happens here instead, at read time, over ``resource_descendants`` — the closure
    table the catalog already maintains. A grant's cell counts every hit in its own subtree, which is
    exactly the question "is this grant load-bearing?". It over-reports in the safe direction: a
    child's own grant also marks its ancestor used, so an ancestor is never wrongly shown as dead.
    """
    rows = db.execute(
        select(
            PermissionUsage.resource,
            PermissionUsage.last_used_at,
            PermissionUsage.hit_count,
        ).where(PermissionUsage.role == role)
    ).all()
    if not rows:
        return {}
    # ``load_resource_descendants`` returns the closure as (ancestor_key, descendant_key) pairs
    # including the self-pair, cache-aware and falling back to the manifest-derived closure on a
    # dialect with no trigger — the same source the named-action cascade reads. One pass over it
    # fans every recorded hit up to the ancestors whose grant could have carried it.
    from src.core.rbac import load_resource_descendants

    ancestors_by_key: dict[str, set[str]] = {}
    for ancestor, descendant in load_resource_descendants(db):
        ancestors_by_key.setdefault(descendant, set()).add(ancestor)
    rolled: dict[str, GrantUsage] = {}
    for resource, last_used_at, hit_count in rows:
        targets = ancestors_by_key.get(resource, set()) | {resource}
        for target in targets:
            current = rolled.get(target)
            if current is None:
                rolled[target] = GrantUsage(
                    last_used_at=last_used_at, hit_count=int(hit_count or 0)
                )
                continue
            newest = current.last_used_at
            if newest is None or (last_used_at is not None and last_used_at > newest):
                newest = last_used_at
            rolled[target] = GrantUsage(
                last_used_at=newest,
                hit_count=current.hit_count + int(hit_count or 0),
            )
    return rolled


def recorded_grant_count(db: Session) -> int:
    """Return how many grants have any recorded usage — the "is the window young?" signal."""
    return int(
        db.execute(select(func.count()).select_from(PermissionUsage)).scalar_one()
    )
