"""Booking, moving and cancelling an appointment: one set of functions for every channel (Issue 81).

A patient books on the web today, and on USSD and WhatsApp when their adapters arrive (Issues 73 to 76).
**Every channel calls these functions, with its own** :class:`~src.commons.enums.TicketSource` **and nothing
else different**, as every channel calls Issue 40's ``join_queue``. The source is recorded on the booking and
carried onto its ticket, and never changes a rule.

* :func:`book` reads only clinics a patient may be shown, asks the offer rule (a block, the clinic's hours,
  the lead time, the horizon) and then the capacity guard, which refuses a full slot, a full day or a second
  booking by the same patient in that queue that day. The patient is sent the reference and the time.
* :func:`reschedule` frees the original place and takes the new one **in one transaction**, the original
  first. If the new slot refuses, the savepoint rolls back and the original booking stands untouched. Locks
  are taken day first, then booking, then slot, the same order as every other writer (see
  :mod:`src.modules.appointments.capacity`), so a move cannot deadlock a booking or a conversion.
* :func:`cancel` gives the place back at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import (
    AppointmentStatus,
    AuditAction,
    AuditEntityType,
    PatientEvent,
    TicketSource,
)
from src.commons.exceptions import ConflictError, NotFoundError
from src.commons.time import business_date, now_sast, stored_sast
from src.core.audit import record_audit_event
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models import (
    Appointment,
    AppointmentSlot,
    Patient,
    Queue,
    Site,
    Ticket,
)
from src.modules.appointments import capacity, service
from src.modules.appointments.availability import refusal_for
from src.modules.appointments.schemas import BookingOut
from src.modules.notifications import service as notifications
from src.modules.queue.sequence import format_reference_code
from src.modules.queue.ticket_page import page_url_for
from src.modules.sites.hours import published_schedules

#: The refusal codes a booking can answer with, besides a slot's own (``appointments.slot.<reason>``).
SLOT_NOT_FOUND_CODE: Final = "appointments.slot.not_found"
BOOKING_NOT_FOUND_CODE: Final = "appointments.booking.not_found"
NOT_BOOKED_CODE: Final = "appointments.booking.not_booked"
SAME_SLOT_CODE: Final = "appointments.booking.same_slot"


class BookingNotBookedError(ConflictError):
    """The booking has already become a ticket, been cancelled or been moved: HTTP 409."""


@dataclass(frozen=True, slots=True)
class Booked:
    """A booking as every channel confirms it."""

    appointment: Appointment
    slot: AppointmentSlot
    queue: Queue
    site: Site

    @property
    def reference(self) -> str:
        """The reference as read aloud: ``K7M-4QP``."""
        return format_reference_code(self.appointment.reference)

    @property
    def starts_at(self) -> datetime:
        """When the appointment is, in Johannesburg time."""
        return stored_sast(self.slot.starts_at)

    @property
    def when(self) -> str:
        """The time as a message says it: ``Tue 6 Oct 10:30``."""
        return when_label(self.starts_at)

    @property
    def confirmation(self) -> str:
        """The sentence every channel says after booking."""
        return (
            f"Booked: {self.queue.name} at {self.site.name}, {self.when}. Reference {self.reference}. "
            "It becomes your place in the queue shortly before the time."
        )


def when_label(moment: datetime) -> str:
    """A time the way a message says it, in Johannesburg: ``Tue 6 Oct 10:30``."""
    local = stored_sast(moment)
    return f"{local:%a} {local.day} {local:%b %H:%M}"


def _published_slot(
    db: Session, site_id: str, slot_id: str
) -> tuple[AppointmentSlot, Queue]:
    """A slot and its queue at a clinic a patient may be shown, or the same "not found" for anything else."""
    rows = service.published_rows([site_id])
    slot = db.execute(
        rows(AppointmentSlot).where(AppointmentSlot.id == slot_id)
    ).scalar_one_or_none()
    queue = (
        db.execute(
            rows(Queue).where(
                Queue.id == slot.queue_id,
                Queue.is_deleted.is_(False),
                Queue.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if slot is not None
        else None
    )
    if slot is None or queue is None:
        raise NotFoundError("No such appointment time.", code=SLOT_NOT_FOUND_CODE)
    return slot, queue


def _check_offer(
    db: Session, site_id: str, slot: AppointmentSlot, moment: datetime
) -> None:
    """Refuse a slot the offer rule would not show: the same reasons as the day view."""
    day = slot.service_day
    opening = published_schedules(
        db, [site_id], from_day=day - service.PLANNING_MARGIN, horizon_days=3
    ).get(site_id)
    if opening is None:
        raise NotFoundError("No such appointment time.", code=SLOT_NOT_FOUND_CODE)
    context = service.offer_context(
        db, service.published_rows([site_id]), opening, day, day, moment
    )
    refusal = refusal_for(slot, context)
    if refusal is not None:
        raise capacity.SlotRefusedError(refusal)


def _tell_booked(db: Session, booked: Booked, *, moment: datetime) -> None:
    """Send the reference and the time through the patient's own notification settings."""
    notifications.notify(
        db,
        patient_id=booked.appointment.patient_id,
        event=PatientEvent.BOOKED,
        context={
            "number": booked.reference,
            "clinic": booked.site.name,
            "queue": booked.queue.name,
            "room": booked.queue.room_label,
            "when": booked.when,
        },
        site_id=booked.site.id,
        dedupe_key=f"{booked.appointment.id}:booked",
        now=moment,
    )


