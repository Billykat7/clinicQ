"""Background job scheduler — the process-wide APScheduler and its kernel sweeps.

One process-wide :class:`~apscheduler.schedulers.background.BackgroundScheduler` runs scheduled
jobs in the application timezone. The kernel registers only the sweeps it owns:

* the **notification retry sweep**, which re-attempts every due notification with exponential
  backoff and dead-letters it once its attempt budget is spent (see
  :func:`src.modules.notifications.service.run_retry_sweep`);
* the **document retention sweep**, which purges every stored document whose retention has expired
  (see :func:`src.modules.documents.service.run_retention_sweep`), idempotently — it only ever
  removes rows still past their expiry, tombstoning each and recording the deletion; and
* the **grant-usage flush**, which writes this instance's buffered permission hits; and
* the **queue snapshot reconciliation** (Issue 36), which recounts every active queue and repairs
  any cached or stored snapshot that has drifted from the count (see
  :func:`src.modules.queue.snapshot.reconcile_snapshots`); and
* the **display device watch** (Issue 61), which alerts the team once about each waiting-room board
  silent for ``DISPLAY_DEVICE_SILENT_MINUTES`` and once when it is back (see
  :func:`src.modules.display.devices.watch_devices`); and
* the **recall timers** (Issue 43), which recall a called patient who has not arrived, once, and
  then mark them a no-show (see :func:`src.modules.queue.timers.run_recall_timers`). Open decision 1
  was settled for this sweep: an APScheduler job under the advisory lock, not an ``arq`` worker,
  because the deadlines live in the database and this module already gives single-runner sweeps.
* the **call-forward sweep** (Issue 86), which tells a patient waiting away from a clinic with a virtual
  waiting room when to leave, once, from the same estimate their ticket page shows (see
  :func:`src.modules.appointments.virtual_waiting.run_call_forward`); and
* the **feedback comment retention sweep** (Issue 87), which empties post-visit comments past their
  clinic's patient-text retention and keeps the scores (see
  :func:`src.modules.appointments.feedback.purge_expired_comments`); and
* the **appointment reminders** (Issue 82), a day and two hours before each booking, once each, answered by
  reply (see :func:`src.modules.appointments.reminders.send_due`); and
* the **appointment conversion** (Issue 81), which turns each booking into a ticket through the join
  service at its clinic's lead time, once, and lapses bookings whose day ended unconverted (see
  :func:`src.modules.appointments.conversion.convert_due`); and
* the **appointment slot generation** (Issue 80), which rolls every listed clinic's bookable slots
  forward to its booking horizon each night (see
  :func:`src.modules.appointments.service.generate_published`).

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
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.commons.enums import NotificationChannel
from src.core import permission_usage
from src.core.config import Settings, get_settings
from src.core.s3_logging import APP_TIMEZONE
from src.database.session import get_db_context
from src.modules.appointments import chronic, virtual_waiting
from src.modules.appointments import conversion as appointments_conversion
from src.modules.appointments import feedback as appointments_feedback
from src.modules.appointments import reminders as appointments_reminders
from src.modules.appointments import service as appointments_service
from src.modules.display import devices as display_devices
from src.modules.documents import service as documents_service
from src.modules.documents.storage import LocalObjectStorage
from src.modules.notifications import delivery_stats
from src.modules.notifications import service as notifications_service
from src.modules.queue import request_keys
from src.modules.queue import snapshot as queue_snapshot
from src.modules.queue import timers as queue_timers
from src.modules.visits import notes as visit_notes

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

# Stable 64-bit key for the queue snapshot reconciliation (Issue 36). Idempotent across instances
# (it overwrites a snapshot with a fresh count), so the lock only saves a second instance the work.
_QUEUE_SNAPSHOT_LOCK_KEY: int = 336

# Stable 64-bit key for the recall timer sweep (Issue 43). The lock is what makes "never
# double-fires" hold across instances; the row locks and the lifecycle's expected-status check hold
# it even without the lock.
_RECALL_TIMERS_LOCK_KEY: int = 443

# Stable 64-bit key for the visit note retention sweep (Issue 53). Idempotent across instances (it
# deletes only what is past its expiry), so the lock only saves a second instance the work.
_VISIT_NOTE_RETENTION_LOCK_KEY: int = 553
_QUEUE_REQUEST_KEY_LOCK_KEY: int = 554

# Stable 64-bit key for the display device watch (Issue 61). The lock is what keeps a silent board to
# one alert when several instances run the watch in the same minute.
_DISPLAY_DEVICE_WATCH_LOCK_KEY: int = 661
#: The delivery failure watch (Issue 71): one runner, so a failing transport is measured once per run.
_NOTIFICATION_FAILURE_WATCH_LOCK_KEY: int = 771
#: The appointment slot generation (Issue 80). Idempotent across instances (the unique slot start
#: refuses a duplicate), so the lock only saves a second instance the work and the refused insert.
_APPOINTMENT_SLOTS_LOCK_KEY: int = 880
#: The appointment conversion (Issue 81). One runner, though the booking's conditional move to ``converted``
#: and ``uq_ticket_appointment`` keep a booking to one ticket even without the lock.
_APPOINTMENT_CONVERSION_LOCK_KEY: int = 881
#: The appointment reminders (Issue 82). One runner, though each reminder's conditional claim and dedupe key keep it
#: to one message even without the lock.
_APPOINTMENT_REMINDERS_LOCK_KEY: int = 882
#: The chronic collection reminders (Issue 85). One runner, though ``reminded_for`` and ``followed_up_for``
#: keep each message to one per cycle even without the lock.
_CHRONIC_COLLECTIONS_LOCK_KEY: int = 883
#: The call-forward sweep (Issue 86). The lock keeps a slow run and the next from overlapping; the claim on
#: ``leave_alert_at`` and the notification's dedupe key keep an alert to one even without it.
_CALL_FORWARD_LOCK_KEY: int = 886
#: The feedback comment retention sweep (Issue 87). Idempotent (it empties only comments past their expiry), so
#: the lock only saves a second instance the work.
_FEEDBACK_RETENTION_LOCK_KEY: int = 887

# ── job identifiers ──────────────────────────────────────────────────────────
# So a restart replaces rather than duplicates each job.

_NOTIFICATION_RETRY_JOB_ID = "notification_retry_sweep"
_DOCUMENT_RETENTION_JOB_ID = "document_retention_sweep"
_PERMISSION_USAGE_JOB_ID = "permission_usage_flush"
_QUEUE_SNAPSHOT_JOB_ID = "queue_snapshot_reconciliation"
_RECALL_TIMERS_JOB_ID = "queue_recall_timers"
_VISIT_NOTE_RETENTION_JOB_ID = "visit_note_retention_sweep"
_QUEUE_REQUEST_KEY_JOB_ID = "queue_request_key_sweep"
_DISPLAY_DEVICE_WATCH_JOB_ID = "display_device_watch"
_NOTIFICATION_FAILURE_WATCH_JOB_ID = "notification_failure_watch"
_APPOINTMENT_SLOTS_JOB_ID = "appointment_slot_generation"
_APPOINTMENT_CONVERSION_JOB_ID = "appointment_conversion"
_APPOINTMENT_REMINDERS_JOB_ID = "appointment_reminders"
_CHRONIC_COLLECTIONS_JOB_ID = "chronic_collections"
_CALL_FORWARD_JOB_ID = "virtual_waiting_call_forward"
_FEEDBACK_RETENTION_JOB_ID = "feedback_comment_retention_sweep"

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


def run_notification_failure_watch(
    *, moment: datetime | None = None, settings: Settings | None = None
) -> list[NotificationChannel]:
    """Alert the team when a transport's delivery failure rate crosses its threshold, once per window (Issue 71).

    Elects a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.notifications.delivery_stats.watch_failures`. Safe to call directly.
    """
    with (
        get_db_context() as db,
        _advisory_lock(db, _NOTIFICATION_FAILURE_WATCH_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug(
                "Notification failure watch skipped: another instance holds the lock."
            )
            return []
        return delivery_stats.watch_failures(db, now=moment, settings=settings)


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


def run_queue_snapshot_reconciliation(settings: Settings | None = None) -> int:
    """Run the queue snapshot reconciliation once; return the number of snapshots repaired.

    Elects a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.queue.snapshot.reconcile_snapshots`, which recounts every active queue and
    repairs any snapshot, in Redis or in ``site_queue_snapshot``, that disagrees with the count.
    Idempotent: a second run finds nothing to repair. Safe to call directly as well as from the
    scheduler.
    """
    cfg = settings or get_settings()
    with (
        get_db_context() as db,
        _advisory_lock(db, _QUEUE_SNAPSHOT_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug(
                "Queue snapshot sweep skipped: another instance holds the lock."
            )
            return 0
        return queue_snapshot.reconcile_snapshots(db, settings=cfg)


def run_recall_timer_sweep(
    settings: Settings | None = None, *, moment: datetime | None = None
) -> int:
    """Run the recall timers once; return how many tickets were recalled or marked no-show.

    Elects a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.queue.timers.run_recall_timers`. Nothing is held in memory between runs, so a
    restarted process picks up every deadline exactly where the last run left it. ``moment`` is for
    tests and operators replaying a sweep; the scheduler passes nothing (now). Safe to call directly.
    """
    cfg = settings or get_settings()
    with (
        get_db_context() as db,
        _advisory_lock(db, _RECALL_TIMERS_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug("Recall timer sweep skipped: another instance holds the lock.")
            return 0
        swept = queue_timers.run_recall_timers(db, moment=moment, settings=cfg)
        if swept.moved:
            logger.info(
                "Recall timers: %d recalled, %d marked no-show.",
                len(swept.recalled),
                len(swept.no_shows),
            )
        return swept.moved


def run_visit_note_retention_sweep(
    settings: Settings | None = None, *, moment: datetime | None = None
) -> int:
    """Delete the visit notes past their retention window once; return how many (Issue 53).

    Elects a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.visits.notes.purge_expired_notes`, which deletes the rows and records one audit
    line with the count and never the content. Idempotent: a re-run finds nothing already deleted, so a
    missed night catches up without ill effect. Safe to call directly as well as from the scheduler.
    """
    with (
        get_db_context() as db,
        _advisory_lock(db, _VISIT_NOTE_RETENTION_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug(
                "Visit note retention sweep skipped: another instance holds the lock."
            )
            return 0
        purged = visit_notes.purge_expired_notes(db, moment=moment)
        if purged:
            logger.info("Visit note retention sweep deleted %d note(s).", purged)
        return purged


def run_queue_request_key_sweep(*, moment: datetime | None = None) -> int:
    """Delete the queue request keys older than a day once; return how many (Issue 50).

    A key only has to outlive the retries of the action it was sent with, so a day is generous. Elects
    a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.queue.request_keys.purge_request_keys`. Idempotent and safe to call directly.
    """
    with (
        get_db_context() as db,
        _advisory_lock(db, _QUEUE_REQUEST_KEY_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug(
                "Queue request key sweep skipped: another instance holds the lock."
            )
            return 0
        purged = request_keys.purge_request_keys(db, moment=moment)
        if purged:
            logger.info("Queue request key sweep deleted %d key(s).", purged)
        return purged


def run_display_device_watch(
    *, moment: datetime | None = None, settings: Settings | None = None
) -> display_devices.WatchResult:
    """Alert about silent waiting-room boards and boards back online, once each; tidy unpaired boxes (Issue 61).

    Elects a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.display.devices.watch_devices` (one alert per silence, naming the board and its
    clinic) and :func:`~src.modules.display.devices.purge_unpaired`. Safe to call directly.
    """
    with (
        get_db_context() as db,
        _advisory_lock(db, _DISPLAY_DEVICE_WATCH_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug(
                "Display device watch skipped: another instance holds the lock."
            )
            return display_devices.WatchResult(silent=(), back=())
        result = display_devices.watch_devices(db, moment=moment, settings=settings)
        purged = display_devices.purge_unpaired(db, moment=moment)
        if purged:
            logger.info("Display device watch removed %d unpaired box(es).", purged)
        return result


def run_call_forward_sweep(*, moment: datetime | None = None) -> int:
    """Tell every travelling patient whose time to leave has come, once; return how many were told (Issue 86).

    Elects a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.appointments.virtual_waiting.run_call_forward`. Safe to call directly.
    """
    with (
        get_db_context() as db,
        _advisory_lock(db, _CALL_FORWARD_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug("Call-forward sweep skipped: another instance holds the lock.")
            return 0
        told = virtual_waiting.run_call_forward(db, moment=moment)
        if told:
            logger.info("Call-forward sweep told %d patient(s) to leave.", told)
        return told


def run_feedback_comment_retention_sweep(*, moment: datetime | None = None) -> int:
    """Empty the post-visit comments past their retention once; return how many (Issue 87).

    Elects a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.appointments.feedback.purge_expired_comments`, which records one audit line with the
    count and never the words. Idempotent, so a missed night catches up. Safe to call directly.
    """
    with (
        get_db_context() as db,
        _advisory_lock(db, _FEEDBACK_RETENTION_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug(
                "Feedback comment retention sweep skipped: another instance holds the lock."
            )
            return 0
        purged = appointments_feedback.purge_expired_comments(db, moment=moment)
        if purged:
            logger.info(
                "Feedback comment retention sweep emptied %d comment(s).", purged
            )
        return purged


def run_appointment_reminders(*, moment: datetime | None = None) -> int:
    """Send every appointment reminder whose window has come, once each; return how many (Issue 82).

    Elects a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.appointments.reminders.send_due`. Safe to call directly.
    """
    with (
        get_db_context() as db,
        _advisory_lock(db, _APPOINTMENT_REMINDERS_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug(
                "Appointment reminders skipped: another instance holds the lock."
            )
            return 0
        sent = appointments_reminders.send_due(db, moment=moment)
        total = len(sent.day_before) + len(sent.two_hours)
        if total:
            logger.info(
                "Appointment reminders: %d day-before, %d two-hour, %d skipped (checked in).",
                len(sent.day_before),
                len(sent.two_hours),
                len(sent.checked_in),
            )
        return total


def run_chronic_collections(*, moment: datetime | None = None) -> int:
    """Remind every collection that is due, and follow up each missed one once; return how many (Issue 85).

    Elects a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.appointments.chronic.run_due`. Safe to call directly.
    """
    with (
        get_db_context() as db,
        _advisory_lock(db, _CHRONIC_COLLECTIONS_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug(
                "Chronic collection reminders skipped: another instance holds the lock."
            )
            return 0
        done = chronic.run_due(db, moment=moment)
        db.commit()
        total = len(done.reminded) + len(done.followed_up)
        if total:
            logger.info(
                "Chronic collections: %d reminded, %d followed up, %d cycles rolled forward.",
                len(done.reminded),
                len(done.followed_up),
                len(done.rolled),
            )
        return total


def run_appointment_conversion(*, moment: datetime | None = None) -> int:
    """Turn every due booking into a ticket once, and lapse the ones whose day has ended (Issue 81).

    Elects a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.appointments.conversion.convert_due`. Returns how many bookings became tickets. Safe to
    call directly, and twice: a booking already converted is not converted again.
    """
    with (
        get_db_context() as db,
        _advisory_lock(db, _APPOINTMENT_CONVERSION_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug(
                "Appointment conversion skipped: another instance holds the lock."
            )
            return 0
        done = appointments_conversion.convert_due(db, moment=moment)
        if done.converted or done.lapsed:
            logger.info(
                "Appointment conversion: %d converted, %d still waiting, %d lapsed.",
                len(done.converted),
                len(done.waiting),
                len(done.lapsed),
            )
        return len(done.converted)


def run_appointment_slot_generation(*, moment: datetime | None = None) -> int:
    """Roll every listed clinic's appointment slots forward to its horizon once; return how many were made.

    Elects a single runner across instances via the advisory lock, then delegates to
    :func:`src.modules.appointments.service.generate_published`. Idempotent: a second run the same
    night creates nothing. Safe to call directly.
    """
    with (
        get_db_context() as db,
        _advisory_lock(db, _APPOINTMENT_SLOTS_LOCK_KEY) as acquired,
    ):
        if not acquired:
            logger.debug(
                "Appointment slot generation skipped: another instance holds the lock."
            )
            return 0
        created = appointments_service.generate_published(db, moment=moment)
        if created:
            logger.info("Appointment slot generation created %d slot(s).", created)
        return created


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
        run_notification_failure_watch,
        trigger=IntervalTrigger(
            minutes=cfg.notification_failure_watch_minutes, timezone=APP_TIMEZONE
        ),
        id=_NOTIFICATION_FAILURE_WATCH_JOB_ID,
        name="Notification failure watch",
        # A missed run is coalesced: the watch measures the window ending when it runs, and alerts once
        # per window, so a late run alerts no more than an on-time one would.
        misfire_grace_time=cfg.notification_failure_watch_minutes * 60,
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
    scheduler.add_job(
        run_queue_snapshot_reconciliation,
        trigger=IntervalTrigger(
            seconds=cfg.queue_snapshot_reconcile_seconds, timezone=APP_TIMEZONE
        ),
        id=_QUEUE_SNAPSHOT_JOB_ID,
        name="Queue snapshot reconciliation",
        # A missed run is coalesced: the sweep recounts from the source, so one catch-up repairs
        # whatever several missed runs would have.
        misfire_grace_time=cfg.queue_snapshot_reconcile_seconds,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_recall_timer_sweep,
        trigger=IntervalTrigger(
            seconds=cfg.queue_recall_sweep_seconds, timezone=APP_TIMEZONE
        ),
        id=_RECALL_TIMERS_JOB_ID,
        name="Recall timers",
        # A missed run is coalesced: the deadlines are in the database, so one late run recalls
        # everything several missed runs would have, and nothing twice.
        misfire_grace_time=cfg.queue_recall_sweep_seconds,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_visit_note_retention_sweep,
        trigger=CronTrigger(
            hour=cfg.visit_note_retention_sweep_hour, minute=0, timezone=APP_TIMEZONE
        ),
        id=_VISIT_NOTE_RETENTION_JOB_ID,
        name="Nightly visit note retention sweep",
        # A missed run is coalesced: the sweep deletes whatever is past its expiry, so one late run
        # deletes what several missed runs would have, and nothing that is not due.
        misfire_grace_time=3600,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_queue_request_key_sweep,
        trigger=IntervalTrigger(hours=1, timezone=APP_TIMEZONE),
        id=_QUEUE_REQUEST_KEY_JOB_ID,
        name="Queue request key sweep",
        # Keys older than a day are deleted whenever the job runs, so a late run loses nothing.
        misfire_grace_time=3600,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_display_device_watch,
        trigger=IntervalTrigger(minutes=1, timezone=APP_TIMEZONE),
        id=_DISPLAY_DEVICE_WATCH_JOB_ID,
        name="Display device watch",
        # A missed minute is coalesced: the watch compares heartbeats with the time it runs, so one late
        # run alerts about everything several missed runs would have, and each silence once.
        misfire_grace_time=60,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_call_forward_sweep,
        trigger=IntervalTrigger(
            seconds=cfg.virtual_waiting_sweep_seconds, timezone=APP_TIMEZONE
        ),
        id=_CALL_FORWARD_JOB_ID,
        name="Virtual waiting room call-forward",
        # A missed run is coalesced: the rule reads the estimate as it is when the sweep runs, so one late
        # run tells everyone several missed runs would have, and nobody twice.
        misfire_grace_time=cfg.virtual_waiting_sweep_seconds,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_feedback_comment_retention_sweep,
        trigger=CronTrigger(
            hour=cfg.visit_note_retention_sweep_hour, minute=15, timezone=APP_TIMEZONE
        ),
        id=_FEEDBACK_RETENTION_JOB_ID,
        name="Nightly feedback comment retention sweep",
        # The same night as the visit notes (Issue 53), a quarter of an hour later. A missed night is
        # coalesced: the sweep empties whatever is past its expiry, so one late run catches up.
        misfire_grace_time=3600,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_chronic_collections,
        trigger=IntervalTrigger(minutes=15, timezone=APP_TIMEZONE),
        id=_CHRONIC_COLLECTIONS_JOB_ID,
        name="Chronic collection reminders",
        # A collection has a day, not a minute: a missed run is coalesced and catches up, once.
        misfire_grace_time=3600,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_appointment_reminders,
        trigger=IntervalTrigger(minutes=1, timezone=APP_TIMEZONE),
        id=_APPOINTMENT_REMINDERS_JOB_ID,
        name="Appointment reminders",
        # A missed minute is coalesced: each reminder has a window of hours, so one late run sends it, once.
        misfire_grace_time=60,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_appointment_conversion,
        trigger=IntervalTrigger(minutes=1, timezone=APP_TIMEZONE),
        id=_APPOINTMENT_CONVERSION_JOB_ID,
        name="Appointment conversion",
        # A missed minute is coalesced: the sweep converts every booking due by the time it runs, so one late
        # run converts what several missed runs would have, and none twice.
        misfire_grace_time=60,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_appointment_slot_generation,
        trigger=CronTrigger(hour=2, minute=30, timezone=APP_TIMEZONE),
        id=_APPOINTMENT_SLOTS_JOB_ID,
        name="Nightly appointment slot generation",
        # A missed night is coalesced: generation reconciles each clinic's whole horizon, so one late
        # run makes every slot the missed runs would have, and none twice.
        misfire_grace_time=3600,
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
