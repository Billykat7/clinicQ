"""The front desk's own walk-ins: the recent ones, and undoing the last (Issue 51).

A walk-in is issued by the join service like every other ticket (Issue 40), so there is no walk-in path
to maintain here. What the desk needs on top is a short memory of **what this person just did**:

* :func:`recent_walk_ins` reads the walk-ins the signed-in person issued today, newest first. Who issued
  a ticket is not a column: it is the join's audit row (``create`` on the ticket, with the actor's id),
  which is where that fact already lives, so it is read from there rather than stored twice.
* :func:`undo_walk_in` takes back **the most recent** of them, while the patient is most likely still at
  the counter: only that one, only while it is still waiting, and only within
  ``QUEUE_WALK_IN_UNDO_SECONDS`` of issuing it. The ticket is cancelled through the lifecycle (Issue 44)
  with the reason ``joined_by_mistake`` and the words ``undo walk-in`` on its audit row, so the number is
  never reused and the trail says exactly what happened. Anything older is an ordinary cancellation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy.orm import Session

from src.commons.enums import (
    TICKET_ACTIVE_STATUSES,
    AuditAction,
    AuditEntityType,
    CancellationReason,
    PatientChannel,
    TicketSource,
    TicketStatus,
)
from src.commons.exceptions import ConflictError
from src.commons.time import business_date, now_sast, stored_sast
from src.core.config import get_settings
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models import AuditEvent, Ticket
from src.modules.queue.cancellation import CancelResult, cancel_ticket
from src.modules.queue.lifecycle import Actor, StaleTransitionError, lock_ticket

WALK_IN_UNDO_NOT_LATEST_CODE: Final = "ticket.walk_in_undo.not_latest"
WALK_IN_UNDO_WINDOW_CLOSED_CODE: Final = "ticket.walk_in_undo.window_closed"
#: How many of the person's walk-ins the desk lists.
RECENT_LIMIT: Final = 8


class WalkInUndoNotLatestError(ConflictError):
    """Only the last walk-in this person issued can be undone: HTTP 409."""

    def __init__(self) -> None:
        super().__init__(
            "Only the last walk-in you issued can be undone. Cancel this ticket instead.",
            code=WALK_IN_UNDO_NOT_LATEST_CODE,
        )


class WalkInUndoWindowClosedError(ConflictError):
    """The walk-in is older than the undo window: HTTP 409."""

    def __init__(self, number: str, seconds: int) -> None:
        super().__init__(
            f"{number} was issued more than {seconds} seconds ago, so it can no longer be undone. "
            "Cancel the ticket instead.",
            code=WALK_IN_UNDO_WINDOW_CLOSED_CODE,
        )


@dataclass(frozen=True, slots=True)
class RecentWalkIn:
    """One walk-in this person issued today, and until when it can be undone (``None``: it cannot)."""

    ticket: Ticket
    undo_until: datetime | None

    @property
    def issued_at(self) -> datetime:
        """When the ticket was issued, in Johannesburg time (a stored value read back correctly)."""
        return stored_sast(self.ticket.joined_at)

    @property
    def still_in_the_day(self) -> bool:
        """Whether the ticket is still in the queue today (waiting, called or being seen)."""
        return self.ticket.status_enum in TICKET_ACTIVE_STATUSES


def recent_walk_ins(
    db: Session,
    access: SiteAccess,
    *,
    moment: datetime | None = None,
    limit: int = RECENT_LIMIT,
) -> tuple[RecentWalkIn, ...]:
    """The walk-ins the caller issued today at this clinic, newest first, the latest with its undo window."""
    now = moment or now_sast()
    issued = (
        scoped_select(AuditEvent, access)
        .with_only_columns(AuditEvent.entity_id)
        .where(
            AuditEvent.action == AuditAction.CREATE.value,
            AuditEvent.entity_type == AuditEntityType.TICKET.value,
            AuditEvent.actor_id == str(access.user.id),
        )
    )
    tickets = (
        db.execute(
            scoped_select(Ticket, access)
            .where(
                Ticket.id.in_(issued),
                Ticket.source == TicketSource.WALK_IN.value,
                Ticket.service_day == business_date(now),
            )
            .order_by(Ticket.joined_at.desc(), Ticket.sequence.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    window = timedelta(seconds=get_settings().queue_walk_in_undo_seconds)
    recent = []
    for index, ticket in enumerate(tickets):
        ends = stored_sast(ticket.joined_at) + window
        undoable = (
            index == 0 and ticket.status_enum is TicketStatus.WAITING and now < ends
        )
        recent.append(
            RecentWalkIn(ticket=ticket, undo_until=ends if undoable else None)
        )
    return tuple(recent)


def undo_walk_in(
    db: Session,
    access: SiteAccess,
    ticket_id: str,
    *,
    actor: Actor,
    moment: datetime | None = None,
) -> CancelResult:
    """Take back the caller's last walk-in, within its window. The caller commits.

    Raises:
        WalkInUndoNotLatestError: The ticket is not the last walk-in the caller issued today here.
        StaleTransitionError: It is no longer waiting (already called, cancelled…).
        WalkInUndoWindowClosedError: It was issued longer ago than ``QUEUE_WALK_IN_UNDO_SECONDS``.
    """
    now = moment or now_sast()
    latest = recent_walk_ins(db, access, moment=now, limit=1)
    if not latest or latest[0].ticket.id != ticket_id:
        raise WalkInUndoNotLatestError
    ticket = lock_ticket(db, ticket_id)
    if ticket.status_enum is not TicketStatus.WAITING:
        raise StaleTransitionError(ticket.status_enum, TicketStatus.WAITING)
    seconds = get_settings().queue_walk_in_undo_seconds
    if now >= stored_sast(ticket.joined_at) + timedelta(seconds=seconds):
        raise WalkInUndoWindowClosedError(ticket.number, seconds)
    return cancel_ticket(
        db,
        ticket.id,
        channel=PatientChannel.WALK_IN,
        actor=actor,
        reason=CancellationReason.JOINED_BY_MISTAKE,
        why="undo walk-in",
        moment=now,
    )
