"""Request and response models for joining a queue and reading tickets (Issue 40).

A ticket on the wire carries its number and its reference code the way a person reads them
(``A043``, ``K7M-4QP``), its status and source as enums, and nothing a public screen should not
learn from a patient's own answer: the reason for the visit is accepted on the way in and never
returned in a list.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from src.commons.enums import (
    CancellationReason,
    EstimateConfidence,
    PriorityReason,
    TicketSource,
    TicketStatus,
    TransferReason,
)
from src.commons.time import stored_sast
from src.database.models.queue_reorder import MAX_REORDER_NOTE_LENGTH, QueueReorder
from src.database.models.ticket import MAX_REASON_LENGTH, Ticket
from src.modules.queue.estimate import WaitEstimate
from src.modules.queue.sequence import format_reference_code


def _blank_to_none(value: str | None) -> str | None:
    """Trim a free-text field; an empty answer is no answer."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


class JoinIn(BaseModel):
    """What a patient sends to join a queue: everything optional but the queue in the path."""

    reason_text: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)
    """A short reason for the visit, if they want to give one."""
    comment_consent: bool = False
    """Consent, for this visit only, to show the reason on the waiting-room board. Off by default."""
    _trim = field_validator("reason_text")(_blank_to_none)


class WalkInIn(BaseModel):
    """What the front desk records for a walk-in. A phone number is optional: many have none."""

    name: str | None = Field(default=None, max_length=80)
    """What to call the patient by at the desk. Staff-facing: never shown on the public board."""
    reason_text: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)
    comment_consent: bool = False
    phone: str | None = Field(default=None, max_length=20)
    """When given, the walk-in is linked to that patient (and cannot hold two tickets in a queue)."""

    _trim = field_validator("reason_text", "name", "phone")(_blank_to_none)


class TransitionIn(BaseModel):
    """A request to move a ticket: where to, and what the caller saw it as."""

    to: TicketStatus
    expected_status: TicketStatus | None = None
    """The status on the caller's screen. When the ticket has moved on since, the move is a 409."""


class TicketOut(BaseModel):
    """One ticket as the API returns it."""

    id: str
    site_id: str
    queue_id: str
    number: str
    reference_code: str
    """Written the way it is read aloud: ``K7M-4QP``."""
    source: TicketSource
    status: TicketStatus
    service_day: date
    joined_at: datetime
    walk_in_name: str | None
    """The desk's name for a walk-in; ``None`` for a phone join, whose name is on the patient."""

    @classmethod
    def of(cls, ticket: Ticket) -> TicketOut:
        """The wire form of a ticket row."""
        return cls(
            id=ticket.id,
            site_id=ticket.site_id,
            queue_id=ticket.queue_id,
            number=ticket.number,
            reference_code=format_reference_code(ticket.reference_code),
            source=ticket.source_enum,
            status=ticket.status_enum,
            service_day=ticket.service_day,
            # Read back through stored_sast: SQLite hands a stored datetime back without its offset.
            joined_at=stored_sast(ticket.joined_at),
            walk_in_name=ticket.walk_in_name,
        )


class WaitOut(BaseModel):
    """How long to expect to wait: always a range, with how far to trust it (Issue 42)."""

    low_minutes: int = Field(ge=0)
    high_minutes: int = Field(gt=0)
    confidence: EstimateConfidence
    approximate: bool
    """True when built from the queue's expected minutes rather than its recent visits."""
    label: str
    """The range as a surface shows it: ``~15–25 min``, or ``~15–25 min (approximate)``."""

    @classmethod
    def of(cls, estimate: WaitEstimate) -> WaitOut:
        """The wire form of an estimate."""
        return cls(
            low_minutes=estimate.wait.low_minutes,
            high_minutes=estimate.wait.high_minutes,
            confidence=estimate.confidence,
            approximate=estimate.approximate,
            label=estimate.label,
        )


class JoinOut(BaseModel):
    """The answer to a join: the ticket, whether it is new, and where it stands."""

    ticket: TicketOut
    created: bool
    """``False`` when the patient already held this ticket and was given it back."""
    waiting_ahead: int = Field(ge=0)
    wait: WaitOut
    """The expected wait from now, for this ticket."""
    message: str
    """A sentence for the patient: "You are A043." or "You already hold A041."."""


