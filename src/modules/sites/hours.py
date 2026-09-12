"""Is this clinic open right now, and if not, when does it open? (Issue 24)

A clinic that shows as open when its doors are locked sends a patient on a trip for nothing, so
there is exactly **one** answer to that question and every surface reads it: discovery (Issue 32),
the board, the four channel menus, and the join gate below.

**The precedence is fixed, and it is the shape of this module**::

    an ad-hoc closure   beats   a public-holiday rule   beats   the weekly schedule

A closure is the manager saying "not today, the water is off", and nothing outranks that.

**The comparison functions are pure.** :func:`is_open_now` and :func:`next_open_at` take an
:class:`OpeningSchedule` and a moment and touch nothing else — no session, no clock, no settings.
That is what lets the tests pin a Tuesday at 00:30 in Johannesburg and assert the answer, including
the case that used to be wrong everywhere: a span that **crosses midnight**.

**Every comparison is in ``Africa/Johannesburg``** (:mod:`src.commons.time`). Opening hours are wall
clock — "we open at seven" means seven in Johannesburg whatever zone the server keeps — so the
spans are wall-clock times and they are combined with a date *in the application zone* to make the
instants that are then compared.

The module is in two halves, separated below: the pure logic first, then the loader that builds a
schedule from the database.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.time import APP_TIMEZONE, business_date, now_sast, stored_sast
from src.core.site_scope import (
    SiteAccess,
    publicly_visible_site_clauses,
    published_select,
    scoped_select,
)
from src.database.models import (
    PublicHoliday,
    Site,
    SiteClosure,
    SiteHolidayRule,
    SiteOpeningHours,
)

#: How far ahead :func:`next_open_at` will look before answering "not within the horizon". Two
#: weeks covers a clinic closed over a long weekend and one whose only session is a weekly clinic;
#: past that, "we do not know" is a more honest answer than a date nobody will act on.
DEFAULT_HORIZON_DAYS = 14

#: One full day, used where a span that opens and closes at the same time means "all day".
_ONE_DAY = timedelta(days=1)


# --------------------------------------------------------------------------------------
# The pure half: values, and two functions of their inputs
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimeSpan:
    """A stretch of wall clock: ``07:00`` to ``12:30``, in ``Africa/Johannesburg``.

    ``closes_at <= opens_at`` means the span **crosses midnight**: 22:00 to 02:00 is one span and
    six hours long, not a negative one. Equal times mean the whole twenty-four hours, which is what
    a casualty unit that never closes looks like.
    """

    opens_at: time
    closes_at: time

    def on(self, day: date) -> tuple[datetime, datetime]:
        """Return this span as the concrete ``(start, end)`` instants it covers starting on ``day``.

        Both are aware and in ``Africa/Johannesburg``. The end rolls onto the next day whenever the
        span crosses midnight, which is the single place that case is handled.
        """
        start = datetime.combine(day, self.opens_at, tzinfo=APP_TIMEZONE)
        end = datetime.combine(day, self.closes_at, tzinfo=APP_TIMEZONE)
        if self.closes_at <= self.opens_at:
            end += _ONE_DAY
        return start, end


@dataclass(frozen=True, slots=True)
class ClosedPeriod:
    """An ad-hoc closure as the logic sees it: a window and the manager's reason.

    ``ends_at`` is ``None`` for "until further notice", which stays closed until somebody lifts it.
    """

    starts_at: datetime
    ends_at: datetime | None
    reason: str

    def covers(self, moment: datetime) -> bool:
        """Whether ``moment`` falls inside this closure."""
        return self.starts_at <= moment and (
            self.ends_at is None or moment < self.ends_at
        )


@dataclass(frozen=True, slots=True)
class OpeningSchedule:
    """Everything needed to answer "open?" for one clinic, and nothing else.

    Built from the database by :func:`schedule_for`, or by hand in a test. The three fields are the
    three levels of precedence, narrowest last.
    """

    #: ``{weekday: spans}``, Monday 0 to Sunday 6. A weekday absent, or mapped to no spans, is a day
    #: the clinic does not open.
    weekly: Mapping[int, tuple[TimeSpan, ...]] = field(default_factory=dict)
    #: ``{holiday date: spans}``. A date present with **no** spans is a holiday the clinic is closed
    #: on — which is what a public holiday means when the clinic has written no rule for it.
    holidays: Mapping[date, tuple[TimeSpan, ...]] = field(default_factory=dict)
    #: Ad-hoc closures, in any order. These beat everything above.
    closures: tuple[ClosedPeriod, ...] = ()

    def spans_on(self, day: date) -> tuple[TimeSpan, ...]:
        """The spans that apply on ``day`` before closures are considered.

        The holiday rule wins over the weekly one: a clinic open every Thursday is shut on a
        Thursday that is Heritage Day, unless it wrote a rule saying otherwise.
        """
        if day in self.holidays:
            return tuple(self.holidays[day])
        return tuple(self.weekly.get(day.weekday(), ()))


@dataclass(frozen=True, slots=True)
class OpenState:
    """The whole answer, for a surface that wants to render it in one go.

    ``closure_reason`` is set only when an ad-hoc closure is what is keeping the clinic shut, so
    discovery can say "closed: the water is off, opens 07:00 tomorrow" rather than just "closed".
    """

    is_open: bool
    #: The next instant the clinic is open, at or after the moment asked about. ``None`` when it
    #: does not open again within the horizon.
    next_open_at: datetime | None
    closure_reason: str | None = None


def _open_intervals(
    schedule: OpeningSchedule, first_day: date, days: int
) -> Iterator[tuple[datetime, datetime]]:
    """Yield the ``(start, end)`` instants the schedule opens for, ignoring closures.

    Starts a day early on purpose: a span that began yesterday at 22:00 and runs to 02:00 is what
    covers 00:30 today, and forgetting it is the midnight bug this module exists to prevent.
    """
    for offset in range(-1, days + 1):
        day = first_day + timedelta(days=offset)
        for span in schedule.spans_on(day):
            yield span.on(day)


def _minus_closures(
    interval: tuple[datetime, datetime], closures: Iterable[ClosedPeriod]
) -> list[tuple[datetime, datetime]]:
    """Cut every closure out of one open interval, returning what is left (possibly nothing)."""
    pieces = [interval]
    for closure in closures:
        remaining: list[tuple[datetime, datetime]] = []
        end_of_closure = closure.ends_at
        for start, end in pieces:
            if end <= closure.starts_at or (
                end_of_closure is not None and end_of_closure <= start
            ):
                remaining.append((start, end))  # no overlap
                continue
            if start < closure.starts_at:
                remaining.append((start, closure.starts_at))
            if end_of_closure is not None and end_of_closure < end:
                remaining.append((end_of_closure, end))
        pieces = remaining
    return pieces


def open_periods(
    schedule: OpeningSchedule,
    moment: datetime | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[tuple[datetime, datetime]]:
    """Every stretch the clinic is genuinely open for, from ``moment`` to the horizon, in order.

    The one place the precedence is applied: the weekly and holiday rules produce the intervals,
    and the closures are cut out of them. Everything else in this module reads the result.

    Args:
        schedule: The clinic's rules.
        moment: Aware datetime; ``None`` means now in Johannesburg.
        horizon_days: How far ahead to look.

    Returns:
        ``(start, end)`` pairs, sorted, clipped so nothing ends before ``moment``.
    """
    moment = moment or now_sast()
    periods = [
        piece
        for interval in _open_intervals(schedule, business_date(moment), horizon_days)
        for piece in _minus_closures(interval, schedule.closures)
        if piece[1] > moment and piece[0] < piece[1]
    ]
    return sorted(periods)


def is_open_now(schedule: OpeningSchedule, moment: datetime | None = None) -> bool:
    """Whether the clinic is open at ``moment`` (now, by default).

    Pure: the same schedule and the same moment always give the same answer, whatever the server's
    clock zone or the caller's session.
    """
    moment = moment or now_sast()
    return any(start <= moment < end for start, end in open_periods(schedule, moment))


def next_open_at(
    schedule: OpeningSchedule,
    moment: datetime | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> datetime | None:
    """The earliest instant at or after ``moment`` when the clinic is open, or ``None``.

    "At or after" rather than "after" on purpose: it makes
    ``next_open_at(s, m) == m`` exactly equivalent to ``is_open_now(s, m)``, so a caller cannot end
    up with a screen that says "open" and "opens at" disagreeing. ``None`` means it does not open
    again within ``horizon_days`` — a clinic closed until further notice, or one with no hours set.
    """
    moment = moment or now_sast()
    for start, end in open_periods(schedule, moment, horizon_days=horizon_days):
        if start <= moment < end:
            return moment
        if start > moment:
            return start
    return None


def open_state(
    schedule: OpeningSchedule,
    moment: datetime | None = None,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> OpenState:
    """The whole answer at ``moment``: open or not, when next, and why not if a closure is why."""
    moment = moment or now_sast()
    upcoming = next_open_at(schedule, moment, horizon_days=horizon_days)
    closure = next(
        (period for period in schedule.closures if period.covers(moment)), None
    )
    return OpenState(
        is_open=upcoming == moment,
        next_open_at=upcoming,
        closure_reason=closure.reason if closure is not None else None,
    )


# --------------------------------------------------------------------------------------
# The loading half: one clinic's rules, read through the site guard
# --------------------------------------------------------------------------------------


def _weekly(rows: Sequence[SiteOpeningHours]) -> dict[int, tuple[TimeSpan, ...]]:
    """Group opening-hours rows into ``{weekday: spans}``, each day's spans in clock order."""
    by_day: dict[int, list[TimeSpan]] = {}
    for row in rows:
        by_day.setdefault(row.weekday, []).append(
            TimeSpan(opens_at=row.opens_at, closes_at=row.closes_at)
        )
    return {
        weekday: tuple(sorted(spans, key=lambda span: span.opens_at))
        for weekday, spans in by_day.items()
    }