def _audit(
    db: Session,
    appointment: Appointment,
    *,
    action: AuditAction,
    actor: str,
    context: str,
) -> None:
    """One audit row per booking change, naming the channel and never more about the patient than their id."""
    record_audit_event(
        db,
        action=action,
        entity_type=AuditEntityType.APPOINTMENT,
        entity_id=appointment.id,
        actor=actor,
        site_id=appointment.site_id,
        context=context,
    )


def book(
    db: Session,
    *,
    site_id: str,
    slot_id: str,
    patient: Patient,
    source: TicketSource,
    actor: str,
    moment: datetime | None = None,
) -> Booked:
    """Book a place for ``patient`` in a slot, from any channel. The caller commits.

    Raises:
        NotFoundError: No such slot at a clinic a patient may see (``appointments.slot.not_found``).
        SlotRefusedError: The slot is not on offer, or it, the day, or the patient's day in that queue is full.
    """
    moment = moment or now_sast()
    slot, queue = _published_slot(db, site_id, slot_id)
    _check_offer(db, site_id, slot, moment)
    appointment = capacity.claim_place(
        db, slot=slot, queue=queue, patient_id=patient.id, source=source, moment=moment
    )
    site = db.get(Site, site_id)
    assert site is not None
    booked = Booked(appointment, slot, queue, site)
    _audit(
        db,
        appointment,
        action=AuditAction.CREATE,
        actor=actor,
        context=f"booked {queue.name} {booked.when} as {booked.reference} via {source.value}",
    )
    _tell_booked(db, booked, moment=moment)
    return booked


def own_booking(db: Session, patient_id: str, appointment_id: str) -> Appointment:
    """One of the patient's own bookings; another patient's, or none, is the same "not found"."""
    appointment = db.get(Appointment, appointment_id)
    if appointment is None or appointment.patient_id != patient_id:
        raise NotFoundError("No such booking.", code=BOOKING_NOT_FOUND_CODE)
    return appointment


def _still_booked(appointment: Appointment) -> None:
    """Refuse to change a booking that is no longer waiting for its time."""
    if appointment.status != AppointmentStatus.BOOKED.value:
        raise BookingNotBookedError(
            "This booking can no longer be changed: it has become a ticket, or was cancelled or moved.",
            code=NOT_BOOKED_CODE,
        )


