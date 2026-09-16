"""Request and response models for joining a queue and reading tickets (Issue 40).

A ticket on the wire carries its number and its reference code the way a person reads them
(``A043``, ``K7M-4QP``), its status and source as enums, and nothing a public screen should not
learn from a patient's own answer: the reason for the visit is accepted on the way in and never
returned in a list.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from src.commons.enums import (
    CancellationReason,
    EstimateConfidence,
    PriorityReason,
    TicketPageHeadline,
    TicketSource,
    TicketStatus,
    TransferReason,
)
from src.commons.time import stored_sast
from src.database.models.queue_reorder import MAX_REORDER_NOTE_LENGTH, QueueReorder
from src.database.models.ticket import MAX_REASON_LENGTH, MAX_TRAVEL_MINUTES, Ticket
from src.modules.appointments.feedback_schemas import FeedbackRequestOut
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
    travel_minutes: int | None = Field(default=None, ge=0, le=MAX_TRAVEL_MINUTES)
    """How long the trip to the clinic takes, for a clinic with a virtual waiting room (Issue 86). ``0`` is
    "already here"; ``None`` takes the default. Not kept where the clinic has no virtual waiting room."""
    for_patient_id: str | None = Field(default=None, max_length=36)
    """The dependant this place is for (Issue 84), when one phone acts for a household. Omitted, or
    the caller's own id, means the caller. The ticket belongs to whoever is named here."""
    _trim = field_validator("reason_text")(_blank_to_none)


class WalkInIn(BaseModel):
    """What the front desk records for a walk-in. A phone number is optional: many have none."""

    name: str | None = Field(default=None, max_length=80)
    """What to call the patient by at the desk. Staff-facing: never shown on the public board."""
    reason_text: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)
    comment_consent: bool = False
    phone: str | None = Field(default=None, max_length=20)
    """When given, the walk-in is linked to that patient (and cannot hold two tickets in a queue)."""
    notifications_consent: bool = False
    """The patient's answer, asked at the desk, to being messaged about their turn (Issue 51). Recorded
    as their ``notifications`` consent, so the notifications of M9 can reach them; only with a phone."""

    _trim = field_validator("reason_text", "name", "phone")(_blank_to_none)

    @model_validator(mode="after")
    def _consent_needs_a_number(self) -> WalkInIn:
        """Agreeing to messages means nothing without a number to message."""
        if self.notifications_consent and self.phone is None:
            raise ValueError("notifications_consent needs a phone number to message.")
        return self


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
    travel_minutes: int | None = None
    """The trip the patient stated, at a clinic with a virtual waiting room (Issue 86)."""
    leave_alert_at: datetime | None = None
    """When the patient was told it is time to leave (Issue 86)."""
    on_my_way_at: datetime | None = None
    """When the patient said they are on their way (Issue 86): what reception looks for."""

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
            travel_minutes=ticket.travel_minutes,
            leave_alert_at=stored_sast(ticket.leave_alert_at)
            if ticket.leave_alert_at
            else None,
            on_my_way_at=stored_sast(ticket.on_my_way_at)
            if ticket.on_my_way_at
            else None,
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


class TicketPageClinic(BaseModel):
    """The clinic on a ticket page: where it is and how to reach it (Issue 68)."""

    name: str
    address: str
    phone_e164: str | None
    call_url: str | None
    """``tel:`` link for a tap-to-call button; ``None`` when the clinic has no number."""
    directions_url: str
    """A maps link to the clinic's position."""


class TicketPageQueue(BaseModel):
    """The queue on a ticket page, and the room it is called to."""

    name: str
    room: str | None


class TicketQrOut(BaseModel):
    """A ticket's QR as drawable data (Issue 70): ``<svg viewBox="0 0 {size} {size}"><path d="{path}"/></svg>``.

    Data rather than an image, so the ticket page, its offline copy on the phone and the printed stub draw the
    same QR without a network or a QR library.
    """

    payload: str
    """What the QR says: ``CLINICQ:K7M-4QP``, the reference code behind ClinicQ's prefix."""
    size: int = Field(gt=0)
    """The side of the square, in modules, including the quiet margin."""
    path: str
    """The dark modules as one SVG path, drawn in ``size`` units."""