def _window(from_day: date | None, horizon_days: int) -> tuple[date, date]:
    """The ``[start, end)`` dates holidays and holiday rules are loaded for.

    A day earlier than ``from_day``, because a span that crosses midnight is anchored on the day it
    started.
    """
    first = from_day or business_date()
    return first - timedelta(days=1), first + timedelta(days=horizon_days + 1)


def _public_holiday_dates(
    db: Session, window_start: date, window_end: date
) -> list[date]:
    """The country's public holidays in the window. Not site-scoped: the calendar is everyone's."""
    return list(
        db.execute(
            select(PublicHoliday.holiday_date).where(
                PublicHoliday.holiday_date >= window_start,
                PublicHoliday.holiday_date < window_end,
            )
        )
        .scalars()
        .all()
    )


def _holidays(
    holiday_dates: Iterable[date], rules: Mapping[date, SiteHolidayRule]
) -> dict[date, tuple[TimeSpan, ...]]:
    """``{holiday: spans}`` for one clinic, from the calendar and that clinic's own rules.

    A public holiday with no rule is a day the clinic is shut: present in the mapping, with no
    spans. That is the safe default, and writing a rule is how a clinic opts out of it.
    """
    holidays: dict[date, tuple[TimeSpan, ...]] = {}
    for day in holiday_dates:
        rule = rules.get(day)
        holidays[day] = (
            (TimeSpan(opens_at=rule.opens_at, closes_at=rule.closes_at),)
            if rule is not None
            and rule.opens_at is not None
            and rule.closes_at is not None
            else ()
        )
    return holidays


