"""Which slots a queue's windows give, against the clinic's hours (Issue 80).

Pure functions, so every case pins its own moment in ``Africa/Johannesburg`` and the answers do not
depend on the zone of the machine running them (CI runs in UTC).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from src.commons.time import APP_TIMEZONE
from src.modules.appointments.schedule import (
    SlotPlan,
    SlotWindow,
    merge_touching,
    plan_slots,
)
from src.modules.sites.hours import ClosedPeriod, OpeningSchedule, TimeSpan

#: Monday 5 October 2026 to Saturday 31 October 2026 contain no public holiday; 24 September is
#: Heritage Day (a Thursday) and 16 December is Day of Reconciliation (a Wednesday).
HERITAGE_DAY = date(2026, 9, 24)
MONDAY = date(2026, 10, 5)

#: Weekdays 07:00 to 16:00.
WEEKDAY_HOURS = OpeningSchedule(
    weekly={weekday: (TimeSpan(time(7), time(16)),) for weekday in range(5)}
)
#: Open around the clock, every day: one 00:00 to 00:00 span per day.
ALWAYS_OPEN = OpeningSchedule(
    weekly={weekday: (TimeSpan(time(0), time(0)),) for weekday in range(7)}
)


def _sast(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=APP_TIMEZONE)


def _window(
    starts: time, ends: time, minutes: int = 30, capacity: int = 2
) -> SlotWindow:
    return SlotWindow(
        span=TimeSpan(starts, ends), slot_minutes=minutes, capacity=capacity
    )


def test_a_month_of_slots_from_a_weekly_template_skips_the_public_holiday() -> None:
    """Every Thursday 08:00 to 10:00 in September 2026 gives four slots a week, except Heritage Day."""
    plan = SlotPlan(weekly={3: (_window(time(8), time(10)),)})
    opening = OpeningSchedule(weekly=WEEKDAY_HOURS.weekly, holidays={HERITAGE_DAY: ()})

    slots = plan_slots(plan, opening, date(2026, 9, 1), date(2026, 9, 30))

    thursdays = sorted({slot.service_day for slot in slots})
    assert thursdays == [date(2026, 9, 3), date(2026, 9, 10), date(2026, 9, 17)]
    assert len(slots) == 3 * 4
    assert all(slot.starts_at.tzinfo is APP_TIMEZONE for slot in slots)
    assert [slot.starts_at.time() for slot in slots[:4]] == [
        time(8),
        time(8, 30),
        time(9),
        time(9, 30),
    ]


def test_a_holiday_the_clinic_opens_on_keeps_its_slots_within_the_holiday_hours() -> (
    None
):
    """A holiday rule opening 08:00 to 09:00 leaves only the two slots inside those hours."""
    plan = SlotPlan(weekly={3: (_window(time(8), time(10)),)})
    opening = OpeningSchedule(
        weekly=WEEKDAY_HOURS.weekly,
        holidays={HERITAGE_DAY: (TimeSpan(time(8), time(9)),)},
    )

    slots = plan_slots(plan, opening, HERITAGE_DAY, HERITAGE_DAY)

    assert [slot.starts_at for slot in slots] == [
        _sast(HERITAGE_DAY, time(8)),
        _sast(HERITAGE_DAY, time(8, 30)),
    ]


def test_an_announced_closure_removes_the_slots_it_overlaps() -> None:
    """A closure from 09:15 to noon removes the 09:00 slot (partly closed) and everything after."""
    plan = SlotPlan(weekly={0: (_window(time(8), time(11)),)})
    opening = OpeningSchedule(
        weekly=WEEKDAY_HOURS.weekly,
        closures=(
            ClosedPeriod(
                starts_at=_sast(MONDAY, time(9, 15)),
                ends_at=_sast(MONDAY, time(12)),
                reason="The water is off",
            ),
        ),
    )

    slots = plan_slots(plan, opening, MONDAY, MONDAY)

    assert [slot.starts_at.time() for slot in slots] == [time(8), time(8, 30)]


def test_slots_outside_the_clinic_hours_are_not_made() -> None:
    """A window from 15:00 to 18:00 at a clinic that closes at 16:00 gives only 15:00 and 15:30."""
    plan = SlotPlan(weekly={0: (_window(time(15), time(18)),)})

    slots = plan_slots(plan, WEEKDAY_HOURS, MONDAY, MONDAY)

    assert [slot.starts_at.time() for slot in slots] == [time(15), time(15, 30)]


def test_a_window_crossing_midnight_gives_the_next_day_its_slots() -> None:
    """Monday 23:00 to 01:00: 23:00 and 23:30 are Monday's, 00:00 and 00:30 are Tuesday's."""
    plan = SlotPlan(weekly={0: (_window(time(23), time(1)),)})
    tuesday = MONDAY + timedelta(days=1)

    both = plan_slots(plan, ALWAYS_OPEN, MONDAY, tuesday)
    only_tuesday = plan_slots(plan, ALWAYS_OPEN, tuesday, tuesday)

    assert [(slot.starts_at, slot.service_day) for slot in both] == [
        (_sast(MONDAY, time(23)), MONDAY),
        (_sast(MONDAY, time(23, 30)), MONDAY),
        (_sast(tuesday, time(0)), tuesday),
        (_sast(tuesday, time(0, 30)), tuesday),
    ]
    # Planning Tuesday alone still finds the window that started on Monday.
    assert [slot.starts_at for slot in only_tuesday] == [
        _sast(tuesday, time(0)),
        _sast(tuesday, time(0, 30)),
    ]
    # 00:00 SAST is 22:00 UTC the day before: the service day is the Johannesburg date, not UTC's.
    assert only_tuesday[0].starts_at.astimezone(UTC).date() == MONDAY


def test_a_slot_straddling_midnight_counts_as_open_at_a_24_hour_clinic() -> None:
    """23:45 to 00:15 crosses the join between two 00:00 to 00:00 spans, which touch: one stretch."""
    plan = SlotPlan(weekly={0: (_window(time(23, 45), time(0, 15)),)})

    slots = plan_slots(plan, ALWAYS_OPEN, MONDAY, MONDAY)

    assert [(slot.starts_at, slot.ends_at) for slot in slots] == [
        (_sast(MONDAY, time(23, 45)), _sast(MONDAY + timedelta(days=1), time(0, 15)))
    ]


def test_a_day_override_replaces_the_week_and_an_empty_one_takes_no_bookings() -> None:
    """Monday's override gives 10:00 to 11:00; the next Monday's empty override gives nothing."""
    next_monday = MONDAY + timedelta(days=7)
    plan = SlotPlan(
        weekly={0: (_window(time(8), time(9)),)},
        overrides={MONDAY: (_window(time(10), time(11), minutes=60),), next_monday: ()},
    )

    slots = plan_slots(plan, WEEKDAY_HOURS, MONDAY, next_monday)

    assert [slot.starts_at for slot in slots] == [_sast(MONDAY, time(10))]


def test_a_slot_that_would_run_past_the_window_is_not_made_and_overlaps_store_once() -> (
    None
):
    """50 minutes cut into 15 gives three; a second window starting at the same time adds nothing."""
    plan = SlotPlan(
        weekly={
            0: (
                _window(time(8), time(8, 50), minutes=15, capacity=3),
                _window(time(8), time(8, 30), minutes=15, capacity=9),
            )
        }
    )

    slots = plan_slots(plan, WEEKDAY_HOURS, MONDAY, MONDAY)

    assert [slot.starts_at.time() for slot in slots] == [
        time(8),
        time(8, 15),
        time(8, 30),
    ]
    assert {slot.capacity for slot in slots} == {3}


def test_touching_periods_merge_and_separate_ones_do_not() -> None:
    """End-to-start periods are one stretch; a gap keeps them apart."""
    first = (_sast(MONDAY, time(0)), _sast(MONDAY, time(12)))
    touching = (_sast(MONDAY, time(12)), _sast(MONDAY, time(16)))
    apart = (_sast(MONDAY, time(17)), _sast(MONDAY, time(18)))

    assert merge_touching([apart, touching, first]) == [
        (first[0], touching[1]),
        apart,
    ]


def test_an_empty_or_inverted_range_plans_nothing() -> None:
    """Nothing to plan is an empty list, not an error."""
    plan = SlotPlan(weekly={0: (_window(time(8), time(9)),)})

    assert plan_slots(plan, WEEKDAY_HOURS, MONDAY, MONDAY - timedelta(days=1)) == []
