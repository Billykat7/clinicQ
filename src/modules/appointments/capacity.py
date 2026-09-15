"""One daily limit per queue, shared by walk-ins and appointments, guarded by the database (Issue 80).

A room sees so many patients a day. The queue's ``max_daily_capacity`` (Issue 25) is that number, and
it has to hold whether a place is taken by somebody at the front desk this morning or by somebody
who booked three weeks ago. **This module is the only place the two are counted together**: the join
service (Issue 40) asks :func:`day_is_over` after issuing a ticket, and :func:`claim_place` asks it
before taking a place in a slot.

**What counts.** :func:`day_usage` is the day's tickets that hold a place (every one not cancelled)
plus the appointment places still held (every booking not cancelled). A cancelled ticket or booking
frees its place at once, because the count is read live: there is no stored figure to drift.

**Why the counting is safe.** A count is only as good as the moment it is read in, so every count is
taken while holding one row's lock, the queue's :class:`~src.database.models.QueueCapacityDay` for
that day, taken by :func:`lock_day`'s single ``INSERT … ON CONFLICT DO UPDATE``. A walk-in and a
booking for the same queue and day wait for each other; the one behind reads what the first
committed. Different queues, or different days, never wait. The lock order is fixed, so nothing can
deadlock:

* a join takes its ticket counter (:func:`~src.modules.queue.sequence.allocate_sequence`), then the day;
* a booking takes the day, then the slot's row in :func:`claim_place`'s conditional ``UPDATE``.

**Why a slot cannot be overbooked.** The place itself is taken with one statement::

    UPDATE appointment_slot SET booked_count = booked_count + 1
    WHERE id = :slot AND booked_count < capacity AND withdrawn_at IS NULL

On PostgreSQL a second transaction blocked on that row re-checks the ``WHERE`` against the committed
row when the first finishes, so the last place goes to exactly one of them and the other updates
nothing and is told the slot is full. Behind it, ``ck_appointment_slot_not_overbooked`` refuses
``booked_count > capacity`` whatever writes it, as ``uq_ticket_queue_id_service_day_sequence`` refuses
a repeated ticket number (Issue 39). Both are proved on a real PostgreSQL in
``tests/integration/appointments/test_capacity_concurrency.py``.

On SQLite (the unit and HTTP suites) there are no row locks and a single writer, so the same
statements run and the concurrency claims are left to the PostgreSQL tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.commons.enums import AppointmentStatus, SlotRefusal, TicketStatus
from src.commons.exceptions import ConflictError
from src.commons.time import now_sast
from src.database.models import (
    Appointment,
    AppointmentSlot,
    Queue,
    QueueCapacityDay,
    Ticket,
)

#: What a patient is told for each reason a place cannot be taken.
REFUSAL_MESSAGES: Final[dict[SlotRefusal, str]] = {
    SlotRefusal.SLOT_FULL: "That time is fully booked. Please choose another time.",
    SlotRefusal.DAY_FULL: (
        "This queue is full on that day. Please choose another day, or another queue."
    ),
    SlotRefusal.BLOCKED: "That time is not available. Please choose another time.",
    SlotRefusal.WITHDRAWN: "That time is no longer offered. Please choose another time.",
    SlotRefusal.CLOSED: "The clinic is closed at that time. Please choose another time.",
    SlotRefusal.TOO_SOON: (
        "That time is too soon to book. Please choose a later time, or join the queue."
    ),
    SlotRefusal.TOO_FAR: "That day is too far ahead to book yet. Please choose an earlier day.",
}


class SlotRefusedError(ConflictError):
    """A place in a slot cannot be taken: HTTP 409 with ``appointments.slot.<reason>``."""

    def __init__(self, refusal: SlotRefusal) -> None:
        super().__init__(
            REFUSAL_MESSAGES[refusal], code=f"appointments.slot.{refusal.value}"
        )
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class DayUsage:
    """How much of one queue's day is taken, and by whom."""

    #: Tickets that hold a place: every one that day not cancelled.
    tickets: int
    #: Appointment places still held that day.
    appointments: int
    #: The queue's daily limit, or ``None`` for no limit.
    limit: int | None

    @property
    def taken(self) -> int:
        """Every place taken, by walk-ins, remote joins and appointments together."""
        return self.tickets + self.appointments

    @property
    def remaining(self) -> int | None:
        """Places left that day, never below zero; ``None`` when the queue has no limit."""
        return None if self.limit is None else max(self.limit - self.taken, 0)


def lock_day(db: Session, queue_id: str, service_day: date) -> None:
    """Take the lock on one queue's day, creating its row the first time, until the transaction ends.

    One statement for both cases (``INSERT … ON CONFLICT DO UPDATE``). The update changes nothing; it
    is there because it is what makes PostgreSQL lock an existing row, and it waits behind a
    concurrent insert of the same key rather than failing.
    """
    values = {"queue_id": queue_id, "service_day": service_day}
    conflict = [QueueCapacityDay.queue_id, QueueCapacityDay.service_day]
    touch = {"service_day": service_day}
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            postgresql_insert(QueueCapacityDay)
            .values(**values)
            .on_conflict_do_update(index_elements=conflict, set_=touch)
        )
        return
    db.execute(
        sqlite_insert(QueueCapacityDay)
        .values(**values)
        .on_conflict_do_update(index_elements=conflict, set_=touch)
    )


