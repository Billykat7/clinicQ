"""Giving a place back: patient cancellation, and everyone behind moving up (Issue 44).

Making cancellation easy is what keeps a queue honest. A patient who changes their plans and cancels
frees a slot and shortens everyone's wait; one who simply does not come costs the clinic a
consultation and the recall timers (Issue 43) ten minutes of a room.

**One function for every channel.** :func:`cancel_ticket` is called by the web ticket page, the USSD
and WhatsApp adapters (M10) and the front desk, and behaves the same for all of them. The channel is
recorded on the ticket (``cancelled_via``) and named in the audit row, so "cancelled on USSD" is a
fact on the trail rather than a guess.

**A cancellation is a transition.** The status moves through
:func:`~src.modules.queue.lifecycle.transition_ticket` (non-negotiable 2), which locks the row,
audits the move and writes the queue snapshot through.

**Positions are derived, never stored.** Nothing is renumbered and no other ticket's row is written.
A patient's place is the count of waiting tickets ahead of them in call order
(:func:`~src.modules.queue.tickets.waiting_ahead`), so the cancellation itself is the whole of the
"recalculation": the very next read anywhere shows everyone behind one place further forward. One
cancellation cannot leave ten stale rows behind, because there are no rows to go stale. Pushing the
new positions to screens as they happen is the board stream's job (Issue 57). Until that lands, a
patient sees their new place on the next page load, and this module does not pretend otherwise.

**The cancellation window.** A patient may cancel their own ticket while it is **waiting**. Once
they have been called (``called``, ``recalled``, ``in_progress``) a room is being held or used for
them, and a self-cancellation is refused with a sentence telling them to speak to reception
(:class:`CalledTicketSelfCancelError`). The front desk may still cancel a called ticket, as staff.

**The reason is optional** and is one of :class:`~src.commons.enums.CancellationReason`, never free
text, so the no-show analysis (Issue 93) can group by it; :func:`reason_counts` is the query it reads.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.commons.enums import (
    TICKET_ACTIVE_STATUSES,
    ActorKind,
    CancellationReason,
    PatientChannel,
    TicketStatus,
)
from src.commons.exceptions import ConflictError, NotFoundError
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models.ticket import Ticket
from src.modules.queue.lifecycle import Actor, lock_ticket, transition_ticket

#: What a patient is told when they try to cancel after being called.
SPEAK_TO_RECEPTION: Final = (
    "You have already been called, so this ticket can no longer be cancelled from your phone. "
    "Please speak to reception."
)
#: The statuses a patient may cancel from themselves: still in the line, not yet called.
_SELF_CANCELLABLE: Final = frozenset({TicketStatus.WAITING})


class CalledTicketSelfCancelError(ConflictError):
    """A patient tried to cancel a ticket that has already been called: HTTP 409."""

    def __init__(self) -> None:
        super().__init__(SPEAK_TO_RECEPTION, code="ticket.cancel.after_call")


@dataclass(frozen=True, slots=True)
class CancelResult:
    """A cancelled ticket, and how many waiting patients behind it moved one place forward."""

    ticket: Ticket
    moved_up: int


def _behind(db: Session, ticket: Ticket) -> int:
    """How many tickets are still waiting behind ``ticket`` in its queue today: the ones it frees."""
    return int(
        db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.queue_id == ticket.queue_id,
                Ticket.service_day == ticket.service_day,
                Ticket.status == TicketStatus.WAITING.value,
                Ticket.sequence > ticket.sequence,
            )
        ).scalar_one()
    )


def cancel_ticket(
    db: Session,
    ticket_id: str,
    *,
    channel: PatientChannel,
    actor: Actor,
    reason: CancellationReason | None = None,
    moment: datetime | None = None,
) -> CancelResult:
    """Cancel one ticket from any channel. The caller commits.

    The caller has already established that ``actor`` may act on this ticket: the patient owns it,
    or the staff member works at its clinic. A patient (``actor.kind`` is ``PATIENT``) may cancel
    only a waiting ticket; staff may also cancel a called one.

    Args:
        db: The session.
        ticket_id: The ticket.
        channel: Where the cancellation came from; recorded on the ticket and in the audit row.
        actor: The patient, or the staff member at the desk.
        reason: Why, if the patient said.
        moment: When (aware); ``None`` means now in Johannesburg.

    Returns:
        The cancelled ticket and how many patients behind it moved up.

    Raises:
        NotFoundError: No such ticket.
        CalledTicketSelfCancelError: A patient cancelling after being called.
        IllegalTransitionError: The ticket is already finished (a correction is a new ticket).
    """
    ticket = lock_ticket(db, ticket_id)
    current = ticket.status_enum
    # A called patient is refused with a sentence; a finished ticket falls through to the
    # lifecycle's own refusal, because it is not a matter of the window.
    if (
        actor.kind is ActorKind.PATIENT
        and current not in _SELF_CANCELLABLE
        and current in TICKET_ACTIVE_STATUSES
    ):
        raise CalledTicketSelfCancelError
    who = "patient" if actor.kind is ActorKind.PATIENT else "staff"
    note = f"cancelled by {who} via {channel.value}" + (
        f", reason {reason.value}" if reason is not None else ""
    )
    moved = transition_ticket(
        db,
        ticket.id,
        TicketStatus.CANCELLED,
        actor=actor,
        expected_status=current,
        note=note,
        moment=moment,
    )
    moved.cancelled_via = channel.value
    moved.cancellation_reason = reason.value if reason is not None else None
    db.flush()
    return CancelResult(ticket=moved, moved_up=_behind(db, moved))


def cancel_own_ticket(
    db: Session,
    patient_id: str,
    ticket_id: str,
    *,
    channel: PatientChannel,
    reason: CancellationReason | None = None,
    moment: datetime | None = None,
) -> CancelResult:
    """A patient cancels one of their own tickets, from the web, USSD or WhatsApp.

    Another patient's ticket, or one that does not exist, is the same "not found", so a ticket id
    cannot be probed for.

    Raises:
        NotFoundError: Not this patient's ticket.
        CalledTicketSelfCancelError: Already called.
    """
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.patient_id != patient_id:
        raise NotFoundError("No such ticket.", code="http.not_found")
    return cancel_ticket(
        db,
        ticket_id,
        channel=channel,
        actor=Actor(kind=ActorKind.PATIENT, label=f"patient:{patient_id}"),
        reason=reason,
        moment=moment,
    )


def reason_counts(
    db: Session, access: SiteAccess, first_day: date, last_day: date
) -> Mapping[CancellationReason | None, int]:
    """How many tickets this clinic's patients cancelled for each reason, over a range of service days.

    The input to the no-show analysis (Issue 93). ``None`` counts cancellations with no reason given.
    """
    rows = db.execute(
        scoped_select(Ticket, access)
        .with_only_columns(Ticket.cancellation_reason, func.count(Ticket.id))
        .where(
            Ticket.status == TicketStatus.CANCELLED.value,
            Ticket.service_day >= first_day,
            Ticket.service_day <= last_day,
        )
        .group_by(Ticket.cancellation_reason)
    ).all()
    return {
        (CancellationReason(reason) if reason is not None else None): int(count)
        for reason, count in rows
    }
