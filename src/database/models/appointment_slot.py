"""Bookable appointment times, and the capacity they share with walk-ins (Issue 80).

A walk-in queue makes a chronic patient stand in line for a repeat script they could have booked. An
appointment gives them a time instead, but a room still sees only so many patients in a day, so
**appointments and walk-ins draw from one daily limit**: the queue's ``max_daily_capacity``
(Issue 25). Seven tables hold that, and their shapes are the rules:

* :class:`AppointmentPolicy`: how far ahead a clinic takes bookings, and how late. One row per clinic;
  a clinic without one gets :data:`DEFAULT_HORIZON_DAYS` and :data:`DEFAULT_MIN_LEAD_MINUTES`.
* :class:`AppointmentTemplateWindow`: the ordinary week. "Mondays, 08:00 to 11:00, 15-minute slots,
  two patients each." A window may cross midnight (``ends_at <= starts_at``), as opening hours may.
* :class:`AppointmentDayOverride`: one date's own windows, replacing the week's on that date. A row
  with no times means "no appointments that day".
* :class:`AppointmentSlot`: one generated time. ``booked_count <= capacity`` is a **check
  constraint**, and places are taken with one conditional ``UPDATE`` (Issue 39's pattern), so a slot
  cannot be overbooked whatever the application does.
* :class:`AppointmentBlock`: a manager's "Dr Naidoo is away on the 14th". Slots inside a block stop
  being offered while it stands, and come back if it is lifted.
* :class:`Appointment`: one place held in one slot by one patient. Issue 81 adds rescheduling and the
  conversion into a ticket; this issue writes the row and frees it.
* :class:`QueueCapacityDay`: the row a walk-in join and a booking both **lock** before counting a
  queue's day, so the two cannot jointly pass the limit (:mod:`src.modules.appointments.capacity`).

Times are ``timestamptz``, written as aware ``Africa/Johannesburg`` instants. ``service_day`` is the
Johannesburg date a slot starts on, so a 00:30 slot belongs to the day it starts on and counts against
that day's limit, as a ticket issued at 00:30 does.
"""

from datetime import date, datetime, time

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import AppointmentStatus, DbSchema, TicketSource
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value

#: What a clinic gets before its manager sets a policy: four weeks ahead, which covers a monthly
#: chronic collection, and no booking less than an hour before the time.
DEFAULT_HORIZON_DAYS = 28
DEFAULT_MIN_LEAD_MINUTES = 60
#: The furthest ahead a clinic may take bookings. Past a year the clinic's hours are a guess.
MAX_HORIZON_DAYS = 365
#: The longest minimum lead time: a week.
MAX_MIN_LEAD_MINUTES = 7 * 24 * 60
#: The range a slot's length may be set in, in minutes.
SLOT_MINUTES_RANGE = (5, 240)
#: The most patients one slot may hold. A group session, not a waiting room.
MAX_SLOT_CAPACITY = 50
#: How long before its time a booking becomes a ticket, by default and at most (Issue 81).
DEFAULT_CONVERT_LEAD_MINUTES = 30
CONVERT_LEAD_RANGE = (5, 240)