def day_usage(db: Session, queue: Queue, service_day: date) -> DayUsage:
    """Count one queue's day: tickets holding a place, and appointment places held.

    Reads counts only, narrowed to a queue the caller already resolved through the site guard. Call
    it under :func:`lock_day` when the answer decides a write.
    """
    tickets = db.execute(
        select(func.count(Ticket.id)).where(
            Ticket.queue_id == queue.id,
            Ticket.service_day == service_day,
            Ticket.status != TicketStatus.CANCELLED.value,
        )
    ).scalar_one()
    appointments = db.execute(
        select(func.count(Appointment.id))
        .join(AppointmentSlot, AppointmentSlot.id == Appointment.slot_id)
        .where(
            AppointmentSlot.queue_id == queue.id,
            AppointmentSlot.service_day == service_day,
            Appointment.status == AppointmentStatus.BOOKED.value,
        )
    ).scalar_one()
    return DayUsage(
        tickets=int(tickets),
        appointments=int(appointments),
        limit=queue.max_daily_capacity,
    )


def day_is_over(db: Session, queue: Queue, service_day: date) -> bool:
    """Whether the queue's day now holds more than its limit. For the join service, after it issues.

    Takes the day's lock first, so a booking for the same day cannot slip in between this count and
    the join's commit. A queue with no limit is never over, and takes no lock.
    """
    if queue.max_daily_capacity is None:
        return False
    lock_day(db, queue.id, service_day)
    usage = day_usage(db, queue, service_day)
    return usage.taken > queue.max_daily_capacity


def claim_place(
    db: Session,
    *,
    slot: AppointmentSlot,
    queue: Queue,
    patient_id: str,
    moment: datetime | None = None,
) -> Appointment:
    """Take one place in ``slot`` for a patient, or refuse. The caller commits.

    The storage primitive behind booking (Issue 81 builds the booking flow on it, as Issue 40 built
    joining on Issue 39's :func:`~src.modules.queue.sequence.issue_ticket`). It decides only what the
    database has to: whether the queue's day and the slot still have room. Whether the slot is on
    offer at all (a block, the clinic's hours, the lead time and horizon) is
    :func:`src.modules.appointments.availability.refusal_for`'s question, which the caller asks first.

    Args:
        db: The session; the lock, the update and the insert join its transaction.
        slot: The slot, read by the caller through the site guard.
        queue: The slot's queue.
        patient_id: The patient the place is held for.
        moment: When the booking is made (aware); ``None`` means now.

    Returns:
        The flushed :class:`~src.database.models.Appointment`.

    Raises:
        SlotRefusedError: ``DAY_FULL``, ``SLOT_FULL`` or ``WITHDRAWN``.
        ValueError: The slot is not this queue's; a programming error in the caller.
    """
    if slot.queue_id != queue.id:
        raise ValueError("The slot does not belong to this queue.")
    moment = moment or now_sast()
    lock_day(db, queue.id, slot.service_day)
    limit = queue.max_daily_capacity
    if limit is not None and day_usage(db, queue, slot.service_day).taken >= limit:
        raise SlotRefusedError(SlotRefusal.DAY_FULL)
    taken = db.execute(
        update(AppointmentSlot)
        .where(
            AppointmentSlot.id == slot.id,
            AppointmentSlot.booked_count < AppointmentSlot.capacity,
            AppointmentSlot.withdrawn_at.is_(None),
        )
        .values(booked_count=AppointmentSlot.booked_count + 1)
        .execution_options(synchronize_session=False)
    )
    if int(getattr(taken, "rowcount", 0) or 0) != 1:
        db.refresh(slot)
        raise SlotRefusedError(
            SlotRefusal.WITHDRAWN
            if slot.withdrawn_at is not None
            else SlotRefusal.SLOT_FULL
        )
    appointment = Appointment(
        site_id=slot.site_id,
        queue_id=queue.id,
        slot_id=slot.id,
        patient_id=patient_id,
        status=AppointmentStatus.BOOKED.value,
        booked_at=moment,
    )
    db.add(appointment)
    db.flush()
    db.refresh(slot)
    return appointment


def release_place(
    db: Session, appointment: Appointment, *, moment: datetime | None = None
) -> bool:
    """Give a booked place back: the booking is cancelled and the slot has the place free at once.

    Idempotent: releasing a booking that is not ``BOOKED`` changes nothing and returns ``False``. The
    status moves with a conditional ``UPDATE`` so two releases of one booking cannot both decrement
    the slot. The caller commits.
    """
    moment = moment or now_sast()
    moved = db.execute(
        update(Appointment)
        .where(
            Appointment.id == appointment.id,
            Appointment.status == AppointmentStatus.BOOKED.value,
        )
        .values(status=AppointmentStatus.CANCELLED.value, cancelled_at=moment)
        .execution_options(synchronize_session=False)
    )
    if int(getattr(moved, "rowcount", 0) or 0) != 1:
        return False
    db.execute(
        update(AppointmentSlot)
        .where(AppointmentSlot.id == appointment.slot_id)
        .values(booked_count=AppointmentSlot.booked_count - 1)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    db.refresh(appointment)
    return True
