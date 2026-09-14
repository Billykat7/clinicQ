"""One staff action on the queue is done once, however often it is sent (Issue 50).

The dashboard sends an ``Idempotency-Key`` header with *Call next*, a status change and an undone call.
:func:`run_once` is how a route uses it:

* **no key**: the action runs, as it always did (an API client that does not send one is unaffected);
* **a new key**: the key is recorded, the action runs, and both commit together;
* **a key already recorded** for the same person, operation and target: nothing runs, and the ticket
  the first request moved is returned, so a double tap or a retried request gets the first answer;
* **a key already used for something else** (another queue, another status): refused with
  ``409 ticket.request.key_reused``, because answering it with an unrelated ticket would be a lie.

The key's row is inserted **before** the action, so two requests with one key at the same instant
cannot both act: on PostgreSQL the second insert waits on ``uq_queue_request_key_user_key`` until the
first transaction ends, then fails and replays the committed answer. When the first request failed
(nobody waiting, a stale screen), its row rolled back with it, and the second is a real attempt.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.commons.exceptions import ConflictError
from src.commons.time import now_sast
from src.core.site_scope import SiteAccess, get_in_site_or_404, scoped_select
from src.database.models import QueueRequestKey, Ticket

#: The header the dashboard sends: a fresh random value per intended action, repeated on a retry.
REQUEST_KEY_HEADER: Final = "Idempotency-Key"
#: What a key may look like: a UUID, or any 8–64 URL-safe characters.
REQUEST_KEY_PATTERN: Final = r"^[A-Za-z0-9_-]{8,64}$"
REQUEST_KEY_REUSED_CODE: Final = "ticket.request.key_reused"
#: How long a key is remembered: far longer than any retry, short enough to keep the table small.
REQUEST_KEY_TTL: Final = timedelta(days=1)


class RequestKeyReusedError(ConflictError):
    """The key was already used by this person for a different action: HTTP 409."""

    def __init__(self) -> None:
        super().__init__(
            "This request key was already used for a different action. Send a new key.",
            code=REQUEST_KEY_REUSED_CODE,
        )


def _recorded(db: Session, access: SiteAccess, key: str) -> QueueRequestKey | None:
    """The caller's row for ``key`` at this clinic, read through the site guard."""
    return db.execute(
        scoped_select(QueueRequestKey, access).where(
            QueueRequestKey.user_id == str(access.user.id), QueueRequestKey.key == key
        )
    ).scalar_one_or_none()


def _replay(
    db: Session, access: SiteAccess, row: QueueRequestKey, operation: str, target: str
) -> Ticket:
    """The first request's ticket, as it is now, if ``row`` recorded this same request."""
    if row.operation != operation or row.target != target or row.ticket_id is None:
        raise RequestKeyReusedError
    return get_in_site_or_404(db, Ticket, row.ticket_id, access)


def run_once(
    db: Session,
    access: SiteAccess,
    key: str | None,
    *,
    operation: str,
    target: str,
    act: Callable[[], Ticket],
) -> Ticket:
    """Run ``act`` and commit, unless ``key`` shows this request was already done. Returns the ticket.

    Args:
        db: The request's session.
        access: The caller at the clinic, from the site guard.
        key: The ``Idempotency-Key`` header, or ``None``.
        operation: What is asked (``call_next``, ``transition``, ``undo_call``).
        target: What it is asked of, including anything that makes it a different request (the
            queue id; the ticket id and the requested status).
        act: The action; it moves one ticket and returns it. It does not commit.

    Raises:
        RequestKeyReusedError: ``key`` was used for a different request, here or at another clinic.
        Whatever ``act`` raises, after which nothing (the key included) is kept.
    """
    if key is None:
        ticket = act()
        db.commit()
        return ticket
    recorded = _recorded(db, access, key)
    if recorded is not None:
        return _replay(db, access, recorded, operation, target)
    row = QueueRequestKey(
        user_id=str(access.user.id),
        key=key,
        site_id=access.site_id,
        operation=operation,
        target=target,
        created_at=now_sast(),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # The same key from a request that got here first and has now committed: answer as it did.
        # Not found at this clinic, it is the caller's key from another clinic: a different request.
        db.rollback()
        recorded = _recorded(db, access, key)
        if recorded is None:
            raise RequestKeyReusedError from None
        return _replay(db, access, recorded, operation, target)
    ticket = act()
    row.ticket_id = ticket.id
    db.commit()
    return ticket


def purge_request_keys(db: Session, *, moment: datetime | None = None) -> int:
    """Delete keys older than :data:`REQUEST_KEY_TTL` and commit; return how many."""
    cutoff = (moment or now_sast()) - REQUEST_KEY_TTL
    result = db.execute(
        delete(QueueRequestKey).where(QueueRequestKey.created_at < cutoff)
    )
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)