class CallForwardOut(BaseModel):
    """The virtual waiting room on a ticket page (Issue 86): when to leave, and "On my way"."""

    travel_minutes: int = Field(gt=0)
    """The trip the patient stated (or the default)."""
    leave_at: datetime | None
    """When to set off, from the same estimate as :attr:`TicketPageOut.wait`; ``None`` once called."""
    due: bool
    """Whether it is time to leave now."""
    alerted_at: datetime | None
    """When the patient was sent "time to leave", if they have been."""
    on_my_way_at: datetime | None
    """When the patient said they are on their way, if they have."""
    on_my_way_url: str | None
    """Where the ticket's own patient says so; ``None`` for anyone else, once said, or once the visit began."""


class OnMyWayOut(BaseModel):
    """The ticket after "On my way": reception now sees it."""

    ticket: TicketOut
    message: str


class TicketPageOut(BaseModel):
    """Everything a patient's ticket page shows, derived on every read (Issue 68).

    The page is reached by an unguessable link, so it carries nothing about the patient: no name, no
    phone number, no reason for the visit.
    """

    number: str
    reference_code: str
    """Written the way it is read aloud: ``K7M-4QP``."""
    headline: TicketPageHeadline
    """What the page leads with."""
    status: TicketStatus
    service_day: date
    clinic: TicketPageClinic
    queue: TicketPageQueue
    position: int | None = Field(default=None, ge=1)
    """Place in the waiting line, 1 being next; ``None`` once the ticket is no longer waiting."""
    waiting_ahead: int | None = Field(default=None, ge=0)
    wait: WaitOut | None = None
    """The expected wait from ``as_of``, always a range; ``None`` once the ticket is no longer waiting."""
    called_at: datetime | None = None
    as_of: datetime
    """When this state was read. The page shows how old it is."""
    refresh_seconds: int = Field(gt=0)
    """How often the page reads this again while its live stream is down."""
    stale_after_seconds: int = Field(gt=0)
    """How long without word from the server before the page says it is not live."""
    stream_url: str | None
    """The live stream (``ticket.state`` events); ``None`` once the ticket is finished."""
    cancel_url: str | None
    """Where the ticket's own patient cancels it; ``None`` for anyone else, or once it is not waiting."""
    next_page_url: str | None
    """The page of the ticket a transfer issued, so the family following along follows the visit."""
    push_key: str | None = None
    """The VAPID key to subscribe this browser to web push with (Issue 64). Only for the ticket's own
    signed-in patient, while the ticket is still in its day and web push is configured."""
    push_subscribe_url: str | None = None
    """Where that subscription is sent; set exactly when ``push_key`` is."""
    reference_qr: TicketQrOut
    """The QR a patient shows at reception (Issue 70), encoding :attr:`reference_code`."""
    reference_spoken: str
    """:attr:`reference_code` as read aloud: ``Kilo 7 Mike, 4 Quebec Papa``."""
    offer_install: bool = False
    """Whether to offer adding the app to the home screen (Issue 69): only to the ticket's own signed-in
    patient, who has just joined, and only while the ticket is open. Never on a shared link."""
    preferences_url: str | None = None
    """Where the patient's message preferences are read and changed by this link (Issue 67); ``None`` for a
    walk-in with no patient."""
    call_forward: CallForwardOut | None = None
    """The virtual waiting room (Issue 86), for a travelling patient at a clinic that runs one."""
    feedback: FeedbackRequestOut | None = None
    """The post-visit question (Issue 87), for the ticket's own patient once the visit is done and the
    question was sent: one tap on a score answers it here too."""


class TicketLookupOut(BaseModel):
    """The ticket a scanned or typed code opens at reception (Issue 70)."""

    ticket: TicketOut
    queue_name: str
    room_label: str | None
    waiting_ahead: int | None = Field(default=None, ge=0)
    """How many are ahead while the ticket waits; ``None`` once it has been called."""
    wait: WaitOut | None = None
    reference_spoken: str
    followed_from: str | None = None
    """The number of the ticket whose code this was, when the visit had moved on to this ticket by transfer."""
    message: str
    """A sentence for the desk: ``T004 is waiting in Triage, 3 ahead.``"""


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
    page_url: str | None
    """The ticket page, to follow the ticket live and share with family (Issue 68)."""


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
    page_url: str | None = None
    """The ticket page (Issue 68); ``None`` for a ticket older than the page."""


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
