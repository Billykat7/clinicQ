"""Moving a patient to the next queue of their visit without rejoining (Issue 45).

A visit is a journey, not a line: triage, then the doctor, then the pharmacy. Making the patient
rejoin at the back of each line is the difference between a system a nurse uses and one they route
around. :func:`transfer_ticket` is that move.

* The ticket in the queue the patient is leaving becomes ``transferred`` through
  :func:`~src.modules.queue.lifecycle.transition_ticket` (non-negotiable 2), from ``waiting`` (the
  wrong line) or ``in_progress`` (seen, and on to the next step).
* A **new ticket** is issued in the target queue with that queue's next number, from the same
  sequence as every join (:func:`~src.modules.queue.sequence.issue_ticket`), carrying the same
  ``visit_id`` and pointing back with ``transferred_from_id``. The patient does not rejoin: no join
  gate, no rate limit, no duplicate-join answer. It is the clinic moving them.
* **Where they land** is the clinic's :class:`~src.commons.enums.TransferPlacement`. The default,
  ``arrival_order``, places them among the patients waiting there by when each **visit** began, so a
  patient triaged at 07:40 goes ahead of one who walked into the doctor's queue at 08:10. That is
  non-negotiable 1's "fair by arrival order" applied to the whole visit: being moved on should not
  cost a patient the time they have already spent at the clinic. ``back_of_line`` puts them behind
  everyone, as if they had just joined, for a clinic that prefers the simpler rule. The place is an
  ``order_key`` between two neighbours; nobody else's row is rewritten.
* **Refused with a clear message**, and nothing changes (the whole move is one savepoint): a target
  queue that is not taking patients, one that is full for the day, the same queue, or a target the
  patient already holds a ticket in.
* **Audited** twice, once per ticket: the move out (``transferred``, with the target and the reason) and
  the ticket in (``create``, with where it came from), both attributed to the staff member.
* **The patient is told** the new queue, number and expected wait, through the notification service
  (ledger, consent, provider), like every patient message.

The visit keeps its history, so total visit time is derivable (:func:`visit_summary`): its legs are
its tickets in issue order, it began with the first and ended when the last reached a terminal status
other than ``transferred``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.commons.enums import (
    TICKET_TERMINAL_STATUSES,
    AuditAction,
    AuditEntityType,
    NotificationTemplate,
    TicketStatus,
    TransferPlacement,
    TransferReason,
)
from src.commons.exceptions import ConflictError, NotFoundError
from src.commons.time import business_date, now_sast, stored_sast
from src.core.audit import record_audit_event
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models.patient import Patient
from src.database.models.queue import Queue
from src.database.models.site import Site
from src.database.models.ticket import Ticket
from src.database.models.visit import Visit
from src.modules.notifications import service as notifications
from src.modules.queue.estimate import WaitEstimate
from src.modules.queue.lifecycle import Actor, lock_ticket, transition_ticket
from src.modules.queue.sequence import is_second_active_ticket, issue_ticket
from src.modules.queue.snapshot import on_queue_changed
from src.modules.queue.tickets import CALL_ORDER, waiting_ahead
from src.modules.queue.waits import estimates_for
from src.modules.queues.service import QUEUE_CLOSED

#: What staff are told when the target is the queue the patient is already in.
SAME_QUEUE: Final = "The patient is already in that queue."
#: What staff are told when the target queue has issued its capacity for the day.
TARGET_FULL: Final = "That queue is full for today. Choose another queue."
#: What staff are told when the patient already holds a ticket in the target queue.
ALREADY_THERE: Final = "The patient already holds a ticket in that queue."


class TransferRefusedError(ConflictError):
    """The transfer was refused and nothing changed: HTTP 409 with ``ticket.transfer.<reason>``."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message, code=f"ticket.transfer.{reason}")


