"""South Africa's public holidays are computed, and the computation is checked (Issue 24).

The dataset is a rule, not a list, so what has to be right is the rule: Easter, the twelve
proclaimed days, and section 2(1) of the Public Holidays Act 36 of 1994 — whenever a public holiday
falls on a **Sunday**, the following Monday is one too.

The fixed points below (Easter Sunday for four separate years) are the published dates, so this
catches an arithmetic slip in the Gregorian algorithm rather than restating it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from scripts.db.holidays import (
    FIXED_HOLIDAYS,
    OBSERVED_SUFFIX,
    easter_sunday,
    holidays_between,
    holidays_for,
)

#: Published Easter Sundays, four years apart in the cycle so one wrong constant cannot pass.
KNOWN_EASTERS = {
    2024: date(2024, 3, 31),
    2025: date(2025, 4, 20),
    2026: date(2026, 4, 5),
    2027: date(2027, 3, 28),
    2030: date(2030, 4, 21),
}


@pytest.mark.parametrize(("year", "expected"), sorted(KNOWN_EASTERS.items()))
def test_easter_matches_the_published_date(year: int, expected: date) -> None:
    """The Gregorian algorithm, checked against dates anyone can look up."""
    assert easter_sunday(year) == expected


@pytest.mark.parametrize("year", sorted(KNOWN_EASTERS))
def test_good_friday_and_family_day_sit_either_side_of_easter(year: int) -> None:
    """Good Friday is the Friday before, Family Day the Monday after: both derived, never typed."""
    by_name = {holiday.name: holiday.day for holiday in holidays_for(year)}
    assert by_name["Good Friday"] == easter_sunday(year) - timedelta(days=2)
    assert by_name["Family Day"] == easter_sunday(year) + timedelta(days=1)
    assert by_name["Good Friday"].weekday() == 4
    assert by_name["Family Day"].weekday() == 0


@pytest.mark.parametrize("year", sorted(KNOWN_EASTERS))
def test_every_proclaimed_holiday_is_present_exactly_once(year: int) -> None:
    """The ten fixed days plus the two Easter ones, before the Sunday rule adds any Monday."""
    proclaimed = [
        holiday for holiday in holidays_for(year) if holiday.observed_for is None
    ]
    assert len(proclaimed) == len(FIXED_HOLIDAYS) + 2
    assert len({holiday.day for holiday in proclaimed}) == len(proclaimed)


def test_a_holiday_on_a_sunday_earns_the_monday_after_it() -> None:
    """Section 2(1). In 2026, Women's Day is a Sunday, so the 10th is a holiday as well."""
    womens_day = date(2026, 8, 9)
    assert womens_day.weekday() == 6

    by_day = {holiday.day: holiday for holiday in holidays_for(2026)}

    assert womens_day in by_day
    monday = by_day[date(2026, 8, 10)]
    assert monday.observed_for == "National Women's Day"
    assert monday.name.endswith(OBSERVED_SUFFIX)


def test_a_holiday_on_a_saturday_earns_nothing() -> None:
    """The rule is Sundays only: Christmas 2027 is a Saturday and stands alone."""
    christmas = date(2027, 12, 25)
    assert christmas.weekday() == 5

    days = {holiday.day for holiday in holidays_for(2027)}

    assert christmas in days
    assert (
        date(2027, 12, 27) in days
    )  # but the 26th *is* a Sunday, so this one is observed
    assert date(2027, 12, 28) not in days


@pytest.mark.parametrize("year", sorted(KNOWN_EASTERS))
def test_the_calendar_is_in_date_order_with_no_duplicates(year: int) -> None:
    """Every consumer reads this in order, so the order is part of the contract."""
    days = [holiday.day for holiday in holidays_for(year)]
    assert days == sorted(days)
    assert len(days) == len(set(days))
    assert all(day.year == year for day in days)


def test_a_multi_year_range_is_the_years_concatenated_in_order() -> None:
    """What the seed writes: this year and next, one list, still sorted."""
    two_years = holidays_between(2026, 2027)
    assert two_years == tuple(
        sorted(
            [*holidays_for(2026), *holidays_for(2027)],
            key=lambda holiday: holiday.day,
        )
    )