def reschedule(
    db: Session,
    *,
    patient: Patient,
    appointment_id: str,
    slot_id: str,
    actor: str,
    moment: datetime | None = None,
) -> Booked:
    """Move a booking to another slot at the same clinic: the original place is free at once. The caller commits.

    All or nothing: when the new slot refuses, the original booking stands exactly as it was.

    Raises:
        NotFoundError: Not the patient's booking, or no such slot.
        BookingNotBookedError: The booking has become a ticket, or was cancelled or moved.
        ConflictError: The same slot (``appointments.booking.same_slot``).
        SlotRefusedError: The new slot is not on offer, or is full.
    """
    moment = moment or now_sast()
    appointment = own_booking(db, patient.id, appointment_id)
    _still_booked(appointment)
    if appointment.slot_id == slot_id:
        raise ConflictError(
            "That is already the time you have booked.", code=SAME_SLOT_CODE
        )
    old_slot = db.get(AppointmentSlot, appointment.slot_id)
    slot, queue = _published_slot(db, appointment.site_id, slot_id)
    _check_offer(db, appointment.site_id, slot, moment)
    assert old_slot is not None
    with db.begin_nested():
        # Day locks first, in a fixed order, then the booking, then the slots.
        days: list[tuple[str, date]] = sorted(
            {(old_slot.queue_id, old_slot.service_day), (queue.id, slot.service_day)}
        )
        for queue_id, day in days:
            capacity.lock_day(db, queue_id, day)
        if not capacity.release_place(
            db, appointment, to=AppointmentStatus.RESCHEDULED, moment=moment
        ):
            raise BookingNotBookedError(
                "This booking can no longer be changed: it has become a ticket, or was cancelled or moved.",
                code=NOT_BOOKED_CODE,
            )
        moved = capacity.claim_place(
            db,
            slot=slot,
            queue=queue,
            patient_id=patient.id,
            source=TicketSource(appointment.source),
            rescheduled_from_id=appointment.id,
            moment=moment,
        )
    site = db.get(Site, appointment.site_id)
    assert site is not None
    booked = Booked(moved, slot, queue, site)
    _audit(
        db,
        moved,
        action=AuditAction.UPDATE,
        actor=actor,
        context=(
            f"moved booking {format_reference_code(appointment.reference)} to {queue.name} "
            f"{booked.when} as {booked.reference}"
        ),
    )
    _tell_booked(db, booked, moment=moment)
    return booked


def cancel(
    db: Session,
    *,
    patient: Patient,
    appointment_id: str,
    actor: str,
    moment: datetime | None = None,
) -> Appointment:
    """Cancel one of the patient's own bookings: the place is free at once. The caller commits.

    Raises:
        NotFoundError: Not the patient's booking.
        BookingNotBookedError: The booking has become a ticket, or was cancelled or moved already.
    """
    moment = moment or now_sast()
    appointment = own_booking(db, patient.id, appointment_id)
    _still_booked(appointment)
    if not capacity.release_place(db, appointment, moment=moment):
        raise BookingNotBookedError(
            "This booking can no longer be changed: it has become a ticket, or was cancelled or moved.",
            code=NOT_BOOKED_CODE,
        )
    _audit(
        db,
        appointment,
        action=AuditAction.UPDATE,
        actor=actor,
        context=f"cancelled booking {format_reference_code(appointment.reference)}",
    )
    return appointment


