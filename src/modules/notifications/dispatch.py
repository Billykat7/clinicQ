"""Post-commit delivery of patient notifications: after the queue's commit, and never inside it (Issue 63).

A queue transition must never wait for, or be undone by, a provider. So the service splits a patient
notification in two:

1. **Inside the queue's transaction**, :func:`src.modules.notifications.service.notify` writes the
   ledger row. It is a database write and nothing else, so it commits or rolls back with the move
   that caused it: a call that rolls back leaves no message behind, and a call that commits always
   has its message on record.
2. **After the commit**, :func:`after_commit` hands the row's id to :func:`submit`, which delivers it
   on a worker thread (``NOTIFICATION_DISPATCH=background``, the default) or on the committing thread
   (``inline``, for tests and scripts). A worker opens its own session on the committed session's
   engine and delivers the row through the service's normal path, gate included.

Nothing here can reach back into the transaction that caused it: :func:`publish` is called from the
session's ``after_commit``, and :func:`src.core.domain_events.publish` swallows and logs whatever a
subscriber raises. A worker that never runs (a crash, a full pool) leaves a ``queued`` row, and the
retry sweep delivers it once :attr:`~src.core.config.Settings.notification_dispatch_grace_seconds`
has passed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any

from sqlalchemy import Connection, Engine
from sqlalchemy.orm import Session

from src.commons.enums import NotificationDispatchMode
from src.core.config import get_settings
from src.core.domain_events import DomainEvent, publish_after_commit

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_executor_lock = Lock()
#: A mode forced for the whole process (tests), ahead of ``NOTIFICATION_DISPATCH``.
_forced_mode: NotificationDispatchMode | None = None


@dataclass(frozen=True, slots=True)
class PatientNotificationQueued(DomainEvent):
    """A patient notification row was committed and is ready to deliver.

    ``bind`` is the engine the row was committed through, so the delivery reads the same database
    whether it runs in the application, a scheduler job or a test's in-memory engine.
    """

    notification_id: str
    bind: Engine | Connection


def after_commit(db: Session, notification_id: str) -> None:
    """Deliver ``notification_id`` once ``db``'s transaction commits; forget it if it rolls back."""
    publish_after_commit(
        db,
        PatientNotificationQueued(notification_id=notification_id, bind=db.get_bind()),
    )


def mode() -> NotificationDispatchMode:
    """The dispatch mode in force: a forced one, else ``NOTIFICATION_DISPATCH``."""
    return _forced_mode or get_settings().notification_dispatch


def _pool() -> ThreadPoolExecutor:
    """The process's delivery pool, created on first use."""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=get_settings().notification_dispatch_workers,
                thread_name_prefix="clinicq-notify",
            )
        return _executor


def _run_logged(job: Callable[[], Any]) -> None:
    """Run one delivery job, logging rather than raising: nothing is waiting for its answer."""
    try:
        job()
    except Exception:
        logger.exception(
            "Post-commit notification delivery failed; the retry sweep has it."
        )


def submit(job: Callable[[], Any]) -> None:
    """Run ``job`` now (inline) or on the delivery pool (background). Never raises."""
    if mode() is NotificationDispatchMode.INLINE:
        _run_logged(job)
        return
    try:
        _pool().submit(_run_logged, job)
    except RuntimeError:
        # The pool is shutting down (the process is exiting): the row stays queued for the sweep.
        logger.warning("Notification pool unavailable; the retry sweep will deliver.")


@contextmanager
def forced(chosen: NotificationDispatchMode) -> Iterator[None]:
    """Force a dispatch mode for the whole process until the block ends. For tests."""
    global _forced_mode
    previous, _forced_mode = _forced_mode, chosen
    try:
        yield
    finally:
        _forced_mode = previous


def drain() -> None:
    """Wait for every background delivery submitted so far to finish. For tests and shutdown."""
    global _executor
    with _executor_lock:
        pool, _executor = _executor, None
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=False)
