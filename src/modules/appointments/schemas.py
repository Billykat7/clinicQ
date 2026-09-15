"""Request and response models for the appointments API (Issue 80).

Times of day (``08:00``) and datetimes without an offset are **Africa/Johannesburg** wall clock, as
everywhere in this API; a datetime with an offset is read as the instant it names.
"""

from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.commons.enums import AppointmentStatus, SlotRefusal, TicketSource
from src.commons.time import stored_sast
from src.database.models.appointment_slot import (
    CONVERT_LEAD_RANGE,
    DEFAULT_CONVERT_LEAD_MINUTES,
    MAX_HORIZON_DAYS,
    MAX_MIN_LEAD_MINUTES,
    MAX_SLOT_CAPACITY,
    SLOT_MINUTES_RANGE,
)

#: The most windows one weekly template or one day may have. A clinic's week, not a timetable.
MAX_WINDOWS = 50
#: The longest block reason, and the most days one generation request may cover.
MAX_BLOCK_REASON = 280
MAX_GENERATE_DAYS = MAX_HORIZON_DAYS


class PolicyIn(BaseModel):
    """How far ahead, and how close to the time, the clinic takes bookings."""

    model_config = ConfigDict(extra="forbid")

    horizon_days: int = Field(ge=1, le=MAX_HORIZON_DAYS)
    min_lead_minutes: int = Field(ge=0, le=MAX_MIN_LEAD_MINUTES)
    convert_lead_minutes: int = Field(
        default=DEFAULT_CONVERT_LEAD_MINUTES,
        ge=CONVERT_LEAD_RANGE[0],
        le=CONVERT_LEAD_RANGE[1],
    )
    """How long before its time a booking becomes a ticket in the queue (Issue 81)."""


class PolicyOut(PolicyIn):
    """The clinic's booking policy; ``is_default`` when the manager has not set one."""

    site_id: str
    is_default: bool


class WindowFields(BaseModel):
    """A stretch of wall clock cut into slots. ``ends_at <= starts_at`` crosses midnight."""

    model_config = ConfigDict(extra="forbid")

    starts_at: time
    ends_at: time
    slot_minutes: int = Field(ge=SLOT_MINUTES_RANGE[0], le=SLOT_MINUTES_RANGE[1])
    capacity: int = Field(ge=1, le=MAX_SLOT_CAPACITY)
    service_id: str | None = None


class WeeklyWindowIn(WindowFields):
    """One window of the ordinary week."""

    weekday: int = Field(ge=0, le=6)
    """0 = Monday ... 6 = Sunday."""


class WeeklyTemplateIn(BaseModel):
    """A queue's whole week, replacing what was there. An empty list takes no bookings."""

    model_config = ConfigDict(extra="forbid")

    windows: list[WeeklyWindowIn] = Field(max_length=MAX_WINDOWS)


class WeeklyWindowOut(WeeklyWindowIn):
    """One stored window of the week."""

    id: str


class WeeklyTemplateOut(BaseModel):
    """A queue's week, ordered by weekday and start."""

    site_id: str
    queue_id: str
    windows: list[WeeklyWindowOut]


class DayOverrideIn(BaseModel):
    """One date's windows, replacing the week on that date. An empty list: no appointments that day."""

    model_config = ConfigDict(extra="forbid")

    windows: list[WindowFields] = Field(max_length=MAX_WINDOWS)


class DayOverrideOut(BaseModel):
    """One date's override; ``windows`` empty means no appointments that day."""

    site_id: str
    queue_id: str
    day: date
    windows: list[WindowFields]


class GenerateIn(BaseModel):
    """Which service days to generate slots for. Defaults: today through the clinic's horizon."""

    model_config = ConfigDict(extra="forbid")

    from_day: date | None = None
    days: int | None = Field(default=None, ge=1, le=MAX_GENERATE_DAYS)


class GenerateOut(BaseModel):
    """What generation changed. Running it again with nothing changed reports all zeros but ``kept``."""

    site_id: str
    queue_id: str
    first_day: date
    last_day: date
    #: New slots stored.
    created: int = Field(ge=0)
    #: Slots already stored and still produced.
    kept: int = Field(ge=0)
    #: Unbooked future slots the schedule no longer produces, deleted.
    removed: int = Field(ge=0)
    #: Future slots the schedule no longer produces that hold bookings: no longer offered, bookings stand.
    withdrawn: int = Field(ge=0)
    #: Withdrawn slots the schedule produces again, offered again.
    restored: int = Field(ge=0)


