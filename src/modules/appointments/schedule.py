"""Which appointment slots a queue offers: pure functions of its windows and the clinic's hours (Issue 80).

A slot plan is a queue's **ordinary week** (weekly windows) and its **day overrides** (one date's own
windows, replacing the week on that date). Turning a plan into slots asks the clinic's opening
schedule (Issue 24) one question per slot, "is the clinic open for the whole of it?", so a public
holiday, an announced closure and the hours outside the clinic's day all remove slots the same way
they stop a walk-in join. Nothing here reads the database or the clock: the tests pin a Monday at
23:00 in Johannesburg and assert the slots, whatever zone the machine running them keeps.

**Midnight.** A window whose end is at or before its start crosses midnight, as an opening span does
(:class:`~src.modules.sites.hours.TimeSpan`). Its slots after midnight belong to the **next** service
day, because a patient booked for 00:30 on Tuesday is part of Tuesday's queue. Planning a range of
days therefore starts one day early, so Monday's 23:00 to 01:00 window still gives Tuesday its 00:00
and 00:30 slots.

**Adjacent open spans are one stretch.** A clinic open 00:00 to 00:00 every day has one span per day,
touching end to start at midnight. A slot from 23:30 to 00:30 is inside the clinic's hours, so spans
that touch are merged before a slot is checked against them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from src.commons.time import APP_TIMEZONE, business_date
from src.modules.sites.hours import OpeningSchedule, TimeSpan, open_periods


@dataclass(frozen=True, slots=True)
class SlotWindow:
    """A stretch of wall clock cut into equal slots: "08:00 to 11:00, 15 minutes, two patients each"."""

    span: TimeSpan
    slot_minutes: int
    capacity: int
    service_id: str | None = None

    def slots_starting(self, day: date) -> list[PlannedSlot]:
        """The slots this window gives when it starts on ``day``, in order.

        A slot that would run past the window's end is not made: a 50-minute window cut into
        15-minute slots gives three, and the last 5 minutes are not a slot.
        """
        start, end = self.span.on(day)
        length = timedelta(minutes=self.slot_minutes)
        slots: list[PlannedSlot] = []
        cursor = start
        while cursor + length <= end:
            slots.append(
                PlannedSlot(
                    starts_at=cursor,
                    ends_at=cursor + length,
                    capacity=self.capacity,
                    service_id=self.service_id,
                )
            )
            cursor += length
        return slots


@dataclass(frozen=True, slots=True)
class PlannedSlot:
    """One slot the plan produces, before it is stored."""

    starts_at: datetime
    ends_at: datetime
    capacity: int
    service_id: str | None = None

    @property
    def service_day(self) -> date:
        """The Johannesburg date the slot starts on: the day whose limit it counts against."""
        return business_date(self.starts_at)


@dataclass(frozen=True, slots=True)
class SlotPlan:
    """A queue's appointment windows: the week, and the dates that replace it."""

    #: ``{weekday: windows}``, Monday 0 to Sunday 6.
    weekly: Mapping[int, tuple[SlotWindow, ...]] = field(default_factory=dict)
    #: ``{date: windows}``. A date present with **no** windows has no appointments that day.
    overrides: Mapping[date, tuple[SlotWindow, ...]] = field(default_factory=dict)

    def windows_on(self, day: date) -> tuple[SlotWindow, ...]:
        """The windows that start on ``day``: the override when the date has one, else the week's."""
        if day in self.overrides:
            return tuple(self.overrides[day])
        return tuple(self.weekly.get(day.weekday(), ()))


def merge_touching(
    periods: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Join periods that overlap or touch end to start into one, sorted."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(periods):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def within(
    starts_at: datetime, ends_at: datetime, periods: Sequence[tuple[datetime, datetime]]
) -> bool:
    """Whether ``[starts_at, ends_at)`` lies wholly inside one of ``periods``."""
    return any(start <= starts_at and ends_at <= end for start, end in periods)


def open_stretches(
    opening: OpeningSchedule, first_day: date, last_day: date
) -> list[tuple[datetime, datetime]]:
    """The clinic's genuinely open stretches covering ``first_day`` to ``last_day``, merged.

    Read from :func:`~src.modules.sites.hours.open_periods`, which applies holidays and closures, from
    the start of the day before ``first_day`` (a window that began then may cross into it) to the end
    of the day after ``last_day``.
    """
    moment = datetime.combine(
        first_day - timedelta(days=1), datetime.min.time(), tzinfo=APP_TIMEZONE
    )
    horizon = (last_day - first_day).days + 3
    return merge_touching(open_periods(opening, moment, horizon_days=horizon))


def plan_slots(
    plan: SlotPlan, opening: OpeningSchedule, first_day: date, last_day: date
) -> list[PlannedSlot]:
    """Every slot the plan gives for the service days ``first_day`` to ``last_day``, in time order.

    A slot is kept only when the clinic is open for the whole of it (weekly hours, holiday rules and
    closures, through the opening schedule), and when its service day is in the range. Two windows
    that produce the same start give one slot, the first window's, so an overlapping template cannot
    store a time twice.

    Args:
        plan: The queue's windows.
        opening: The clinic's opening schedule, loaded for at least the same range.
        first_day: The first service day, inclusive.
        last_day: The last service day, inclusive.

    Returns:
        The planned slots, sorted by start.
    """
    if last_day < first_day:
        return []
    stretches = open_stretches(opening, first_day, last_day)
    seen: set[datetime] = set()
    planned: list[PlannedSlot] = []
    day = first_day - timedelta(days=1)
    while day <= last_day:
        for window in plan.windows_on(day):
            for slot in window.slots_starting(day):
                if not first_day <= slot.service_day <= last_day:
                    continue
                if slot.starts_at in seen:
                    continue
                if not within(slot.starts_at, slot.ends_at, stretches):
                    continue
                seen.add(slot.starts_at)
                planned.append(slot)
        day += timedelta(days=1)
    return sorted(planned, key=lambda slot: slot.starts_at)
