"""Clinical priority overrides: moving a visibly unwell patient forward, never silently (Issue 46).

"One fair queue" (non-negotiable 1) survives contact with a real clinic only if staff can move a
visibly unwell patient forward in two taps. It stays a clinical judgement rather than a way to skip
the line because every override is attributable and has a reason:

* **No reason, no override.** :func:`override_priority` refuses a missing reason code with
  :class:`PriorityReasonRequiredError` (422) **on the server**, before anything is read or written.
  The request schema requires one too, but a check in a form is not a rule. The reason is a
  :class:`~src.commons.enums.PriorityReason`, never free text; a short optional note may sit beside
  it.
* **Never ahead of somebody already being seen.** A patient is moved ahead of a named ticket
  (``ahead_of``), and that ticket must still be **waiting**. One that is called, recalled or in
  progress has a room held or in use, and an override naming it is refused
  (:class:`AheadOfCalledPatientError`, 409). Nothing can be placed ahead of such a patient,
  because only waiting tickets are in the call order.
* **Two rows, two jobs.** A ``queue_reorder`` row holds the queue-specific detail (who, why, the
  place before and after), and an audit row (Issue 20) is the proof. Both are written in the same
  transaction, or neither is.
* **The order changes, no position is stored.** The ticket's ``order_key`` is set between the two
  tickets it now stands between, and every place in the queue is still counted from that order at
  the next read (Issue 44). Nobody else's row is written.
* **Nothing about priority reaches the public board.** No ticket, board or discovery shape carries a
  reason, a note or a reorder, and ``tests/integration/queue/test_priority_override.py`` walks the
  public API to keep it that way.

:func:`override_counts` is what the reports read (Issue 90). It is deliberately **not a ranking**: it
lists staff members alphabetically with how many overrides each made, and says in its answer that the
counts describe use of a clinical tool, not anybody's performance.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.commons.enums import (
    AuditAction,
    AuditEntityType,
    PriorityReason,
    TicketStatus,
)
from src.commons.exceptions import ConflictError, UnprocessableError
from src.commons.time import business_day_bounds, now_sast
from src.core.audit import record_audit_event
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models.queue import Queue
from src.database.models.queue_reorder import QueueReorder
from src.database.models.ticket import Ticket
from src.modules.queue.lifecycle import Actor, lock_ticket
from src.modules.queue.snapshot import on_queue_changed
from src.modules.queue.tickets import ahead_of, waiting_ahead

#: Said with the counts wherever they are shown, so they are read for what they are.
COUNTS_ARE_NOT_RANKINGS: Final = (
    "How often each staff member used a priority override. Overrides are clinical judgements, and "
    "these counts are not a measure of anybody's performance; they are listed by name, not by count."
)

#: How each reason reads on a staff screen: the words the reason prompt offers and the trail shows
#: (Issue 52). Next to the vocabulary's service so a reason added to the enum without words fails
#: ``tests/unit/queue/test_priority_labels.py`` rather than rendering a wire value.
PRIORITY_REASON_LABELS: Final[dict[PriorityReason, str]] = {
    PriorityReason.VISIBLY_UNWELL: "Visibly unwell",
    PriorityReason.ELDERLY: "Elderly",
    PriorityReason.INFANT: "Infant or small child",
    PriorityReason.PREGNANCY: "Pregnancy",
    PriorityReason.STAFF_REFERRAL: "A clinician asked",
    PriorityReason.OTHER: "Other (say why in the note)",
}

#: What staff are told for each refusal.
REASON_REQUIRED: Final = "A priority override needs a reason."
AHEAD_OF_CALLED: Final = "A patient cannot be moved ahead of someone who has already been called or is being seen."
ONLY_WAITING: Final = "Only a patient who is still waiting can be moved forward."
NOT_FORWARD: Final = "The patient is already ahead of that ticket."
OTHER_QUEUE: Final = "The two tickets are not waiting in the same queue today."


class PriorityReasonRequiredError(UnprocessableError):
    """An override without a reason code: HTTP 422, and nothing is saved."""

    def __init__(self) -> None:
        super().__init__(REASON_REQUIRED, code="ticket.priority.reason_required")


class AheadOfCalledPatientError(ConflictError):
    """An override that would put a patient ahead of one already called or being seen: HTTP 409."""

    def __init__(self) -> None:
        super().__init__(AHEAD_OF_CALLED, code="ticket.priority.ahead_of_called")


class PriorityRefusedError(ConflictError):
    """Any other refused override (not waiting, not forward, another queue): HTTP 409."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message, code=f"ticket.priority.{reason}")


@dataclass(frozen=True, slots=True)
class OverrideResult:
    """The moved ticket, and the record of the move."""

    ticket: Ticket
    reorder: QueueReorder