class _RefuseError(Exception):
    """Raised inside the transfer's savepoint so leaving it undoes the whole move."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class TransferResult:
    """The ticket left behind, the ticket in the next queue, and where the patient stands there."""

    from_ticket: Ticket
    ticket: Ticket
    waiting_ahead: int
    wait: WaitEstimate


def _placement_key(
    db: Session,
    target: Queue,
    visit: Visit,
    placement: TransferPlacement,
    moment: datetime,
) -> float | None:
    """The ``order_key`` that puts this visit in its place in ``target``, or ``None`` for the back.

    Arrival order: before the first waiting ticket whose own visit began later than this one, and
    after the waiting ticket before that. ``None`` (the back of the line, by the new sequence) when
    no waiting visit began later, or when the clinic places transfers at the back.
    """
    if placement is TransferPlacement.BACK_OF_LINE:
        return None
    waiting = db.execute(
        select(Ticket.order_key, Visit.started_at)
        .join(Visit, Visit.id == Ticket.visit_id)
        .where(
            Ticket.queue_id == target.id,
            Ticket.service_day == business_date(moment),
            Ticket.status == TicketStatus.WAITING.value,
        )
        .order_by(*CALL_ORDER)
    ).all()
    started = stored_sast(visit.started_at)
    previous: float | None = None
    for key, other_started in waiting:
        if stored_sast(other_started) > started:
            return (previous + key) / 2 if previous is not None else key - 1.0
        previous = key
    return None


def _issued_today(db: Session, queue: Queue, moment: datetime) -> int:
    """Today's tickets in ``queue`` that hold a place, counted under its counter lock (Issue 40)."""
    return int(
        db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.queue_id == queue.id,
                Ticket.service_day == business_date(moment),
                Ticket.status != TicketStatus.CANCELLED.value,
            )
        ).scalar_one()
    )


def _notify(db: Session, ticket: Ticket, target: Queue, wait: WaitEstimate) -> None:
    """Tell the patient their new queue, number and wait, through the notification service."""
    if ticket.patient_id is None:
        return
    patient = db.get(Patient, ticket.patient_id)
    site = db.get(Site, ticket.site_id)
    if patient is None or site is None:
        return
    notifications.send_sms(
        db,
        to=patient.phone_e164,
        template=NotificationTemplate.TICKET_TRANSFERRED,
        context={
            "clinic": site.name,
            "queue": target.name,
            "number": ticket.number,
            "wait": wait.label,
        },
    )


