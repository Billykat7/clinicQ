"""Opening hours, holidays and closures, in Johannesburg and in that order (Issue 24).

Pure functions of their inputs, so unit tests with the moment pinned. What is proven here:

* the **precedence** — a closure beats a holiday rule, a holiday rule beats the weekly schedule;
* **midnight**, from both sides: a span that runs 22:00 to 02:00 covers 00:30 *the next morning*,
  and it does so in ``Africa/Johannesburg``, which is where the bug would otherwise live;
* a lunch break and a split shift, which are the same shape;
* ``is_open_now`` and ``next_open_at`` never disagree, because one is defined in terms of the other.
"""

from __future__ import annotations

import os
import time as time_module
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.commons.time import APP_TIMEZONE, stored_sast
from src.modules.sites.hours import (
    ClosedPeriod,
    OpeningSchedule,
    TimeSpan,
    is_open_now,
    next_open_at,
    open_periods,
    open_state,
)

MONDAY, TUESDAY, WEDNESDAY = 0, 1, 2
#: Monday 14 September 2026. Every moment below is derived from it, so the weekday is never guessed.
_MONDAY = date(2026, 9, 14)


def sast(day: date, hour: int, minute: int = 0) -> datetime:
    """An aware ``Africa/Johannesburg`` moment."""
    return datetime.combine(day, time(hour, minute), tzinfo=APP_TIMEZONE)


def _office_hours() -> OpeningSchedule:
    """Weekdays 07:00 to 16:00, with a 12:30 to 13:00 lunch break: two spans a day."""
    spans = (TimeSpan(time(7, 0), time(12, 30)), TimeSpan(time(13, 0), time(16, 0)))
    return OpeningSchedule(weekly=dict.fromkeys(range(MONDAY, 5), spans))


# --- the ordinary week ------------------------------------------------------------------


def test_a_clinic_is_open_inside_its_hours_and_shut_outside_them() -> None:
    """The base case, before any holiday or closure is involved."""
    schedule = _office_hours()
    assert is_open_now(schedule, sast(_MONDAY, 9))
    assert not is_open_now(schedule, sast(_MONDAY, 6, 59))
    assert not is_open_now(schedule, sast(_MONDAY, 16, 1))


def test_the_lunch_break_is_a_gap_and_next_open_at_names_its_end() -> None:
    """Two spans a day means shut at 12:45, and the next opening is 13:00 the same day."""
    schedule = _office_hours()
    midday = sast(_MONDAY, 12, 45)
    assert not is_open_now(schedule, midday)
    assert next_open_at(schedule, midday) == sast(_MONDAY, 13, 0)


def test_a_weekend_clinic_opens_again_on_monday() -> None:
    """Saturday and Sunday have no spans at all, so the answer skips to the next weekday."""
    saturday = _MONDAY + timedelta(days=5)
    assert next_open_at(_office_hours(), sast(saturday, 10)) == sast(
        _MONDAY + timedelta(days=7), 7, 0
    )


def test_a_clinic_with_no_hours_at_all_never_opens_rather_than_always_being_open() -> (
    None
):
    """An empty schedule fails closed: a clinic nobody configured is not open all week."""
    empty = OpeningSchedule()
    assert not is_open_now(empty, sast(_MONDAY, 9))
    assert next_open_at(empty, sast(_MONDAY, 9)) is None


# --- midnight, which is the case that goes wrong everywhere else -------------------------


def _after_hours() -> OpeningSchedule:
    """An after-hours service: Mondays 22:00 through to 02:00 on Tuesday. One span, six hours."""
    return OpeningSchedule(weekly={MONDAY: (TimeSpan(time(22, 0), time(2, 0)),)})


def test_a_span_that_crosses_midnight_still_covers_the_early_hours_of_the_next_day() -> (
    None
):
    """00:30 on Tuesday is inside Monday's 22:00-02:00 span, and the clinic is open.

    This is the assertion Issue 24 names: a schedule read a day at a time answers "closed" here,
    because Tuesday has no spans of its own and the one that matters started yesterday.
    """
    tuesday = _MONDAY + timedelta(days=1)
    schedule = _after_hours()

    assert is_open_now(schedule, sast(_MONDAY, 23, 30))
    assert is_open_now(schedule, sast(tuesday, 0, 30))
    assert is_open_now(schedule, sast(tuesday, 1, 59))
    assert not is_open_now(schedule, sast(tuesday, 2, 0))
    assert not is_open_now(schedule, sast(tuesday, 9, 0))