def _key_ahead_of(db: Session, target: Ticket) -> float:
    """An ``order_key`` just ahead of ``target``: between it and the waiting ticket before it."""
    before = db.execute(
        select(Ticket.order_key)
        .where(
            Ticket.queue_id == target.queue_id,
            Ticket.service_day == target.service_day,
            Ticket.status == TicketStatus.WAITING.value,
            ahead_of(target),
        )
        .order_by(Ticket.order_key.desc(), Ticket.sequence.desc())
        .limit(1)
    ).scalar_one_or_none()
    return (
        (before + target.order_key) / 2
        if before is not None
        else target.order_key - 1.0
    )


def override_priority(
    db: Session,
    ticket_id: str,
    *,
    ahead_of_ticket_id: str,
    reason: PriorityReason | None,
    actor: Actor,
    note: str | None = None,
    moment: datetime | None = None,
) -> OverrideResult:
    """Move a waiting patient forward, to just ahead of another waiting ticket. The caller commits.

    Args:
        db: The session.
        ticket_id: The patient to move; the caller scoped it to its clinic.
        ahead_of_ticket_id: The waiting ticket the patient should now be called before.
        reason: Why. ``None`` is refused, whatever the caller's schema allowed.
        actor: The staff member making the override.
        note: A few optional words beside the reason.
        moment: When (aware); ``None`` means now in Johannesburg.

    Returns:
        The moved ticket and its ``queue_reorder`` row.

    Raises:
        PriorityReasonRequiredError: No reason code (422). Checked before anything else.
        NotFoundError: Either ticket does not exist.
        AheadOfCalledPatientError: The named ticket has been called or is being seen (409).
        PriorityRefusedError: The patient is not waiting, is already ahead, or the two tickets are
            not in the same queue today (409).
    """
    if reason is None:
        raise PriorityReasonRequiredError
    moment = moment or now_sast()
    ticket = lock_ticket(db, ticket_id)
    target = lock_ticket(db, ahead_of_ticket_id)
    if (target.queue_id, target.service_day) != (ticket.queue_id, ticket.service_day):
        raise PriorityRefusedError("other_queue", OTHER_QUEUE)
    if target.status_enum in {
        TicketStatus.CALLED,
        TicketStatus.RECALLED,
        TicketStatus.IN_PROGRESS,
    }:
        raise AheadOfCalledPatientError
    if (
        ticket.status_enum is not TicketStatus.WAITING
        or target.status_enum is not TicketStatus.WAITING
    ):
        raise PriorityRefusedError("not_waiting", ONLY_WAITING)

    before = waiting_ahead(db, ticket) + 1
    target_place = waiting_ahead(db, target) + 1
    if target_place >= before:
        raise PriorityRefusedError("not_forward", NOT_FORWARD)
    ticket.order_key = _key_ahead_of(db, target)
    db.flush()
    after = waiting_ahead(db, ticket) + 1

    reorder = QueueReorder(
        site_id=ticket.site_id,
        queue_id=ticket.queue_id,
        ticket_id=ticket.id,
        staff_user_id=actor.user_id,
        staff=actor.label,
        reason_code=reason.value,
        note=note,
        position_before=before,
        position_after=after,
        created_at=moment,
    )
    db.add(reorder)
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.TICKET,
        entity_id=ticket.id,
        actor=actor.label,
        actor_id=actor.user_id,
        actor_role=actor.kind.value,
        site_id=ticket.site_id,
        # Snapshots rather than a ready diff, so the note is redacted like every free text.
        before={"position": before, "note": None},
        after={"position": after, "note": note},
        context=(
            f"{ticket.number}: priority override, place {before} → {after}, "
            f"reason {reason.value}"
        ),
    )
    db.flush()
    queue = db.get(Queue, ticket.queue_id)
    if queue is not None:
        on_queue_changed(db, queue)
    return OverrideResult(ticket=ticket, reorder=reorder)


def reorder_trail(db: Session, access: SiteAccess, day: date) -> Sequence[QueueReorder]:
    """This clinic's overrides on a service day, newest first: the trail a clinic manager reads."""
    start, end = business_day_bounds(day)
    return (
        db.execute(
            scoped_select(QueueReorder, access)
            .where(QueueReorder.created_at >= start, QueueReorder.created_at < end)
            .order_by(QueueReorder.created_at.desc())
        )
        .scalars()
        .all()
    )


@dataclass(frozen=True, slots=True)
class StaffOverrideCount:
    """How many overrides one staff member made in a period."""

    staff: str
    overrides: int


def override_counts(
    db: Session, access: SiteAccess, first_day: date, last_day: date
) -> list[StaffOverrideCount]:
    """Overrides per staff member at this clinic over a range of service days, **by name**.

    Sorted alphabetically on purpose, never by count: see :data:`COUNTS_ARE_NOT_RANKINGS`.
    """
    start, _ = business_day_bounds(first_day)
    _, end = business_day_bounds(last_day)
    rows = db.execute(
        scoped_select(QueueReorder, access)
        .with_only_columns(QueueReorder.staff, func.count(QueueReorder.id))
        .where(QueueReorder.created_at >= start, QueueReorder.created_at < end)
        .group_by(QueueReorder.staff)
        .order_by(QueueReorder.staff)
    ).all()
    return [
        StaffOverrideCount(staff=staff, overrides=int(count)) for staff, count in rows
    ]
