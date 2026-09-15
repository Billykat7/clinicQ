"""Appointment reminders a day and two hours before, answered by reply (Issue 82).

**Sending.** :func:`send_due` is the reminder sweep's body (``run_appointment_reminders``, lock 882, every
minute). A booking still ``booked`` is reminded once in each window:

* the **day before**: from 24 hours before its time until 2 hours before, if it was booked more than a day
  ahead;
* **two hours before**: from 2 hours before until its time, if it was booked more than two hours ahead.

Each reminder is claimed with a conditional ``UPDATE`` of its ``reminded_*_at`` column and sent with a dedupe
key, so it goes once however many sweeps run. It goes **through the notification service**, which applies the
patient's preferred transport, quiet hours, mutes, consent and opt-out: none of those is re-checked here. A
patient who has **already checked in** gets no reminder: their booking is no longer ``booked`` (it became a
ticket), or they already hold a ticket in that queue that day.

**Replying**, without opening any app:

* **SMS**: ``CONFIRM`` (or ``YES``) keeps the booking; ``CANCEL`` (or ``NO``) gives the time back at once.
  ``CANCEL`` is also a STOP synonym, so it cancels the booking only when the patient has a reminded booking
  open, and stops messages otherwise, as before. A keyword reply answers the soonest reminded booking.
* **Web push**: the reminder's own Confirm and Cancel buttons post to ``/api/v1/appointments/replies/{token}``.
* **WhatsApp**: quick-reply buttons call :func:`reply_by_phone` once Issue 75's inbound adapter exists.

**What Issue 93 measures.** Every booking keeps when it was reminded, whether it was confirmed, whether it was
cancelled by reply and through which channel; with its ticket's outcome, :func:`reminder_outcomes` counts
attendance with and without reminders per clinic.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.commons.enums import (
    SMS_CANCEL_BOOKING_KEYWORDS,
    SMS_CONFIRM_KEYWORDS,
    TICKET_ACTIVE_STATUSES,
    AppointmentStatus,
    AuditAction,
    AuditEntityType,
    BookingReply,
    BookingReplyChannel,
    PatientEvent,
    TicketStatus,
)
from src.commons.exceptions import NotFoundError
from src.commons.phone import InvalidPhoneNumberError
from src.commons.time import business_day_bounds, now_sast, stored_sast
from src.core.audit import record_audit_event
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models import Appointment, AppointmentSlot, Queue, Site, Ticket
from src.modules.appointments import capacity
from src.modules.appointments.booking import when_label
from src.modules.notifications import service as notifications
from src.modules.notifications.template_registry import REPLY_PATH_PREFIX
from src.modules.patients.service import find_patient
from src.modules.queue.sequence import format_reference_code

#: The two reminders, and how long before the appointment each is due.
REMINDER_LEADS: Final[dict[PatientEvent, timedelta]] = {
    PatientEvent.REMINDER_24H: timedelta(hours=24),
    PatientEvent.REMINDER_2H: timedelta(hours=2),
}
#: A reply token is 256 random bits, URL-safe.
_TOKEN_SHAPE: Final = re.compile(r"[A-Za-z0-9_-]{43}")
REPLY_NOT_FOUND_CODE: Final = "appointments.reply.not_found"


@dataclass(slots=True)
class RemindersSent:
    """What one sweep did."""

    day_before: list[str] = field(default_factory=list)
    two_hours: list[str] = field(default_factory=list)
    #: Bookings not reminded because the patient already holds a ticket in that queue that day.
    checked_in: list[str] = field(default_factory=list)


def reply_path(token: str) -> str:
    """Where a web push's reply buttons send the answer."""
    return f"{REPLY_PATH_PREFIX}{token}"


def _checked_in(db: Session, appointment: Appointment, slot: AppointmentSlot) -> bool:
    """Whether the patient already holds an open ticket in the booking's queue on its day."""
    return (
        db.execute(
            select(Ticket.id).where(
                Ticket.patient_id == appointment.patient_id,
                Ticket.queue_id == appointment.queue_id,
                Ticket.service_day == slot.service_day,
                Ticket.status.in_(
                    sorted(status.value for status in TICKET_ACTIVE_STATUSES)
                ),
            )
        ).first()
        is not None
    )