def test_midnight_is_johannesburg_midnight_and_not_the_servers() -> None:
    """The same instant, expressed in UTC, gives the same answer: the zone is the schedule's.

    22:30 UTC **is** 00:30 the next day in Johannesburg. A comparison that took the server's clock
    zone would call this Monday evening and be right by accident on a laptop and wrong in the
    container, which is the failure this test exists to catch.
    """
    tuesday = _MONDAY + timedelta(days=1)
    johannesburg = sast(tuesday, 0, 30)
    same_instant_in_utc = datetime(2026, 9, 14, 22, 30, tzinfo=ZoneInfo("UTC"))

    assert johannesburg == same_instant_in_utc
    assert is_open_now(_after_hours(), same_instant_in_utc)
    # And the boundary is the Johannesburg one: 02:00 SAST is 00:00 UTC, when it shuts.
    assert not is_open_now(
        _after_hours(), datetime(2026, 9, 15, 0, 0, tzinfo=ZoneInfo("UTC"))
    )


def test_next_open_at_is_reported_in_johannesburg_whatever_zone_was_asked_in() -> None:
    """A caller in UTC still gets the wall clock a patient is told: 07:00 SAST."""
    asked_in_utc = datetime(2026, 9, 14, 3, 0, tzinfo=ZoneInfo("UTC"))  # 05:00 SAST
    opening = next_open_at(_office_hours(), asked_in_utc)
    assert opening is not None
    assert opening.tzinfo is APP_TIMEZONE
    assert opening.strftime("%H:%M") == "07:00"


# --- the precedence ----------------------------------------------------------------------


def test_a_public_holiday_shuts_a_clinic_that_would_otherwise_be_open() -> None:
    """A holiday with no rule of the clinic's own means closed: the safe default."""
    schedule = OpeningSchedule(weekly=_office_hours().weekly, holidays={_MONDAY: ()})
    assert not is_open_now(schedule, sast(_MONDAY, 9))
    assert next_open_at(schedule, sast(_MONDAY, 9)) == sast(
        _MONDAY + timedelta(days=1), 7, 0
    )


def test_a_clinic_that_works_holidays_says_so_and_its_rule_replaces_the_weekly_hours() -> (
    None
):
    """The rule is the day's hours, not an addition to them: a short holiday shift shuts at 11:00."""
    schedule = OpeningSchedule(
        weekly=_office_hours().weekly,
        holidays={_MONDAY: (TimeSpan(time(8, 0), time(11, 0)),)},
    )
    assert not is_open_now(
        schedule, sast(_MONDAY, 7, 30)
    )  # the weekly 07:00 does not apply
    assert is_open_now(schedule, sast(_MONDAY, 9))
    assert not is_open_now(schedule, sast(_MONDAY, 14))  # nor the weekly afternoon


def test_an_adhoc_closure_beats_a_holiday_rule_which_beats_the_weekly_schedule() -> (
    None
):
    """All three levels in one schedule, checked at one moment each: the precedence, end to end."""
    holiday_hours = (TimeSpan(time(8, 0), time(11, 0)),)
    schedule = OpeningSchedule(
        weekly=_office_hours().weekly,
        holidays={_MONDAY: holiday_hours},
        closures=(
            ClosedPeriod(
                starts_at=sast(_MONDAY, 9),
                ends_at=sast(_MONDAY, 10),
                reason="The water is off.",
            ),
        ),
    )
    assert is_open_now(schedule, sast(_MONDAY, 8, 30))  # holiday rule, no closure
    assert not is_open_now(schedule, sast(_MONDAY, 9, 30))  # closure wins
    assert is_open_now(schedule, sast(_MONDAY, 10, 30))  # closure over, rule again
    assert not is_open_now(schedule, sast(_MONDAY, 13))  # weekly hours do not apply


def test_a_closure_cuts_a_hole_in_the_day_rather_than_ending_it() -> None:
    """An afternoon closure leaves the morning open, and the clinic reopens when it ends."""
    schedule = OpeningSchedule(
        weekly=_office_hours().weekly,
        closures=(
            ClosedPeriod(sast(_MONDAY, 11), sast(_MONDAY, 14), "Power failure."),
        ),
    )
    assert is_open_now(schedule, sast(_MONDAY, 10))
    assert not is_open_now(schedule, sast(_MONDAY, 11, 30))
    assert next_open_at(schedule, sast(_MONDAY, 11, 30)) == sast(_MONDAY, 14)
    assert is_open_now(schedule, sast(_MONDAY, 15))


def test_an_open_ended_closure_keeps_the_clinic_shut_past_the_horizon() -> None:
    """ "Until further notice" means there is no next opening to report, not a guess at one."""
    schedule = OpeningSchedule(
        weekly=_office_hours().weekly,
        closures=(ClosedPeriod(sast(_MONDAY, 11), None, "Flood damage."),),
    )
    assert not is_open_now(schedule, sast(_MONDAY, 12))
    assert next_open_at(schedule, sast(_MONDAY, 12)) is None
    assert open_periods(schedule, sast(_MONDAY, 12)) == []