def patient_bookings(
    db: Session, patient_id: str, *, moment: datetime | None = None
) -> list[tuple[Appointment, AppointmentSlot, Queue, Site]]:
    """A patient's own bookings from today on, soonest first, at whichever clinics."""
    today = business_date(moment or now_sast())
    rows = db.execute(
        select(Appointment, AppointmentSlot, Queue, Site)
        .join(AppointmentSlot, AppointmentSlot.id == Appointment.slot_id)
        .join(Queue, Queue.id == Appointment.queue_id)
        .join(Site, Site.id == Appointment.site_id)
        .where(
            Appointment.patient_id == patient_id, AppointmentSlot.service_day >= today
        )
        .order_by(AppointmentSlot.starts_at)
    ).all()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def booking_view(
    db: Session,
    appointment: Appointment,
    slot: AppointmentSlot,
    queue: Queue,
    site: Site,
    *,
    message: str | None = None,
) -> BookingOut:
    """A booking as the API returns it, with the ticket page once it has become a ticket."""
    ticket = (
        db.execute(
            select(Ticket).where(Ticket.appointment_id == appointment.id)
        ).scalar_one_or_none()
        if appointment.status == AppointmentStatus.CONVERTED.value
        else None
    )
    booked = Booked(appointment, slot, queue, site)
    status = AppointmentStatus(appointment.status)
    return BookingOut(
        id=appointment.id,
        reference=booked.reference,
        status=status,
        source=TicketSource(appointment.source),
        site_id=site.id,
        clinic=site.name,
        queue_id=queue.id,
        queue_name=queue.name,
        starts_at=booked.starts_at,
        ends_at=stored_sast(slot.ends_at),
        booked_at=stored_sast(appointment.booked_at),
        converted_at=stored_sast(appointment.converted_at)
        if appointment.converted_at
        else None,
        rescheduled_from_id=appointment.rescheduled_from_id,
        ticket_page_url=page_url_for(ticket) if ticket is not None else None,
        changeable=status is AppointmentStatus.BOOKED,
        reminded_24h_at=stored_sast(appointment.reminded_24h_at)
        if appointment.reminded_24h_at
        else None,
        reminded_2h_at=stored_sast(appointment.reminded_2h_at)
        if appointment.reminded_2h_at
        else None,
        confirmed_at=stored_sast(appointment.confirmed_at)
        if appointment.confirmed_at
        else None,
        message=message
        or (
            CONFIRMED_SENTENCE
            if status is AppointmentStatus.BOOKED and appointment.confirmed_at
            else _STATUS_SENTENCE[status]
        ).format(when=booked.when, reference=booked.reference),
    )


#: A booked appointment the patient confirmed by replying to a reminder (Issue 82).
CONFIRMED_SENTENCE: Final = "Confirmed for {when}, reference {reference}. See you then."
#: What a booking's status says to the patient.
_STATUS_SENTENCE: Final[dict[AppointmentStatus, str]] = {
    AppointmentStatus.BOOKED: "Booked for {when}, reference {reference}.",
    AppointmentStatus.CANCELLED: "Cancelled. The time is free for someone else.",
    AppointmentStatus.RESCHEDULED: "Moved to another time.",
    AppointmentStatus.CONVERTED: "Your booking is now your place in the queue.",
    AppointmentStatus.LAPSED: "This booking could not go ahead. Please book again.",
}


def site_bookings(
    db: Session, access: SiteAccess, day: date
) -> list[tuple[Appointment, AppointmentSlot, Queue, Site]]:
    """A clinic's bookings on one service day, in time order, through the site guard."""
    rows = db.execute(
        scoped_select(Appointment, access)
        .join(AppointmentSlot, AppointmentSlot.id == Appointment.slot_id)
        .add_columns(AppointmentSlot)
        .where(AppointmentSlot.service_day == day)
        .order_by(AppointmentSlot.starts_at)
    ).all()
    site = db.get(Site, access.site_id)
    queues = {
        queue.id: queue for queue in db.execute(scoped_select(Queue, access)).scalars()
    }
    assert site is not None
    return [(row[0], row[1], queues[row[0].queue_id], site) for row in rows]


def view_by_id(
    db: Session, appointment_id: str, *, message: str | None = None
) -> BookingOut:
    """The API's view of one booking by id, after the caller has checked whose it is."""
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise NotFoundError("No such booking.", code=BOOKING_NOT_FOUND_CODE)
    slot = db.get(AppointmentSlot, appointment.slot_id)
    queue = db.get(Queue, appointment.queue_id)
    site = db.get(Site, appointment.site_id)
    assert slot is not None and queue is not None and site is not None
    return booking_view(db, appointment, slot, queue, site, message=message)