class CancelIn(BaseModel):
    """A cancellation. The reason is optional, and one of a closed list when given."""

    reason: CancellationReason | None = None


class CancelOut(BaseModel):
    """The cancelled ticket, and what it did for the people behind."""

    ticket: TicketOut
    moved_up: int = Field(ge=0)
    """How many patients still waiting behind this ticket each moved one place forward."""
    message: str


class MyTicketOut(TicketOut):
    """A patient's own ticket, with where it stands: derived on every read, never stored (Issue 44)."""

    waiting_ahead: int | None = Field(default=None, ge=0)
    """Waiting tickets ahead in call order; ``None`` once the ticket is no longer waiting."""
    wait: WaitOut | None = None
    """The expected wait from now; ``None`` once the ticket is no longer waiting."""


class PriorityIn(BaseModel):
    """A priority override: ahead of which waiting ticket, why (required) and an optional note."""

    ahead_of_ticket_id: str
    reason: PriorityReason
    """Required. The server also refuses an override without one."""
    note: str | None = Field(default=None, max_length=MAX_REORDER_NOTE_LENGTH)

    _trim = field_validator("note")(_blank_to_none)


class ReorderOut(BaseModel):
    """One override on the trail: which ticket, who, why, and the place before and after."""

    id: str
    ticket_id: str
    queue_id: str
    staff: str
    reason: PriorityReason
    note: str | None
    position_before: int = Field(ge=1)
    position_after: int = Field(ge=1)
    created_at: datetime

    @classmethod
    def of(cls, row: QueueReorder) -> ReorderOut:
        """The wire form of a ``queue_reorder`` row."""
        return cls(
            id=row.id,
            ticket_id=row.ticket_id,
            queue_id=row.queue_id,
            staff=row.staff,
            reason=PriorityReason(row.reason_code),
            note=row.note,
            position_before=row.position_before,
            position_after=row.position_after,
            created_at=stored_sast(row.created_at),
        )


class PriorityOut(BaseModel):
    """The moved ticket, its new place, and the record of the move."""

    ticket: TicketOut
    reorder: ReorderOut
    waiting_ahead: int = Field(ge=0)


class ReorderTrailOut(BaseModel):
    """A clinic's priority overrides on a service day, newest first."""

    site_id: str
    service_day: date
    total: int = Field(ge=0)
    items: list[ReorderOut]


class StaffOverrideCountOut(BaseModel):
    """How many overrides one staff member made. Not a score."""

    staff: str
    overrides: int = Field(ge=0)


class OverrideCountsOut(BaseModel):
    """Override counts per staff member over a period, listed by name, never ranked (Issue 46)."""

    site_id: str
    start: date
    end: date
    items: list[StaffOverrideCountOut]
    note: str
    """What the counts are, and what they are not."""


class TransferIn(BaseModel):
    """Where to move the patient, and why (a closed list, never free text)."""

    queue_id: str
    reason: TransferReason


class TransferOut(BaseModel):
    """The ticket left behind, the new ticket, and where the patient stands in the new queue."""

    from_ticket: TicketOut
    ticket: TicketOut
    visit_id: str
    waiting_ahead: int = Field(ge=0)
    wait: WaitOut
    message: str


class VisitLegOut(BaseModel):
    """One ticket of a visit: which queue, which number, and when each step happened."""

    ticket_id: str
    queue_id: str
    number: str
    status: TicketStatus
    joined_at: datetime
    called_at: datetime | None
    completed_at: datetime | None
    transferred_from_id: str | None


class VisitOut(BaseModel):
    """One patient's journey through the clinic, derived from its tickets (Issue 45)."""

    id: str
    site_id: str
    started_at: datetime
    ended_at: datetime | None
    """``None`` while the visit is under way."""
    total_minutes: float | None = Field(default=None, ge=0)
    """Start to end, once it has ended."""
    legs: list[VisitLegOut]


class VisitListOut(BaseModel):
    """The clinic's visits that began on a service day, earliest first."""

    site_id: str
    service_day: date
    total: int = Field(ge=0)
    items: list[VisitOut]


class TicketListOut(BaseModel):
    """Tickets still in the day at a clinic, in sequence order within each queue."""

    site_id: str
    service_day: date
    total: int = Field(ge=0)
    items: list[TicketOut]
