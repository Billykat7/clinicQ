"""Checking in at the tablet by the door: "I am here" without queueing to say it (Issue 83).

The 07:30 bottleneck at a clinic is not the nurses, it is the line at reception where everybody who has
already got a place still has to say they have arrived. A tablet at the door takes that sentence:

* a patient **scans the QR** on their ticket page (Issue 70) or on their booking, or types the six-character
  reference, or their **phone number** on a digits-only keypad;
* a **ticket** they already hold is stamped arrived, and the front desk sees it;
* a **booking** that has not become a ticket yet is converted **now**, through Issue 81's one conversion
  path, so the patient takes the next number in the same sequence as everyone else;
* where the clinic allows it, a patient with neither may **start a walk-in** at the door.

**What checking in does not do.** It changes no status and moves nobody up the line: a ticket is a place
whether or not anyone has arrived (Issue 81's late rule), so ``arrived_at`` is a fact for the front desk,
not a claim on the room. It is stamped once; a second scan says the same thing back.

**What the tablet may see.** The device is paired to one clinic (Issue 61) and is not signed in, so every
read here is narrowed to that clinic's own id, and a code from another clinic is the same "not found" as a
code that does not exist. Nothing here lists tickets, and nothing answers with a name: the tablet is shown
a number, a queue, a room and how many are ahead, which is what the waiting-room board shows anyway.

**Too early.** A booking is checked in from :data:`EARLY_MINUTES` before its time (or from the clinic's
own conversion lead time, whichever is earlier, because by then it is already a ticket). A patient who
arrives before that is told when to come back, rather than being given a place hours early.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import (
    TICKET_ACTIVE_STATUSES,
    AppointmentStatus,
    AuditAction,
    AuditEntityType,
    ConsentPurpose,
    PatientChannel,
    TicketSource,
    TicketStatus,
)
from src.commons.exceptions import ConflictError, NotFoundError, UnprocessableError
from src.commons.phone import InvalidPhoneNumberError, normalize_phone
from src.commons.time import business_date, now_sast, stored_sast
from src.core.audit import record_audit_event
from src.database.models import (
    Appointment,
    AppointmentSlot,
    Patient,
    Queue,
    Site,
    Ticket,
)
from src.database.models.appointment_slot import DEFAULT_CONVERT_LEAD_MINUTES
from src.modules.appointments.booking import when_label
from src.modules.appointments.conversion import convert_one, lead_minutes
from src.modules.patients.consent import record_consent
from src.modules.patients.service import get_or_create_patient
from src.modules.queue.sequence import format_reference_code
from src.modules.queue.service import join_queue
from src.modules.queue.snapshot import on_queue_changed
from src.modules.queue.ticket_codes import read_code
from src.modules.queue.waits import ticket_wait
from src.modules.sites.hours import OpeningSchedule

#: How long before its time a booking may be checked in at the door. An hour is early enough for a
#: patient who caught the first taxi, and short enough that the queue is still the day's real order.
EARLY_MINUTES: Final = 60
#: How many transfer legs a scanned code follows before it gives up (a visit moved from queue to queue).
MAX_LEGS: Final = 10
#: What a manager reads beside the clinic's kiosk walk-in switch.
KIOSK_WALK_INS_EXPLANATION: Final = (
    "On: the check-in tablet at the door also offers your open queues, so a patient with no booking "
    "can take a number without queueing at reception. Off: the tablet only checks in patients who "
    "already hold a ticket or a booking, and sends everyone else to reception."
)
#: Who the audit trail says checked a patient in: the tablet, never a person.
DEVICE_ACTOR: Final = "device:check-in"


class CheckInRefusal(StrEnum):
    """Why the tablet could not check somebody in. The wire code is ``appointments.checkin.<value>``.

    - ``NOT_A_CODE``: what was scanned or typed cannot be a reference or a phone number.
    - ``NOT_FOUND``: this clinic has nothing today under it (another clinic's code included).
    - ``ENDED``: the visit is over, or the booking was cancelled.
    - ``TOO_EARLY``: the booking's time is more than an hour away.
    - ``NOT_OFFERED``: this clinic does not start walk-ins at the door.
    """

    NOT_A_CODE = "not_a_code"
    NOT_FOUND = "not_found"
    ENDED = "ended"
    TOO_EARLY = "too_early"
    NOT_OFFERED = "not_offered"


def _code(refusal: CheckInRefusal) -> str:
    return f"appointments.checkin.{refusal.value}"


class CheckInNotACodeError(UnprocessableError):
    """What was scanned or typed is neither a reference nor a phone number: HTTP 422."""

    def __init__(self) -> None:
        super().__init__(
            "That is not a code. Scan the QR on your phone, or type your six-character "
            "reference or your phone number.",
            code=_code(CheckInRefusal.NOT_A_CODE),
        )


class CheckInNotFoundError(NotFoundError):
    """Nothing at this clinic today answers to it: HTTP 404, the same for another clinic's code."""

    def __init__(self) -> None:
        super().__init__(
            "We cannot find that here today. Please see reception.",
            code=_code(CheckInRefusal.NOT_FOUND),
        )


class CheckInEndedError(ConflictError):
    """The visit is over or the booking was cancelled: HTTP 409."""

    def __init__(self) -> None:
        super().__init__(
            "That visit is finished. Please see reception.",
            code=_code(CheckInRefusal.ENDED),
        )


class CheckInTooEarlyError(ConflictError):
    """The booking's time is more than :data:`EARLY_MINUTES` away: HTTP 409, with the time."""

    def __init__(self, when: str) -> None:
        super().__init__(
            f"Your time is {when}. Please check in when it is closer.",
            code=_code(CheckInRefusal.TOO_EARLY),
        )


class WalkInNotOfferedError(ConflictError):
    """This clinic does not let the tablet start a walk-in: HTTP 409."""

    def __init__(self) -> None:
        super().__init__(
            "Please see reception to join a queue here.",
            code=_code(CheckInRefusal.NOT_OFFERED),
        )


@dataclass(frozen=True, slots=True)
class CheckedIn:
    """What the tablet shows back, and nothing more: a number, where it is, and how many are ahead."""

    ticket_id: str
    number: str
    queue_name: str
    room_label: str | None
    waiting_ahead: int
    wait_label: str | None
    #: True when the patient had already checked in: the tablet says the same thing again.
    already: bool
    #: True when this check-in turned a booking into its ticket.
    from_booking: bool


def _today_ticket(
    db: Session, site_id: str, *, code: str, today: datetime
) -> Ticket | None:
    """The ticket at this clinic today whose reference was scanned, following transfers."""
    ticket = db.execute(
        select(Ticket).where(
            Ticket.site_id == site_id,
            Ticket.reference_code == code,
            Ticket.service_day == business_date(today),
        )
    ).scalar_one_or_none()
    if ticket is None:
        return None
    for _ in range(MAX_LEGS):
        if ticket.status_enum is not TicketStatus.TRANSFERRED:
            break
        after = db.execute(
            select(Ticket).where(
                Ticket.site_id == site_id, Ticket.transferred_from_id == ticket.id
            )
        ).scalar_one_or_none()
        if after is None:
            break
        ticket = after
    return ticket


def _today_booking(
    db: Session, site_id: str, *, code: str, moment: datetime
) -> tuple[Appointment, AppointmentSlot] | None:
    """The booking at this clinic today whose reference was scanned or typed."""
    row = db.execute(
        select(Appointment, AppointmentSlot)
        .join(AppointmentSlot, AppointmentSlot.id == Appointment.slot_id)
        .where(
            Appointment.site_id == site_id,
            Appointment.reference == code,
            AppointmentSlot.service_day == business_date(moment),
        )
    ).first()
    return (row[0], row[1]) if row is not None else None


def _patient_ticket(
    db: Session, site_id: str, patient_id: str, moment: datetime
) -> Ticket | None:
    """The patient's own open ticket at this clinic today, the soonest joined."""
    return (
        db.execute(
            select(Ticket)
            .where(
                Ticket.site_id == site_id,
                Ticket.patient_id == patient_id,
                Ticket.service_day == business_date(moment),
                Ticket.status.in_(sorted(s.value for s in TICKET_ACTIVE_STATUSES)),
            )
            .order_by(Ticket.joined_at)
        )
        .scalars()
        .first()
    )


def _patient_booking(
    db: Session, site_id: str, patient_id: str, moment: datetime
) -> tuple[Appointment, AppointmentSlot] | None:
    """The patient's own booking at this clinic today, the soonest first."""
    row = db.execute(
        select(Appointment, AppointmentSlot)
        .join(AppointmentSlot, AppointmentSlot.id == Appointment.slot_id)
        .where(
            Appointment.site_id == site_id,
            Appointment.patient_id == patient_id,
            Appointment.status == AppointmentStatus.BOOKED.value,
            AppointmentSlot.service_day == business_date(moment),
        )
        .order_by(AppointmentSlot.starts_at)
    ).first()
    return (row[0], row[1]) if row is not None else None


def _standing(
    db: Session, ticket: Ticket, *, moment: datetime, already: bool, from_booking: bool
) -> CheckedIn:
    """What the tablet shows: the number, its queue and room, and how many are ahead."""
    queue = db.get(Queue, ticket.queue_id)
    ahead, wait = (0, None)
    if queue is not None and ticket.status_enum is TicketStatus.WAITING:
        ahead, estimate = ticket_wait(db, ticket, queue, moment=moment)
        wait = estimate.label
    return CheckedIn(
        ticket_id=ticket.id,
        number=ticket.number,
        queue_name=queue.name if queue else "",
        room_label=queue.room_label if queue else None,
        waiting_ahead=ahead,
        wait_label=wait,
        already=already,
        from_booking=from_booking,
    )


def _arrive(
    db: Session,
    ticket: Ticket,
    *,
    moment: datetime,
    from_booking: bool,
    announce: bool = True,
) -> CheckedIn:
    """Stamp the arrival once and tell the board's queue something changed.

    ``announce`` is false for a ticket this check-in has just issued: the join already told the queue,
    in the same transaction, and one change is one event.
    """
    already = ticket.arrived_at is not None
    if ticket.status_enum not in TICKET_ACTIVE_STATUSES:
        raise CheckInEndedError()
    if not already:
        ticket.arrived_at = moment
        db.flush()
        record_audit_event(
            db,
            action=AuditAction.UPDATE,
            entity_type=AuditEntityType.TICKET,
            entity_id=ticket.id,
            actor=DEVICE_ACTOR,
            site_id=ticket.site_id,
            context=f"checked in at the door: ticket {ticket.number}",
        )
        queue = db.get(Queue, ticket.queue_id) if announce else None
        if queue is not None:
            on_queue_changed(db, queue)
    return _standing(
        db, ticket, moment=moment, already=already, from_booking=from_booking
    )


def _check_in_booking(
    db: Session,
    site: Site,
    schedule: OpeningSchedule,
    appointment: Appointment,
    slot: AppointmentSlot,
    *,
    moment: datetime,
) -> CheckedIn:
    """Turn a booking into its ticket now, through Issue 81's one conversion path, and stamp the arrival.

    Raises:
        CheckInEndedError: The booking is cancelled, lapsed, or was already converted onto a ticket that
            has since ended.
        CheckInTooEarlyError: Its time is more than :data:`EARLY_MINUTES` away.
        JoinRefusedError: The queue cannot take the ticket (it is full, or the clinic is closed).
    """
    if appointment.status == AppointmentStatus.CONVERTED.value:
        ticket = db.execute(
            select(Ticket).where(Ticket.appointment_id == appointment.id)
        ).scalar_one_or_none()
        if ticket is None:
            raise CheckInEndedError()
        return _arrive(db, ticket, moment=moment, from_booking=True)
    if appointment.status != AppointmentStatus.BOOKED.value:
        raise CheckInEndedError()
    starts = stored_sast(slot.starts_at)
    opens = starts - timedelta(
        minutes=max(
            EARLY_MINUTES, lead_minutes(db, site.id) or DEFAULT_CONVERT_LEAD_MINUTES
        )
    )
    if moment < opens:
        raise CheckInTooEarlyError(when_label(starts))
    ticket = convert_one(db, appointment, schedule=schedule, moment=moment)
    return _arrive(db, ticket, moment=moment, from_booking=True, announce=False)


def check_in(
    db: Session,
    site: Site,
    schedule: OpeningSchedule,
    raw: str,
    *,
    moment: datetime | None = None,
) -> CheckedIn:
    """Check somebody in from what the tablet read: a QR, a typed reference, or a phone number.

    The caller commits.

    Raises:
        CheckInNotACodeError: It is neither a reference nor a phone number.
        CheckInNotFoundError: Nothing at this clinic today answers to it.
        CheckInEndedError: The visit is over or the booking was cancelled.
        CheckInTooEarlyError: The booking's time is more than an hour away.
        JoinRefusedError: A booking's queue cannot take the ticket now.
    """
    moment = moment or now_sast()
    code = read_code(raw)
    if code is not None:
        ticket = _today_ticket(db, site.id, code=code, today=moment)
        if ticket is not None:
            return _arrive(db, ticket, moment=moment, from_booking=False)
        found = _today_booking(db, site.id, code=code, moment=moment)
        if found is not None:
            return _check_in_booking(db, site, schedule, *found, moment=moment)
        raise CheckInNotFoundError()
    try:
        phone = normalize_phone(raw)
    except InvalidPhoneNumberError as error:
        raise CheckInNotACodeError() from error
    patient = db.execute(
        select(Patient).where(
            Patient.phone_e164 == phone, Patient.is_deleted.is_(False)
        )
    ).scalar_one_or_none()
    if patient is None:
        raise CheckInNotFoundError()
    ticket = _patient_ticket(db, site.id, patient.id, moment)
    if ticket is not None:
        return _arrive(db, ticket, moment=moment, from_booking=False)
    booked = _patient_booking(db, site.id, patient.id, moment)
    if booked is None:
        raise CheckInNotFoundError()
    return _check_in_booking(db, site, schedule, *booked, moment=moment)


def start_walk_in(
    db: Session,
    site: Site,
    queue: Queue,
    schedule: OpeningSchedule,
    *,
    phone: str | None = None,
    notifications_consent: bool = False,
    moment: datetime | None = None,
) -> CheckedIn:
    """Start a walk-in at the door, where the clinic allows it. The caller commits.

    The ticket is a walk-in like any other issued at the desk: the patient is in the building. A phone
    number is optional, and is kept only with the patient's own answer about being messaged.

    Raises:
        WalkInNotOfferedError: This clinic does not start walk-ins at the tablet.
        CheckInNotACodeError: The phone number cannot be read.
        JoinRefusedError: The queue cannot take another ticket now.
    """
    moment = moment or now_sast()
    if not site.kiosk_walk_ins_enabled:
        raise WalkInNotOfferedError()
    patient: Patient | None = None
    if phone:
        try:
            patient, _ = get_or_create_patient(db, normalize_phone(phone))
        except InvalidPhoneNumberError as error:
            raise CheckInNotACodeError() from error
        patient.last_channel = PatientChannel.WALK_IN.value
    result = join_queue(
        db,
        site=site,
        queue=queue,
        schedule=schedule,
        source=TicketSource.WALK_IN,
        patient=patient,
        actor=DEVICE_ACTOR,
        moment=moment,
    )
    if patient is not None and notifications_consent:
        record_consent(
            db,
            patient,
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WALK_IN,
            site_id=site.id,
        )
    return _arrive(db, result.ticket, moment=moment, from_booking=False, announce=False)


def reference_of(appointment: Appointment) -> str:
    """A booking's reference as it is printed and scanned (``K7M-4QP``)."""
    return format_reference_code(appointment.reference)
