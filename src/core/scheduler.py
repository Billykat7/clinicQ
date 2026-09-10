"""Background job scheduler — the process-wide APScheduler and its kernel sweeps.

One process-wide :class:`~apscheduler.schedulers.background.BackgroundScheduler` runs scheduled
jobs in the application timezone. The kernel registers only the sweeps it owns:

* the **notification retry sweep**, which re-attempts every due notification with exponential
  backoff and dead-letters it once its attempt budget is spent (see
  :func:`src.modules.notifications.service.run_retry_sweep`);
* the **document retention sweep**, which purges every stored document whose retention has expired
  (see :func:`src.modules.documents.service.run_retention_sweep`), idempotently — it only ever
  removes rows still past their expiry, tombstoning each and recording the deletion; and
* the **grant-usage flush**, which writes this instance's buffered permission hits.

Your own sweeps are added the same way: a ``run_*`` function that takes the advisory lock, and one
``scheduler.add_job`` call in :func:`start_scheduler`.

Each sweep is **safe to run on more than one instance**. Two guards make that so:

* a PostgreSQL *session-level advisory lock* (:func:`_advisory_lock`) elects a single runner per
  run — the instance that fails to take the lock does nothing and returns; and
* a per-sweep database uniqueness backstop makes each idempotent even without the lock.

On any non-PostgreSQL backend (the SQLite test suite) there is no advisory lock and no second
instance, so the lock is treated as always held; the sweep logic itself is exercised directly in
unit tests without going through the scheduler.

Advisory-lock keys must be unique across the application — a collision silently serialises two
unrelated sweeps against each other. Keep new ones in the block below, next to these.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core import permission_usage
from src.core.config import Settings, get_settings
from src.core.s3_logging import APP_TIMEZONE
from src.database.session import get_db_context
from src.modules.documents import service as documents_service
from src.modules.documents.storage import LocalObjectStorage
from src.modules.notifications import service as notifications_service

logger = logging.getLogger(__name__)

# ── advisory-lock keys ───────────────────────────────────────────────────────
# Any constant works as long as every instance contends on the same one. Add yours here.

# Stable 64-bit key for the notification retry sweep's advisory lock.
_NOTIFICATION_RETRY_LOCK_KEY: int = 67

# Stable 64-bit key for the document retention sweep's advisory lock.
_DOCUMENT_RETENTION_LOCK_KEY: int = 70

# Stable 64-bit key for the grant-usage flush's advisory lock. Unlike the other sweeps this one is
# not idempotent across instances — each instance holds its *own* buffer, and the lock only
# serialises the upsert so two flushes cannot interleave a read-modify-write on the same row. An
# instance that fails to take the lock keeps its buffer for the next window rather than dropping
# it, so nothing is lost by losing the race.
_PERMISSION_USAGE_LOCK_KEY: int = 176

# ── job identifiers ──────────────────────────────────────────────────────────
# So a restart replaces rather than duplicates each job.

_NOTIFICATION_RETRY_JOB_ID = "notification_retry_sweep"
_DOCUMENT_RETENTION_JOB_ID = "document_retention_sweep"
_PERMISSION_USAGE_JOB_ID = "permission_usage_flush"

# Process-wide scheduler; created by :func:`start_scheduler`, stopped by :func:`shutdown_scheduler`.
_scheduler: BackgroundScheduler | None = None


@contextmanager
def _advisory_lock(db: Session, key: int) -> Iterator[bool]:
    """Yield whether a PostgreSQL session-level advisory lock ``key`` was acquired.

    On PostgreSQL, ``pg_try_advisory_lock`` returns immediately: ``True`` when this connection
    took the lock, ``False`` when another instance already holds it — the guard that makes the
    sweep safe to run on more than one instance. The lock is always released on exit. On any
    non-PostgreSQL backend (the SQLite test suite) there is no advisory lock and no second
    instance, so it is treated as always acquired.
    """
    if db.bind is None or db.bind.dialect.name != "postgresql":
        yield True
        return

    acquired = bool(
        db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
    )
    try:
        yield acquired
    finally:
        if acquired:
            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})


def run_notification_retry_sweep(settings: Settings | None = None) -> int:
    """Run the notification retry sweep once; return the number delivered this run.

    Elects a single runner across instances via the advisory lock, then delegates re-attempting
    every due notification to :func:`src.modules.notifications.service.run_retry_sweep` — which
    retries each with exponential backoff and dead-letters it once its attempt budget is spent, so
    a transient failure is never lost silently. The session commits on the context-manager exit, so
    status changes persist. Safe to call directly (e.g. a management command) as well as from the
    scheduler.
    """
    _ = settings or get_settings()
    with (
        get_db_context() as db,
        _advisory_lock(db, _NOTIFICATION_RETRY_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.info(
                "Notification retry sweep skipped: another instance holds the lock."
            )
            return 0
        delivered = notifications_service.run_retry_sweep(db)
        if delivered:
            logger.info("Notification retry sweep delivered %d message(s).", delivered)
        return delivered


def run_document_retention_sweep(settings: Settings | None = None) -> int:
    """Run the daily document retention sweep once; return the number of documents purged.

    Elects a single runner across instances via the advisory lock, then delegates the purge to
    :func:`src.modules.documents.service.run_retention_sweep` — which removes each expired
    document's bytes, tombstones the row and writes an audit line, so a deletion is never silent.
    Idempotent (a re-run finds nothing already purged), so a missed day catches up without ill
    effect. Safe to call directly (e.g. a management command) as well as from the scheduler.
    """
    cfg = settings or get_settings()
    storage = LocalObjectStorage(cfg.document_storage_dir)
    with (
        get_db_context() as db,
        _advisory_lock(db, _DOCUMENT_RETENTION_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.info(
                "Document retention sweep skipped: another instance holds the lock."
            )
            return 0
        purged = documents_service.run_retention_sweep(db, storage)
        if purged:
            logger.info("Document retention sweep purged %d document(s).", len(purged))
        return len(purged)


def run_permission_usage_flush(settings: Settings | None = None) -> int:
    """Flush this instance's buffered grant-usage hits; return the number of grants touched.

    Collection itself happens in-process at the three ``ensure_*`` chokepoints and touches no
    database at all; this job is the only thing that writes ``permission_usage``, on an interval,
    so an authorized request never pays for a row.

    Elects a single writer across instances via the advisory lock — not for idempotency (each
    instance buffers its own hits, so there is nothing to double-apply) but so two flushes cannot
    interleave a read-modify-write on the same ``hit_count``. An instance that loses the race
    **keeps its buffer** and flushes it next window; nothing is dropped by not holding the lock.

    A no-op when ``PERMISSION_USAGE_ENABLED`` is false, so turning the feature off stops the writes
    as well as the collection. Safe to call directly (e.g. a management command) as well as from
    the scheduler.
    """
    cfg = settings or get_settings()
    if not cfg.permission_usage_enabled:
        return 0
    with (
        get_db_context() as db,
        _advisory_lock(db, _PERMISSION_USAGE_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug(
                "Grant-usage flush skipped: another instance holds the lock; buffer retained."
            )
            return 0
        touched = permission_usage.flush(db)
        if touched:
            logger.info("Grant-usage flush wrote %d grant(s).", touched)
        return touched


def start_scheduler(settings: Settings | None = None) -> BackgroundScheduler | None:
    """Start the process-wide scheduler and register every enabled job.

    A no-op returning ``None`` when ``SCHEDULER_ENABLED`` is false or a scheduler is already
    running.
    """
    global _scheduler
    cfg = settings or get_settings()
    if not cfg.scheduler_enabled:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false); no jobs registered.")
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone=APP_TIMEZONE)
    scheduler.add_job(
        run_notification_retry_sweep,
        trigger=IntervalTrigger(
            minutes=cfg.notification_retry_interval_minutes, timezone=APP_TIMEZONE
        ),
        id=_NOTIFICATION_RETRY_JOB_ID,
        name="Notification retry sweep",
        # A missed run is coalesced into a single catch-up run; the sweep is idempotent (it only
        # ever re-attempts rows still due and stops at the attempt budget), so a catch-up is safe.
        misfire_grace_time=cfg.notification_retry_interval_minutes * 60,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_document_retention_sweep,
        trigger=CronTrigger(
            hour=cfg.document_retention_sweep_hour, minute=0, timezone=APP_TIMEZONE
        ),
        id=_DOCUMENT_RETENTION_JOB_ID,
        name="Daily document retention sweep",
        # A missed run is coalesced into a single catch-up run; the sweep is idempotent (it only
        # ever purges rows still past their expiry), so replaying a missed day purges nothing extra.
        misfire_grace_time=3600,
        coalesce=True,
        replace_existing=True,
    )
    if cfg.permission_usage_enabled:
        scheduler.add_job(
            run_permission_usage_flush,
            trigger=IntervalTrigger(
                minutes=cfg.permission_usage_flush_minutes, timezone=APP_TIMEZONE
            ),
            id=_PERMISSION_USAGE_JOB_ID,
            name="Grant-usage flush",
            # A missed run is coalesced into one catch-up: the buffer is cumulative, so a skipped
            # window simply flushes with the next one. Nothing is replayed and nothing is doubled.
            misfire_grace_time=cfg.permission_usage_flush_minutes * 60,
            coalesce=True,
            replace_existing=True,
        )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started (%s) with %d job(s): %s.",
        APP_TIMEZONE.key,
        len(scheduler.get_jobs()),
        ", ".join(job.name for job in scheduler.get_jobs()) or "none",
    )
    return scheduler


def shutdown_scheduler() -> None:
    """Stop the process-wide scheduler if one is running (idempotent)."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped.")