def _claim(
    db: Session, appointment: Appointment, event: PatientEvent, moment: datetime
) -> bool:
    """Mark one reminder sent, once: a conditional ``UPDATE`` a second sweep matches nothing with."""
    column = (
        Appointment.reminded_24h_at
        if event is PatientEvent.REMINDER_24H
        else Appointment.reminded_2h_at
    )
    moved = db.execute(
        update(Appointment)
        .where(
            Appointment.id == appointment.id,
            Appointment.status == AppointmentStatus.BOOKED.value,
            column.is_(None),
        )
        .values({column.key: moment})
        .execution_options(synchronize_session=False)
    )
    return int(getattr(moved, "rowcount", 0) or 0) == 1


def _remind(
    db: Session,
    appointment: Appointment,
    slot: AppointmentSlot,
    event: PatientEvent,
    moment: datetime,
) -> None:
    """Send one reminder through the notification service."""
    site = db.get(Site, appointment.site_id)
    queue = db.get(Queue, appointment.queue_id)
    notifications.notify(
        db,
        patient_id=appointment.patient_id,
        event=event,
        context={
            "number": format_reference_code(appointment.reference),
            "clinic": site.name if site else "",
            "queue": queue.name if queue else "",
            "room": queue.room_label if queue else None,
            "when": when_label(slot.starts_at),
            "reply_url": reply_path(appointment.reply_token)
            if appointment.reply_token
            else None,
        },
        site_id=appointment.site_id,
        dedupe_key=f"{appointment.id}:{event.value}",
        now=moment,
    )


def send_due(db: Session, *, moment: datetime | None = None) -> RemindersSent:
    """Send every reminder whose window has come, once each. The caller commits."""
    moment = moment or now_sast()
    sent = RemindersSent()
    rows = db.execute(
        select(Appointment, AppointmentSlot)
        .join(AppointmentSlot, AppointmentSlot.id == Appointment.slot_id)
        .where(
            Appointment.status == AppointmentStatus.BOOKED.value,
            AppointmentSlot.starts_at > moment,
            AppointmentSlot.starts_at
            <= moment + REMINDER_LEADS[PatientEvent.REMINDER_24H],
        )
        .order_by(AppointmentSlot.starts_at)
    ).all()
    for appointment, slot in rows:
        starts = stored_sast(slot.starts_at)
        booked = stored_sast(appointment.booked_at)
        if moment < starts - REMINDER_LEADS[PatientEvent.REMINDER_2H]:
            event, bucket = PatientEvent.REMINDER_24H, sent.day_before
            already = appointment.reminded_24h_at
        else:
            event, bucket = PatientEvent.REMINDER_2H, sent.two_hours
            already = appointment.reminded_2h_at
        if already is not None or booked > starts - REMINDER_LEADS[event]:
            continue  # sent, or booked inside this window: the booking confirmation said it all
        if _checked_in(db, appointment, slot):
            sent.checked_in.append(appointment.id)
            continue
        with db.begin_nested():
            if _claim(db, appointment, event, moment):
                _remind(db, appointment, slot, event, moment)
                bucket.append(appointment.id)
    return sent


# --------------------------------------------------------------------------------------
# Replies
# --------------------------------------------------------------------------------------


def apply(
    db: Session,
    appointment: Appointment,
    reply: BookingReply,
    channel: BookingReplyChannel,
    *,
    moment: datetime | None = None,
) -> bool:
    """Confirm or cancel one booking from a reply. Idempotent; ``False`` when the booking is no longer booked.

    A cancellation gives the time back in this transaction, so the slot is offered again the moment the reply
    commits. The caller commits.
    """
    moment = moment or now_sast()
    if appointment.status != AppointmentStatus.BOOKED.value:
        return False
    if reply is BookingReply.CONFIRM:
        if appointment.confirmed_at is None:
            appointment.confirmed_at = moment
            db.flush()
    else:
        if not capacity.release_place(db, appointment, moment=moment):
            return False
        appointment.cancelled_via = channel.value
        db.flush()
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.APPOINTMENT,
        entity_id=appointment.id,
        actor=f"patient:{appointment.patient_id}",
        site_id=appointment.site_id,
        context=f"booking {format_reference_code(appointment.reference)} {reply.value} by {channel.value} reply",
    )
    return True