class BlockIn(BaseModel):
    """A range to take out of the book: a staff absence. ``queue_id`` ``None`` blocks every queue."""

    model_config = ConfigDict(extra="forbid")

    starts_at: datetime
    ends_at: datetime
    reason: str = Field(min_length=3, max_length=MAX_BLOCK_REASON)
    queue_id: str | None = None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _johannesburg(cls, value: datetime) -> datetime:
        """A time sent without an offset is Johannesburg wall clock, as the whole API documents."""
        return stored_sast(value)

    @model_validator(mode="after")
    def _ends_after_start(self) -> BlockIn:
        """A block has to cover some time."""
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at.")
        return self


class BlockOut(BaseModel):
    """One block as the API returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    site_id: str
    queue_id: str | None
    starts_at: datetime
    ends_at: datetime
    reason: str
    lifted_at: datetime | None
    created_at: datetime

    @field_validator("starts_at", "ends_at", "lifted_at", "created_at")
    @classmethod
    def _in_johannesburg(cls, value: datetime | None) -> datetime | None:
        """Stored instants, written back as Johannesburg time whatever the database handed over."""
        return None if value is None else stored_sast(value)


class BlockCreatedOut(BlockOut):
    """A new block, and how many places were already booked inside it."""

    #: Booked places in slots the block now hides. They stand: reaching those patients is Issue 81's.
    booked_places_inside: int = Field(ge=0)


class BlockListOut(BaseModel):
    """A clinic's blocks, most recent first."""

    site_id: str
    total: int = Field(ge=0)
    items: list[BlockOut]


class SlotOut(BaseModel):
    """One slot in the day view."""

    id: str
    queue_id: str
    service_id: str | None
    starts_at: datetime
    ends_at: datetime
    capacity: int
    booked: int
    #: Places a booking could take now: the slot's own, capped by the queue's day.
    remaining: int = Field(ge=0)
    #: ``None`` when the slot can be booked; otherwise why not.
    refusal: SlotRefusal | None


class QueueDayOut(BaseModel):
    """One queue's day: its limit, what walk-ins and appointments have taken, and its slots."""

    queue_id: str
    queue_name: str
    daily_limit: int | None
    tickets: int = Field(ge=0)
    appointments: int = Field(ge=0)
    day_remaining: int | None
    slots: list[SlotOut]


class DayAvailabilityOut(BaseModel):
    """What one service day looks like to somebody booking, per queue."""

    site_id: str
    day: date
    #: When this was read. Nothing is cached: a cancellation shows on the next read.
    as_of: datetime
    #: Only slots that can be booked (``refusal`` is ``None``), unless ``include_unavailable``.
    queues: list[QueueDayOut]


# --------------------------------------------------------------------------------------
# Booking (Issue 81)
# --------------------------------------------------------------------------------------


class PublicSlotOut(BaseModel):
    """A bookable time as a patient sees it: when, and whether there is room. No counts of other patients."""

    id: str
    starts_at: datetime
    ends_at: datetime
    service_id: str | None


class PublicQueueDayOut(BaseModel):
    """One queue's bookable times on a day."""

    queue_id: str
    queue_name: str
    slots: list[PublicSlotOut]


class PublicDayOut(BaseModel):
    """What a patient can book at a clinic on one service day."""

    site_id: str
    day: date
    as_of: datetime
    queues: list[PublicQueueDayOut]


class BookIn(BaseModel):
    """A booking: the time chosen."""

    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(min_length=1, max_length=36)


class RescheduleIn(BookIn):
    """A move to another time at the same clinic."""


class BookingOut(BaseModel):
    """A booking as every channel confirms it."""

    id: str
    reference: str
    """Read aloud as ``K7M-4QP``."""
    status: AppointmentStatus
    source: TicketSource
    site_id: str
    clinic: str
    queue_id: str
    queue_name: str
    starts_at: datetime
    ends_at: datetime
    booked_at: datetime
    converted_at: datetime | None
    rescheduled_from_id: str | None
    #: The ticket this booking became, once converted.
    ticket_page_url: str | None
    #: Whether the patient may still move or cancel it.
    changeable: bool
    message: str


class BookingListOut(BaseModel):
    """A patient's bookings from today on, or a clinic's on one day."""

    total: int = Field(ge=0)
    items: list[BookingOut]
