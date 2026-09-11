"""Business time helpers (Issue 4): SAST in, SAST out, and the service day turns at SAST midnight.

Unit tests, because this is the essential rule the queue's daily numbering rests on
(``.cursor/rules/testing-strategy.mdc``): a service day that turned at UTC midnight would restart
every queue's numbers at 02:00 in the middle of the night shift.
"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from src.commons.time import (
    APP_TIMEZONE,
    business_date,
    business_day_bounds,
    now_sast,
    to_sast,
)
from src.core import s3_logging

SAST = timedelta(hours=2)


def test_now_sast_is_aware_and_in_johannesburg() -> None:
    """``now_sast()`` is timezone-aware, in the app zone, at UTC+02:00."""
    now = now_sast()
    assert now.tzinfo is APP_TIMEZONE
    assert now.utcoffset() == SAST
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=5)


def test_there_is_one_app_timezone() -> None:
    """The kernel's import path and the new one are the same object, not two definitions."""
    assert s3_logging.APP_TIMEZONE is APP_TIMEZONE
    assert APP_TIMEZONE.key == "Africa/Johannesburg"


def test_to_sast_keeps_the_instant_and_changes_the_wall_clock() -> None:
    """Converting a UTC value to SAST is the same instant, two hours later on the clock."""
    utc_value = datetime(2026, 9, 11, 6, 30, tzinfo=UTC)
    converted = to_sast(utc_value)
    assert converted == utc_value
    assert (converted.hour, converted.minute) == (8, 30)
    assert converted.tzinfo is APP_TIMEZONE


def test_to_sast_accepts_any_aware_zone() -> None:
    """A fixed-offset value (as a client might send) converts as well."""
    lagos = datetime(2026, 9, 11, 7, 0, tzinfo=timezone(timedelta(hours=1)))
    assert to_sast(lagos).hour == 8


def test_to_sast_refuses_a_naive_datetime() -> None:
    """A naive value says nothing about which instant it is, so it is rejected, not guessed."""
    with pytest.raises(ValueError, match="naive"):
        to_sast(datetime(2026, 9, 11, 8, 0))  # noqa: DTZ001 - the naive input is the test


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        # 23:30 UTC on the 10th is 01:30 SAST on the 11th: already the next service day.
        (datetime(2026, 9, 10, 23, 30, tzinfo=UTC), date(2026, 9, 11)),
        # 21:59 UTC is 23:59 SAST: still the same day.
        (datetime(2026, 9, 10, 21, 59, tzinfo=UTC), date(2026, 9, 10)),
        # 22:00 UTC is SAST midnight exactly: the new day starts.
        (datetime(2026, 9, 10, 22, 0, tzinfo=UTC), date(2026, 9, 11)),
    ],
)
def test_the_service_day_turns_at_sast_midnight(
    instant: datetime, expected: date
) -> None:
    """``business_date`` reads the Johannesburg calendar, not the value's own zone."""
    assert business_date(instant) == expected


def test_business_date_defaults_to_today_in_johannesburg() -> None:
    """With no argument it is today's service day."""
    assert business_date() == now_sast().date()


def test_business_date_refuses_a_naive_datetime() -> None:
    """The same refusal as :func:`to_sast`."""
    with pytest.raises(ValueError, match="naive"):
        business_date(datetime(2026, 9, 11, 8, 0))  # noqa: DTZ001 - the naive input is the test


def test_business_day_bounds_are_half_open_sast_midnights() -> None:
    """``[start, end)`` runs SAST midnight to SAST midnight: 22:00 UTC the evening before."""
    start, end = business_day_bounds(date(2026, 9, 11))
    assert start == datetime(2026, 9, 10, 22, 0, tzinfo=UTC)
    assert end == datetime(2026, 9, 11, 22, 0, tzinfo=UTC)
    assert end - start == timedelta(days=1)
    assert start.tzinfo is APP_TIMEZONE


def test_every_instant_of_a_service_day_falls_inside_its_bounds() -> None:
    """The first and last instants of a day map back to it; the next instant does not."""
    day = date(2026, 9, 11)
    start, end = business_day_bounds(day)
    assert business_date(start) == day
    assert business_date(end - timedelta(microseconds=1)) == day
    assert business_date(end) == day + timedelta(days=1)
