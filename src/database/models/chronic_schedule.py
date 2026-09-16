"""A repeating collection: when a patient's chronic medication is next due (Issue 85).

Chronic medication collection is the most predictable flow in primary care and the most valuable nudge
the product can send: a patient who collects on time stays on treatment, and a clinic whose collections
are spread over the week is not overwhelmed on a Monday.

One row is one patient's repeating collection in one queue:

* ``interval_days`` and ``next_due_on`` say when; the sweep reminds on the first **open** day from the
  due date, so nobody is asked to come on a day the clinic is shut;
* ``reminded_for`` and ``followed_up_for`` hold the due date each message was sent for, so a reminder
  goes **once** per cycle and a missed collection gets **exactly one** follow-up, never a nag;
* ``join_token`` is what the reminder's own button or reply carries, so taking a place is one
  interaction with no sign-in;
* collecting rolls the schedule forward from the day it happened, not from the day it was due, so a
  patient who comes three days late is next due 28 days after that, as a clinic counts it;
* ``stopped_at`` ends it. The row stays, because the collections it explains stay.

Datetimes are aware; ``next_due_on`` and the rest are service days in Africa/Johannesburg.
"""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value

#: The usual cycle in South African primary care: a month's supply, counted in days.
DEFAULT_INTERVAL_DAYS = 28
#: The shortest and longest repeat a clinic may set: a week, and half a year.
MIN_INTERVAL_DAYS = 7
MAX_INTERVAL_DAYS = 180
#: How long after the due day a collection is still "on time" before the one follow-up goes.
DEFAULT_GRACE_DAYS = 3
MAX_GRACE_DAYS = 30


class ChronicSchedule(Base, TimestampMixin):
    """One patient's repeating collection at one clinic: when it is due, and what has been sent."""

    __tablename__ = "chronic_schedule"
    __table_args__ = (
        UniqueConstraint(
            "patient_id", "queue_id", name="uq_chronic_schedule_patient_queue"
        ),
        Index("uq_chronic_schedule_join_token", "join_token", unique=True),
        Index("ix_clinicq_chronic_schedule_due", "next_due_on", "stopped_at"),
        Index("ix_clinicq_chronic_schedule_site", "site_id", "stopped_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"), nullable=False
    )
    """The collection queue the reminder's join action takes a place in."""
    patient_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="CASCADE"),
        nullable=False,
    )
    service: Mapped[str | None] = mapped_column(String(60), nullable=True)
    """What is being collected, in the clinic's own words ("ARV refill"); shown to staff, never sent."""
    interval_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_INTERVAL_DAYS
    )
    next_due_on: Mapped[date] = mapped_column(Date, nullable=False)
    """The service day the next collection is due. Rolled forward by a collection or a missed cycle."""
    grace_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_GRACE_DAYS
    )
    """How many days after the due day a collection still counts as made, before the one follow-up."""
    last_collected_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    reminded_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    """The due date the reminder was last sent for: one reminder per cycle, however often the sweep runs."""
    followed_up_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    """The due date the follow-up was last sent for: **exactly one** per missed cycle."""
    join_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """The secret the reminder's join button or reply carries. Unguessable, and one per schedule."""
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the schedule was stopped. Nothing is sent from that moment; the row stays for the record."""
    stopped_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Who stopped it: a staff member's email, or ``patient`` when the patient asked."""
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """The staff member who set it up. No foreign key: the schedule outlives their account."""

    @property
    def is_active(self) -> bool:
        """Whether the sweep still sends anything for this schedule."""
        return self.stopped_at is None

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures: ids and the due date, never a name."""
        return (
            f"ChronicSchedule(patient_id={self.patient_id!r}, queue_id={self.queue_id!r}, "
            f"next_due_on={self.next_due_on!r})"
        )
