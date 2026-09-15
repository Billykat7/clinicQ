"""Whether a slot is on offer, and what one day looks like to somebody booking (Issue 80).

:func:`refusal_for` is the rule, as a pure function of the slot, the clinic's policy, its blocks, its
opening schedule and the moment. The per-day view and the booking path (Issue 81) both ask it, so a
slot the view hides is a slot a booking refuses, for the same reason.

The rule's order is the order a patient would want to be told in: a time the clinic is not taking
(closed, blocked, withdrawn) before a time that is merely too soon or too far, before one that is full.
Fullness is the database's to decide at the moment of booking
(:func:`~src.modules.appointments.capacity.claim_place`); the view shows the places left as of the
read, and a cancellation shows up on the next read, because nothing is cached.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src.commons.enums import SlotRefusal
from src.commons.time import business_date, stored_sast
from src.database.models import AppointmentBlock, AppointmentSlot
from src.modules.appointments.schedule import within


@dataclass(frozen=True, slots=True)
class BookingPolicy:
    """How far ahead, and how late, a clinic takes bookings."""

    horizon_days: int
    min_lead_minutes: int

    def last_bookable_day(self, moment: datetime) -> date:
        """The furthest service day a booking made at ``moment`` may be for."""
        return business_date(moment) + timedelta(days=self.horizon_days)

    def earliest_start(self, moment: datetime) -> datetime:
        """The earliest a slot booked at ``moment`` may start."""
        return moment + timedelta(minutes=self.min_lead_minutes)


@dataclass(frozen=True, slots=True)
class BlockedRange:
    """A range out of the book, as the rule sees it: which queue (``None`` for all) and when."""

    queue_id: str | None
    starts_at: datetime
    ends_at: datetime

    @classmethod
    def of(cls, row: AppointmentBlock) -> BlockedRange:
        """The rule's view of a stored block."""
        return cls(
            queue_id=row.queue_id,
            starts_at=stored_sast(row.starts_at),
            ends_at=stored_sast(row.ends_at),
        )

    def covers(self, queue_id: str, starts_at: datetime, ends_at: datetime) -> bool:
        """Whether this block overlaps ``[starts_at, ends_at)`` on ``queue_id``."""
        if self.queue_id is not None and self.queue_id != queue_id:
            return False
        return self.starts_at < ends_at and starts_at < self.ends_at


@dataclass(frozen=True, slots=True)
class OfferContext:
    """Everything :func:`refusal_for` needs about one clinic, loaded once for a day or a booking."""

    policy: BookingPolicy
    blocks: Sequence[BlockedRange]
    moment: datetime
    #: The clinic's open stretches around the days asked about, merged
    #: (:func:`~src.modules.appointments.schedule.open_stretches`).
    stretches: Sequence[tuple[datetime, datetime]]


def refusal_for(slot: AppointmentSlot, context: OfferContext) -> SlotRefusal | None:
    """Why ``slot`` is not on offer at ``context.moment``, or ``None`` when it is.

    Fullness is not checked here (see the module docstring), except that a slot with no place left is
    reported ``SLOT_FULL`` so the view and a booking say the same thing.
    """
    starts_at = stored_sast(slot.starts_at)
    ends_at = stored_sast(slot.ends_at)
    if slot.withdrawn_at is not None:
        return SlotRefusal.WITHDRAWN
    if any(block.covers(slot.queue_id, starts_at, ends_at) for block in context.blocks):
        return SlotRefusal.BLOCKED
    if not within(starts_at, ends_at, context.stretches):
        return SlotRefusal.CLOSED
    if starts_at < context.policy.earliest_start(context.moment):
        return SlotRefusal.TOO_SOON
    if slot.service_day > context.policy.last_bookable_day(context.moment):
        return SlotRefusal.TOO_FAR
    if slot.booked_count >= slot.capacity:
        return SlotRefusal.SLOT_FULL
    return None
