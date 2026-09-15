"""A booking becomes a ticket shortly before its time, in the same queue as everyone else (Issue 81).

The conversion sweep (:func:`src.core.scheduler.run_appointment_conversion`, advisory lock 881) runs every
minute and calls :func:`convert_due`. Each due booking goes through **Issue 40's** ``join_queue``: the same
function every channel calls, drawing the same counter, so a booked patient takes the next number in the
same sequence as a walk-in who arrives at that minute, and the clinic runs one board, not a board and a
diary that disagree.

**When.** A booking is due at its slot's start minus the clinic's ``convert_lead_minutes`` (30 by default).

**Idempotent, keyed on the booking.** Converting twice cannot make two tickets:

* inside the join's savepoint the booking moves ``booked → converted`` with a conditional ``UPDATE``; a
  second run finds it no longer booked and issues nothing (the savepoint rolls the number back);
* ``uq_ticket_appointment`` refuses a second ticket naming the booking, whatever writes it.

**The late rule** (documented here and on the booking page). A booked patient is never silently dropped:

1. Conversion does not wait for the patient. At the lead time the ticket exists, whether or not they have
   arrived, and it is called like any other.
2. If the clinic is closed at the lead time (it opens at the slot's start, or a closure is announced), the
   sweep keeps trying on every run for the rest of that service day. **A booking converted after its slot
   has started still gets a ticket**, at the back of the line like any arrival.
3. A patient who arrives after being called follows the recall rule (Issue 43), like everyone. Once the
   ticket is marked a no-show, they may join the queue again like anyone else; the booking is not a second
   claim on the room.
4. A booking still unconverted when its service day ends (the clinic never opened that day) is marked
   ``lapsed``, and the patient is told, never left to find out at the door.

A patient who already holds a ticket in that queue that day (they walked in early) keeps it: the booking is
marked converted onto that ticket, and no second place is taken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.commons.enums import (
    AppointmentStatus,
    AuditAction,
    AuditEntityType,
    JoinRefusal,
    PatientEvent,
    TicketSource,
)
from src.commons.time import business_date, now_sast, stored_sast
from src.core.audit import record_audit_event
from src.database.models import (
    Appointment,
    AppointmentPolicy,
    AppointmentSlot,
    Patient,
    Queue,
    Site,
    Ticket,
)
from src.database.models.appointment_slot import DEFAULT_CONVERT_LEAD_MINUTES
from src.modules.appointments.booking import when_label
from src.modules.appointments.capacity import (
    BookingAlreadyConvertedError,
    mark_converted,
)
from src.modules.notifications import service as notifications
from src.modules.queue.sequence import format_reference_code
from src.modules.queue.service import JoinRefusedError, join_queue
from src.modules.sites.hours import OpeningSchedule, published_schedules

#: Who the audit trail says converted a booking: the sweep, never a person.
CONVERSION_ACTOR = "system:appointment-conversion"


@dataclass(slots=True)
class Converted:
    """What one run did: bookings converted (with their ticket), still waiting (the clinic is shut), lapsed."""

    converted: list[tuple[str, str]] = field(default_factory=list)
    waiting: list[tuple[str, JoinRefusal]] = field(default_factory=list)
    lapsed: list[str] = field(default_factory=list)


def _due(db: Session, moment: datetime) -> list[tuple[Appointment, AppointmentSlot]]:
    """Every booking still booked for today or earlier, with its slot, soonest first."""
    rows = db.execute(
        select(Appointment, AppointmentSlot)
        .join(AppointmentSlot, AppointmentSlot.id == Appointment.slot_id)
        .where(
            Appointment.status == AppointmentStatus.BOOKED.value,
            AppointmentSlot.service_day <= business_date(moment),
        )
        .order_by(AppointmentSlot.starts_at)
    ).all()
    return [(row[0], row[1]) for row in rows]


def _leads(db: Session, site_ids: set[str]) -> dict[str, int]:
    """Each clinic's lead time in minutes; the default where a clinic has set none."""
    found = {
        row.site_id: row.convert_lead_minutes
        for row in db.execute(
            select(AppointmentPolicy).where(
                AppointmentPolicy.site_id.in_(sorted(site_ids))
            )
        ).scalars()
    }
    return {
        site_id: found.get(site_id, DEFAULT_CONVERT_LEAD_MINUTES)
        for site_id in site_ids
    }