def by_token(db: Session, token: str) -> Appointment:
    """The booking a reminder's reply buttons name; anything else is the same "not found".

    Raises:
        NotFoundError: No such booking.
    """
    appointment = (
        db.execute(
            select(Appointment).where(Appointment.reply_token == token)
        ).scalar_one_or_none()
        if _TOKEN_SHAPE.fullmatch(token)
        else None
    )
    if appointment is None:
        raise NotFoundError("No such booking.", code=REPLY_NOT_FOUND_CODE)
    return appointment


def keyword_reply(text: str) -> BookingReply | None:
    """``CONFIRM``/``YES`` or ``CANCEL``/``NO`` as the first word of a message, or ``None``."""
    words = text.strip().split()
    keyword = words[0].upper().strip(".!,") if words else ""
    if keyword in SMS_CONFIRM_KEYWORDS:
        return BookingReply.CONFIRM
    if keyword in SMS_CANCEL_BOOKING_KEYWORDS:
        return BookingReply.CANCEL
    return None


def reply_by_phone(
    db: Session,
    phone: str,
    text: str,
    channel: BookingReplyChannel,
    *,
    moment: datetime | None = None,
) -> tuple[BookingReply, str] | None:
    """Treat a message as a reply to the sender's soonest reminded booking; ``None`` when it is not one.

    Returns the reply and the patient's id when a booking was confirmed or cancelled. A patient with no reminded
    booking open gets ``None``, so a ``CANCEL`` falls through to the STOP handling it has always had.
    """
    moment = moment or now_sast()
    reply = keyword_reply(text)
    if reply is None:
        return None
    try:
        patient = find_patient(db, phone)
    except InvalidPhoneNumberError:
        patient = None
    if patient is None:
        return None
    found = db.execute(
        select(Appointment)
        .join(AppointmentSlot, AppointmentSlot.id == Appointment.slot_id)
        .where(
            Appointment.patient_id == patient.id,
            Appointment.status == AppointmentStatus.BOOKED.value,
            (Appointment.reminded_24h_at.is_not(None))
            | (Appointment.reminded_2h_at.is_not(None)),
            AppointmentSlot.starts_at > moment,
        )
        .order_by(AppointmentSlot.starts_at)
        .limit(1)
    ).scalar_one_or_none()
    if found is None or not apply(db, found, reply, channel, moment=moment):
        return None
    return reply, patient.id


# --------------------------------------------------------------------------------------
# What Issue 93 measures
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReminderOutcome:
    """Bookings in one reminder group, and what became of them."""

    reminders: int
    bookings: int
    confirmed: int
    cancelled_by_reply: int
    attended: int
    no_show: int


def reminder_outcomes(
    db: Session, access: SiteAccess, *, start: date, end: date
) -> list[ReminderOutcome]:
    """Per number of reminders sent (0, 1, 2): bookings, confirmations, cancellations by reply, attended, no-shows.

    Attended means the booking's ticket reached the consulting room (``in_progress`` or ``done``); a no-show means
    its ticket was marked ``no_show``. Read through the site guard, for bookings whose day falls in the range.
    """
    first, _ = business_day_bounds(start)
    _, last = business_day_bounds(end)
    rows = db.execute(
        scoped_select(Appointment, access)
        .join(AppointmentSlot, AppointmentSlot.id == Appointment.slot_id)
        .outerjoin(Ticket, Ticket.appointment_id == Appointment.id)
        .add_columns(Ticket.status)
        .where(AppointmentSlot.starts_at >= first, AppointmentSlot.starts_at < last)
    ).all()
    groups: dict[int, Counter[str]] = {0: Counter(), 1: Counter(), 2: Counter()}
    for appointment, ticket_status in rows:
        count = (appointment.reminded_24h_at is not None) + (
            appointment.reminded_2h_at is not None
        )
        group = groups[count]
        group["bookings"] += 1
        group["confirmed"] += appointment.confirmed_at is not None
        group["cancelled_by_reply"] += appointment.cancelled_via is not None
        group["attended"] += ticket_status in (
            TicketStatus.IN_PROGRESS.value,
            TicketStatus.DONE.value,
        )
        group["no_show"] += ticket_status == TicketStatus.NO_SHOW.value
    return [
        ReminderOutcome(
            reminders=count,
            bookings=group["bookings"],
            confirmed=group["confirmed"],
            cancelled_by_reply=group["cancelled_by_reply"],
            attended=group["attended"],
            no_show=group["no_show"],
        )
        for count, group in groups.items()
    ]