def _closed_period(row: SiteClosure) -> ClosedPeriod:
    """One stored closure as the pure logic sees it."""
    return ClosedPeriod(
        # ``stored_sast``, never ``astimezone``: SQLite hands these back naive, and Python would
        # read a naive value as the *server's* zone — correct on a SAST laptop, two hours out in a
        # UTC container. See src.commons.time.stored_sast.
        starts_at=stored_sast(row.starts_at),
        ends_at=None if row.ends_at is None else stored_sast(row.ends_at),
        reason=row.reason,
    )


def schedule_for(
    db: Session,
    access: SiteAccess,
    *,
    from_day: date | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> OpeningSchedule:
    """Build one clinic's :class:`OpeningSchedule` from the database.

    Every query is built by the site guard (:func:`~src.core.site_scope.scoped_select`), so this
    cannot read another clinic's hours even by accident. The public-holiday calendar is the one
    read that is *not* site-scoped, because it is the country's and the same for everyone.

    Args:
        db: The session.
        access: The clinic the request is about, from the site guard.
        from_day: The first day to load holidays for; ``None`` means today in Johannesburg.
        horizon_days: How far ahead holidays are loaded, matching :func:`next_open_at`'s horizon.

    Returns:
        The schedule, ready for the pure functions above.
    """
    window_start, window_end = _window(from_day, horizon_days)
    weekly_rows = db.execute(scoped_select(SiteOpeningHours, access)).scalars().all()
    rules = {
        rule.holiday_date: rule
        for rule in db.execute(
            scoped_select(SiteHolidayRule, access).where(
                SiteHolidayRule.holiday_date >= window_start,
                SiteHolidayRule.holiday_date < window_end,
            )
        )
        .scalars()
        .all()
    }
    closures = tuple(
        _closed_period(row)
        for row in db.execute(
            scoped_select(SiteClosure, access).where(SiteClosure.lifted_at.is_(None))
        )
        .scalars()
        .all()
    )
    return OpeningSchedule(
        weekly=_weekly(weekly_rows),
        holidays=_holidays(_public_holiday_dates(db, window_start, window_end), rules),
        closures=closures,
    )


def published_schedules(
    db: Session,
    site_ids: Collection[str],
    *,
    from_day: date | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> dict[str, OpeningSchedule]:
    """Build the schedules of many **publicly visible** clinics at once, for discovery (Issue 31).

    The patient-facing counterpart of :func:`schedule_for`: a patient holds no role at any clinic,
    so each query is built by :func:`~src.core.site_scope.published_select`, which admits only the
    rows of clinics a patient may be shown. A draft or suspended clinic in ``site_ids`` simply has
    no entry in the answer.

    Four queries whatever the number of clinics (weekly hours, holiday rules, closures and the
    calendar), rather than four per clinic, which is what keeps a page of twenty results inside
    Issue 31's 200 ms budget.

    Args:
        db: The session.
        site_ids: The clinics to load; typically one page of search results.
        from_day: The first day to load holidays for; ``None`` means today in Johannesburg.
        horizon_days: How far ahead holidays are loaded.

    Returns:
        ``{site_id: schedule}`` for every visible clinic in ``site_ids``, including one with no hours
        set (an empty schedule, which is never open).
    """
    if not site_ids:
        return {}
    window_start, window_end = _window(from_day, horizon_days)
    visible = set(
        db.execute(
            select(Site.id).where(
                Site.id.in_(list(site_ids)), *publicly_visible_site_clauses()
            )
        ).scalars()
    )
    weekly: dict[str, list[SiteOpeningHours]] = {}
    for row in db.execute(published_select(SiteOpeningHours, visible)).scalars():
        weekly.setdefault(row.site_id, []).append(row)
    rules: dict[str, dict[date, SiteHolidayRule]] = {}
    for rule in db.execute(
        published_select(SiteHolidayRule, visible).where(
            SiteHolidayRule.holiday_date >= window_start,
            SiteHolidayRule.holiday_date < window_end,
        )
    ).scalars():
        rules.setdefault(rule.site_id, {})[rule.holiday_date] = rule
    closures: dict[str, list[ClosedPeriod]] = {}
    for row in db.execute(
        published_select(SiteClosure, visible).where(SiteClosure.lifted_at.is_(None))
    ).scalars():
        closures.setdefault(row.site_id, []).append(_closed_period(row))
    holiday_dates = (
        _public_holiday_dates(db, window_start, window_end) if visible else []
    )
    return {
        site_id: OpeningSchedule(
            weekly=_weekly(weekly.get(site_id, ())),
            holidays=_holidays(holiday_dates, rules.get(site_id, {})),
            closures=tuple(closures.get(site_id, ())),
        )
        for site_id in visible
    }