def test_the_reason_travels_with_the_answer_so_a_screen_can_say_why() -> None:
    """Discovery shows "closed: the water is off", not a bare "closed" (Issue 32 renders this)."""
    schedule = OpeningSchedule(
        weekly=_office_hours().weekly,
        closures=(
            ClosedPeriod(sast(_MONDAY, 9), sast(_MONDAY, 12), "The water is off."),
        ),
    )
    state = open_state(schedule, sast(_MONDAY, 10))
    assert state.is_open is False
    assert state.closure_reason == "The water is off."
    # The closure ends at 12:00 and the morning span runs to 12:30, so half an hour of the
    # morning survives it: the clinic reopens then, not after lunch.
    assert state.next_open_at == sast(_MONDAY, 12)


def test_a_closure_that_has_not_started_yet_does_not_shut_the_clinic_now() -> None:
    """A manager scheduling this afternoon's closure at nine o'clock keeps the morning open."""
    schedule = OpeningSchedule(
        weekly=_office_hours().weekly,
        closures=(ClosedPeriod(sast(_MONDAY, 13), sast(_MONDAY, 16), "Stock take."),),
    )
    assert is_open_now(schedule, sast(_MONDAY, 9))
    assert open_state(schedule, sast(_MONDAY, 9)).closure_reason is None


# --- the two functions agree ---------------------------------------------------------------


@pytest.mark.parametrize("hour", range(24))
def test_is_open_now_and_next_open_at_never_disagree(hour: int) -> None:
    """``next_open_at(s, m) == m`` is exactly ``is_open_now(s, m)``, at every hour of a real day."""
    schedule = OpeningSchedule(
        weekly=_office_hours().weekly,
        holidays={_MONDAY + timedelta(days=2): ()},
        closures=(
            ClosedPeriod(sast(_MONDAY, 11), sast(_MONDAY, 14), "Power failure."),
        ),
    )
    moment = sast(_MONDAY, hour)
    assert is_open_now(schedule, moment) == (next_open_at(schedule, moment) == moment)


# --- reading a stored closure back, in whatever zone the server runs in ------------------------


def test_a_stored_closure_is_read_as_the_johannesburg_instant_it_was_written_as() -> (
    None
):
    """The regression CI found and a SAST laptop could not.

    SQLite has no ``timestamptz``: it hands a business datetime back **naive**, with the offset
    gone. ``value.astimezone(APP_TIMEZONE)`` then reads that naive value as the **server's** clock
    zone, so a closure written at 17:02 SAST resolves as 17:02 SAST on a developer's machine and as
    19:02 SAST in a UTC container — two hours in the future, which is why
    ``test_a_closure_shows_up_in_the_open_endpoint_with_its_reason`` passed locally and failed in
    CI with ``assert None == 'The water is off.'``.

    :func:`~src.commons.time.stored_sast` is the fix: everything this application writes is aware
    SAST, so a naive value coming back out of its own storage is read as SAST.
    """
    written = sast(_MONDAY, 17, 2)
    as_sqlite_returns_it = written.replace(tzinfo=None)

    assert stored_sast(as_sqlite_returns_it) == written
    assert stored_sast(written) == written
    # And the same instant arriving from PostgreSQL in UTC reads as the same moment.
    assert stored_sast(written.astimezone(ZoneInfo("UTC"))) == written


def test_the_whole_closure_path_holds_in_a_utc_process() -> None:
    """The failure end to end, with the process clock zone actually set to CI's.

    ``time_module.tzset()`` is what makes this a real reproduction rather than a restatement: the naive
    datetime is interpreted against the process zone, so this test genuinely fails against the old
    code and passes against the new one, on any machine.
    """
    written = sast(_MONDAY, 17, 2)
    naive = written.replace(tzinfo=None)
    before = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "UTC"
        time_module.tzset()
        # What the old code did, and what it produced: two hours into the future.
        assert naive.astimezone(APP_TIMEZONE) == written + timedelta(hours=2)
        # What the schedule loader does now.
        schedule = OpeningSchedule(
            weekly=dict.fromkeys(range(7), (TimeSpan(time(0, 0), time(0, 0)),)),
            closures=(ClosedPeriod(stored_sast(naive), None, "The water is off."),),
        )
        state = open_state(schedule, sast(_MONDAY, 17, 30))
        assert state.is_open is False
        assert state.closure_reason == "The water is off."
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time_module.tzset()