def transfer_ticket(
    db: Session,
    ticket_id: str,
    target: Queue,
    *,
    actor: Actor,
    reason: TransferReason,
    moment: datetime | None = None,
) -> TransferResult:
    """Move a patient from their current queue to ``target``, keeping the visit. The caller commits.

    Args:
        db: The session.
        ticket_id: The ticket in the queue the patient is leaving; the caller scoped it to its clinic.
        target: The queue to move them to, at the same clinic (from the site guard).
        actor: The staff member making the move.
        reason: Why, from a closed list.
        moment: When (aware); ``None`` means now in Johannesburg.

    Returns:
        Both tickets, and the patient's place and wait in the new queue.

    Raises:
        NotFoundError: No such ticket, or the target is at another clinic.
        TransferRefusedError: The target is closed, full, the same queue, or already holds the
            patient. Nothing is changed.
        IllegalTransitionError: The ticket cannot be transferred from its status (only ``waiting``
            and ``in_progress`` can).
    """
    moment = moment or now_sast()
    source = lock_ticket(db, ticket_id)
    if target.site_id != source.site_id:
        raise NotFoundError("No such queue.", code="http.not_found")
    source_queue = db.get(Queue, source.queue_id)
    visit = db.get(Visit, source.visit_id)
    site = db.get(Site, source.site_id)
    assert source_queue is not None and visit is not None and site is not None
    current = source.status_enum
    try:
        with db.begin_nested():
            if target.id == source.queue_id:
                raise _RefuseError("same_queue", SAME_QUEUE)
            if not target.is_active or target.is_deleted:
                raise _RefuseError("queue_closed", QUEUE_CLOSED)
            moved_out = transition_ticket(
                db,
                source.id,
                TicketStatus.TRANSFERRED,
                actor=actor,
                expected_status=current,
                note=f"transferred to {target.name}, reason {reason.value}",
                moment=moment,
            )
            try:
                ticket = issue_ticket(
                    db,
                    queue=target,
                    source=moved_out.source_enum,
                    patient_id=moved_out.patient_id,
                    walk_in_name=moved_out.walk_in_name,
                    reason_text=moved_out.reason_text,
                    comment_consent=moved_out.comment_consent,
                    moment=moment,
                    visit_id=visit.id,
                    transferred_from_id=moved_out.id,
                    order_key=_placement_key(
                        db,
                        target,
                        visit,
                        TransferPlacement(site.transfer_placement),
                        moment,
                    ),
                )
            except IntegrityError as exc:
                if not is_second_active_ticket(exc):
                    raise
                raise _RefuseError("already_there", ALREADY_THERE) from None
            capacity = target.max_daily_capacity
            if capacity is not None and _issued_today(db, target, moment) > capacity:
                raise _RefuseError("queue_full", TARGET_FULL)
            record_audit_event(
                db,
                action=AuditAction.CREATE,
                entity_type=AuditEntityType.TICKET,
                entity_id=ticket.id,
                actor=actor.label,
                actor_id=actor.user_id,
                actor_role=actor.kind.value,
                site_id=ticket.site_id,
                context=(
                    f"{ticket.number} in {target.name}: transferred from {moved_out.number} in "
                    f"{source_queue.name}, reason {reason.value}"
                ),
            )
    except _RefuseError as refused:
        raise TransferRefusedError(refused.reason, refused.message) from None
    db.flush()
    on_queue_changed(db, target)
    ahead = waiting_ahead(db, ticket)
    wait = estimates_for(db, [target], {target.id: ahead}, moment=moment)[target.id]
    _notify(db, ticket, target, wait)
    return TransferResult(
        from_ticket=moved_out, ticket=ticket, waiting_ahead=ahead, wait=wait
    )


@dataclass(frozen=True, slots=True)
class VisitSummary:
    """One visit's legs in order, and how long the whole journey took (when it has ended)."""

    visit: Visit
    legs: Sequence[Ticket]
    started_at: datetime
    ended_at: datetime | None
    total_minutes: float | None


def visit_summary(db: Session, access: SiteAccess, visit_id: str) -> VisitSummary:
    """A visit at this clinic, derived from its tickets. ``ended_at`` is ``None`` while it is under way.

    Raises:
        NotFoundError: No such visit at this clinic (the same answer as none at all).
    """
    visit = db.execute(
        scoped_select(Visit, access).where(Visit.id == visit_id)
    ).scalar_one_or_none()
    if visit is None:
        raise NotFoundError("Not found.", code="http.not_found")
    legs = (
        db.execute(
            scoped_select(Ticket, access)
            .where(Ticket.visit_id == visit.id)
            .order_by(Ticket.joined_at, Ticket.sequence)
        )
        .scalars()
        .all()
    )
    last = legs[-1] if legs else None
    ended = (
        last is not None
        and last.status_enum in TICKET_TERMINAL_STATUSES
        and last.status_enum is not TicketStatus.TRANSFERRED
        and last.completed_at is not None
    )
    started_at = stored_sast(visit.started_at)
    ended_at = (
        stored_sast(last.completed_at) if ended and last and last.completed_at else None
    )
    total = (
        (ended_at - started_at).total_seconds() / 60 if ended_at is not None else None
    )
    return VisitSummary(
        visit=visit,
        legs=legs,
        started_at=started_at,
        ended_at=ended_at,
        total_minutes=total,
    )
