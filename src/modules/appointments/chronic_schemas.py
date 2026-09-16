"""What the collection routes take and answer (Issue 85).

A schedule is the clinic's own record of a patient's repeat, so its answers carry the patient's name as
the desk knows it and never their number; the patient's own answer is the one thing they asked for, a
place in the queue.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from src.database.models.chronic_schedule import (
    DEFAULT_GRACE_DAYS,
    DEFAULT_INTERVAL_DAYS,
    MAX_GRACE_DAYS,
    MAX_INTERVAL_DAYS,
    MIN_INTERVAL_DAYS,
)


class ScheduleIn(BaseModel):
    """A repeating collection, as the front desk sets it up."""

    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=6, max_length=32)
    """The patient's number, as the desk types it. Their record is created on first use."""
    queue_id: str = Field(min_length=1, max_length=36)
    next_due_on: date
    interval_days: int = Field(
        default=DEFAULT_INTERVAL_DAYS, ge=MIN_INTERVAL_DAYS, le=MAX_INTERVAL_DAYS
    )
    grace_days: int = Field(default=DEFAULT_GRACE_DAYS, ge=0, le=MAX_GRACE_DAYS)
    service: str | None = Field(default=None, max_length=60)
    """What is being collected, in the clinic's words. Staff-facing; never in a message."""


class ScheduleOut(BaseModel):
    """One repeating collection as the clinic's screen shows it."""

    id: str
    patient_id: str
    patient_phone: str = Field(
        description="The patient's number, masked: +27 ** *** 0851. Never their name."
    )
    queue_id: str
    queue_name: str
    service: str | None
    interval_days: int
    grace_days: int
    next_due_on: date
    last_collected_on: date | None
    reminded_for: date | None = Field(
        description="The due date the reminder was sent for; one per cycle."
    )
    followed_up_for: date | None = Field(
        description="The due date the one follow-up was sent for."
    )
    active: bool


class ScheduleListOut(BaseModel):
    """This clinic's repeating collections, soonest due first."""

    site_id: str
    total: int
    items: list[ScheduleOut]


class CollectionJoinOut(BaseModel):
    """What a patient is told when their reminder's join action takes their place."""

    number: str
    queue_name: str
    room_label: str | None
    waiting_ahead: int
    already: bool = Field(description="True when they already held a place today.")
    message: str


class AdherenceOut(BaseModel):
    """One collection queue's adherence over a range of days."""

    queue_id: str
    queue_name: str
    schedules: int
    due: int
    collected_on_time: int
    collected_late: int
    missed: int


class AdherenceReportOut(BaseModel):
    """How many collections happen on time at one clinic (Issue 90 reads this)."""

    site_id: str
    start: date
    end: date
    queues: list[AdherenceOut]