class AppointmentPolicy(Base, TimestampMixin):
    """How far ahead, and how close to the time, one clinic takes bookings."""

    __tablename__ = "appointment_policy"
    __table_args__ = (
        CheckConstraint(
            f"horizon_days BETWEEN 1 AND {MAX_HORIZON_DAYS}", name="horizon_days_range"
        ),
        CheckConstraint(
            f"min_lead_minutes BETWEEN 0 AND {MAX_MIN_LEAD_MINUTES}",
            name="min_lead_minutes_range",
        ),
        CheckConstraint(
            f"convert_lead_minutes BETWEEN {CONVERT_LEAD_RANGE[0]} AND {CONVERT_LEAD_RANGE[1]}",
            name="convert_lead_minutes_range",
        ),
    )

    site_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"),
        primary_key=True,
    )
    horizon_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_HORIZON_DAYS
    )
    """The last bookable service day is today plus this many days."""
    min_lead_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_MIN_LEAD_MINUTES
    )
    """A slot starting sooner than this is no longer offered."""
    convert_lead_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_CONVERT_LEAD_MINUTES,
        server_default=str(DEFAULT_CONVERT_LEAD_MINUTES),
    )
    """How long before its time a booking becomes a ticket in the queue (Issue 81)."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"AppointmentPolicy(site_id={self.site_id!r}, horizon_days={self.horizon_days}, "
            f"min_lead_minutes={self.min_lead_minutes})"
        )


class AppointmentTemplateWindow(Base, TimestampMixin):
    """One stretch of a queue's ordinary week that is cut into bookable slots."""

    __tablename__ = "appointment_template_window"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint(
            f"slot_minutes BETWEEN {SLOT_MINUTES_RANGE[0]} AND {SLOT_MINUTES_RANGE[1]}",
            name="slot_minutes_range",
        ),
        CheckConstraint(
            f"capacity BETWEEN 1 AND {MAX_SLOT_CAPACITY}", name="capacity_range"
        ),
        Index("ix_clinicq_appointment_template_window_queue", "queue_id", "weekday"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.clinic_service.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The service these slots are for (Issue 26), or ``None`` for whatever the queue sees."""
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    """0 = Monday ... 6 = Sunday, as :meth:`datetime.date.weekday` numbers them."""
    starts_at: Mapped[time] = mapped_column(Time, nullable=False)
    ends_at: Mapped[time] = mapped_column(Time, nullable=False)
    """Wall clock in ``Africa/Johannesburg``. ``ends_at <= starts_at`` crosses midnight."""
    slot_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    """How long each slot is."""
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    """How many patients each slot holds."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"AppointmentTemplateWindow(queue_id={self.queue_id!r}, weekday={self.weekday}, "
            f"{self.starts_at}-{self.ends_at})"
        )


class AppointmentDayOverride(Base, TimestampMixin):
    """One date's windows for one queue, replacing the ordinary week on that date.

    A date with any override row uses only its override rows. A row with no times is the way to say
    "no appointments on this date" while the week says otherwise.
    """

    __tablename__ = "appointment_day_override"
    __table_args__ = (
        CheckConstraint(
            "(starts_at IS NULL) = (ends_at IS NULL) "
            "AND (starts_at IS NULL) = (slot_minutes IS NULL) "
            "AND (starts_at IS NULL) = (capacity IS NULL)",
            name="window_complete",
        ),
        CheckConstraint(
            f"slot_minutes IS NULL OR slot_minutes BETWEEN {SLOT_MINUTES_RANGE[0]} "
            f"AND {SLOT_MINUTES_RANGE[1]}",
            name="slot_minutes_range",
        ),
        CheckConstraint(
            f"capacity IS NULL OR capacity BETWEEN 1 AND {MAX_SLOT_CAPACITY}",
            name="capacity_range",
        ),
        Index("ix_clinicq_appointment_day_override_queue_day", "queue_id", "day"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.clinic_service.id", ondelete="SET NULL"),
        nullable=True,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    """The Johannesburg date the window starts on."""
    starts_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    ends_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    slot_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"AppointmentDayOverride(queue_id={self.queue_id!r}, day={self.day})"


class AppointmentSlot(Base, TimestampMixin):
    """One bookable time in one queue, and how many of its places are taken."""

    __tablename__ = "appointment_slot"
    __table_args__ = (
        # The guard this issue exists for: a slot is never booked past its capacity.
        CheckConstraint("booked_count <= capacity", name="not_overbooked"),
        CheckConstraint("booked_count >= 0", name="booked_count_not_negative"),
        CheckConstraint(
            f"capacity BETWEEN 1 AND {MAX_SLOT_CAPACITY}", name="capacity_range"
        ),
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        # Generating twice makes one slot, not two.
        UniqueConstraint(
            "queue_id", "starts_at", name="uq_appointment_slot_queue_start"
        ),
        # The per-day view and the capacity count read this.
        Index("ix_clinicq_appointment_slot_site_day", "site_id", "service_day"),
        Index("ix_clinicq_appointment_slot_queue_day", "queue_id", "service_day"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.clinic_service.id", ondelete="SET NULL"),
        nullable=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service_day: Mapped[date] = mapped_column(Date, nullable=False)
    """The Johannesburg date ``starts_at`` falls on: the day whose limit this slot counts against."""
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    booked_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    """Places held. Changed only by one conditional ``UPDATE`` in the capacity module."""
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """Set when the schedule stops producing a slot that already has bookings: it is no longer
    offered, and its bookings stand. Cleared if the schedule produces it again."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"AppointmentSlot(queue_id={self.queue_id!r}, starts_at={self.starts_at}, "
            f"{self.booked_count}/{self.capacity})"
        )


class AppointmentBlock(Base, TimestampMixin):
    """A range a manager has taken out of the book: a staff absence, a training morning.

    ``queue_id`` ``None`` blocks every queue at the clinic. The row is kept after it is lifted, so
    "why could nobody book on the 14th" stays answerable.
    """

    __tablename__ = "appointment_block"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        Index("ix_clinicq_appointment_block_site_starts", "site_id", "starts_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"), nullable=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    """The manager's words, for staff. Not shown to patients: an absence is the clinic's business."""
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.user.id", ondelete="RESTRICT"), nullable=True
    )
    lifted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"AppointmentBlock(site_id={self.site_id!r}, queue_id={self.queue_id!r}, "
            f"{self.starts_at}-{self.ends_at})"
        )


class Appointment(Base, TimestampMixin):
    """One place in one slot, held for one patient."""

    __tablename__ = "appointment"
    __table_args__ = (
        Index("ix_clinicq_appointment_slot_status", "slot_id", "status"),
        Index("ix_clinicq_appointment_patient", "patient_id"),
        # A booking is read aloud at the desk and typed into a menu by this (Issue 81).
        UniqueConstraint("reference", name="uq_appointment_reference"),
        # The conversion sweep reads what is still booked.
        Index("ix_clinicq_appointment_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"), nullable=False
    )
    slot_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.appointment_slot.id", ondelete="RESTRICT"),
        nullable=False,
    )
    patient_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AppointmentStatus.BOOKED.value
    )
    """:class:`~src.commons.enums.AppointmentStatus`."""
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reference: Mapped[str] = mapped_column(String(6), nullable=False)
    """Six unambiguous characters (the ticket reference alphabet), read aloud or typed on a menu (Issue 81)."""
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TicketSource.WEB.value, server_default="web"
    )
    """:class:`~src.commons.enums.TicketSource` the booking came through; its ticket records the same."""
    rescheduled_from_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.appointment.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The booking this one replaced, when it was a move to another time."""
    converted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the booking became a ticket; the ticket names this booking (``ticket.appointment_id``)."""
    lapsed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"Appointment(id={self.id!r}, slot_id={self.slot_id!r}, status={self.status!r})"


class QueueCapacityDay(Base):
    """The lock row for one queue's day: what a walk-in join and a booking both hold while counting.

    It carries no figures on purpose. The count is always read live (tickets plus held appointment
    places), so there is nothing to drift; the row exists only so a day that has no ticket yet (a
    booking three weeks out) still has something to lock.
    """

    __tablename__ = "queue_capacity_day"

    queue_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"),
        primary_key=True,
    )
    service_day: Mapped[date] = mapped_column(Date, primary_key=True)

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"QueueCapacityDay(queue_id={self.queue_id!r}, service_day={self.service_day})"