def _lapse(
    db: Session, appointment: Appointment, slot: AppointmentSlot, moment: datetime
) -> bool:
    """Mark a booking whose day has ended lapsed, once, and tell the patient."""
    moved = db.execute(
        update(Appointment)
        .where(
            Appointment.id == appointment.id,
            Appointment.status == AppointmentStatus.BOOKED.value,
        )
        .values(status=AppointmentStatus.LAPSED.value, lapsed_at=moment)
        .execution_options(synchronize_session=False)
    )
    if int(getattr(moved, "rowcount", 0) or 0) != 1:
        return False
    site = db.get(Site, appointment.site_id)
    queue = db.get(Queue, appointment.queue_id)
    reference = format_reference_code(appointment.reference)
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.APPOINTMENT,
        entity_id=appointment.id,
        actor=CONVERSION_ACTOR,
        site_id=appointment.site_id,
        context=f"booking {reference} lapsed: its day ended before it could become a ticket",
    )
    notifications.notify(
        db,
        patient_id=appointment.patient_id,
        event=PatientEvent.BOOKING_LAPSED,
        context={
            "number": reference,
            "clinic": site.name if site else "",
            "queue": queue.name if queue else "",
            "room": queue.room_label if queue else None,
            "when": when_label(slot.starts_at),
        },
        site_id=appointment.site_id,
        dedupe_key=f"{appointment.id}:lapsed",
        now=moment,
    )
    return True


def convert_due(db: Session, *, moment: datetime | None = None) -> Converted:
    """Turn every due booking into a ticket through the join service; lapse the ones whose day has ended.

    Each booking is its own savepoint, so one clinic's closure never holds another's conversions. The caller
    commits.
    """
    moment = moment or now_sast()
    today = business_date(moment)
    due = _due(db, moment)
    done = Converted()
    if not due:
        return done
    site_ids = {appointment.site_id for appointment, _ in due}
    leads = _leads(db, site_ids)
    schedules = published_schedules(
        db, sorted(site_ids), from_day=today - timedelta(days=1), horizon_days=2
    )
    for appointment, slot in due:
        if slot.service_day < today:
            if _lapse(db, appointment, slot, moment):
                done.lapsed.append(appointment.id)
            continue
        if (
            stored_sast(slot.starts_at) - timedelta(minutes=leads[appointment.site_id])
            > moment
        ):
            continue
        schedule = schedules.get(appointment.site_id)
        if schedule is None:
            continue  # a clinic no longer listed: its bookings wait, and lapse at the day's end
        try:
            with db.begin_nested():
                ticket = convert_one(db, appointment, schedule=schedule, moment=moment)
        except JoinRefusedError as refused:
            done.waiting.append((appointment.id, refused.refusal))
            continue
        except BookingAlreadyConvertedError, MissingBookingPartsError:
            continue
        done.converted.append((appointment.id, ticket.id))
    return done


class MissingBookingPartsError(RuntimeError):
    """The booking's clinic, queue or patient is no longer there: nothing to convert onto."""


def lead_minutes(db: Session, site_id: str) -> int:
    """One clinic's conversion lead time in minutes, or the default where it has set none."""
    return _leads(db, {site_id})[site_id]


def convert_one(
    db: Session,
    appointment: Appointment,
    *,
    schedule: OpeningSchedule,
    moment: datetime,
) -> Ticket:
    """Turn **one** booking into its ticket through the join service, and return the ticket.

    The single conversion: the sweep calls it when the lead time comes, and the check-in tablet
    (Issue 83) calls it when the patient arrives. The caller owns the transaction, and the sweep wraps
    each call in its own savepoint so one refusal rolls back only that booking's number.

    Raises:
        JoinRefusedError: The queue cannot take the ticket (closed, or full).
        BookingAlreadyConvertedError: Another writer converted it first.
        MissingBookingPartsError: Its clinic, queue or patient has gone.
    """
    site = db.get(Site, appointment.site_id)
    queue = db.get(Queue, appointment.queue_id)
    patient = db.get(Patient, appointment.patient_id)
    if site is None or queue is None or patient is None:
        raise MissingBookingPartsError
    result = join_queue(
        db,
        site=site,
        queue=queue,
        schedule=schedule,
        source=TicketSource(appointment.source),
        patient=patient,
        actor=CONVERSION_ACTOR,
        appointment=appointment,
        moment=moment,
    )
    if not result.created:
        _convert_onto_existing(db, appointment, result.ticket.id, moment)
    return result.ticket


def _convert_onto_existing(
    db: Session, appointment: Appointment, ticket_id: str, moment: datetime
) -> None:
    """The patient already holds a ticket in that queue today: the booking is converted onto it."""
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or not mark_converted(db, appointment, ticket, moment=moment):
        raise BookingAlreadyConvertedError
