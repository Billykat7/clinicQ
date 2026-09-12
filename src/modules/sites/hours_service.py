"""Reading and writing a clinic's hours, holiday rules and closures (Issue 24).

Everything that touches the database for Issue 24, kept out of :mod:`src.modules.sites.hours` so
that module stays what its tests need it to be: pure functions of their inputs. Every query here is
built by the site guard (:func:`~src.core.site_scope.scoped_select`), so another clinic's hours are
unreachable rather than merely unasked-for.

**Announcing a closure does not send anything.** :func:`announce_closure` writes the row and
publishes :class:`~src.core.domain_events.SiteClosureAnnounced` *after the commit*; who is holding a
ticket is the queue's question (Issue 39) and reaching them is the notification service's
(Issue 63). A clinic must be able to close when the SMS gateway is down.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.time import APP_TIMEZONE, business_date, now_sast
from src.core.domain_events import (
    SiteClosureAnnounced,
    SiteClosureLifted,
    publish_after_commit,
)
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models import (
    PublicHoliday,
    SiteClosure,
    SiteHolidayRule,
    SiteOpeningHours,
)
from src.modules.sites.hours import DEFAULT_HORIZON_DAYS, TimeSpan
from src.modules.sites.schemas import (
    ClosureIn,
    ClosureListOut,
    ClosureOut,
    DayHoursOut,
    HolidayListOut,
    HolidayOut,
    HolidayRuleIn,
    TimeSpanOut,
    WeeklyHoursIn,
    WeeklyHoursOut,
)

#: How far ahead the holiday calendar is listed by default: a clinic plans a season, not a fortnight.
HOLIDAY_WINDOW_DAYS = 400


class ClosureNotFoundError(LookupError):
    """No such closure at this clinic, or it has already been lifted."""


class HolidayNotFoundError(LookupError):
    """That date is not a public holiday, so there is nothing to write a rule about."""


# --------------------------------------------------------------------------------------
# The weekly schedule
# --------------------------------------------------------------------------------------


def weekly_hours(db: Session, access: SiteAccess) -> WeeklyHoursOut:
    """One clinic's ordinary week, **all seven days**, so a caller never infers a missing one."""
    rows = db.execute(scoped_select(SiteOpeningHours, access)).scalars().all()
    by_day: dict[int, list[SiteOpeningHours]] = {}
    for row in rows:
        by_day.setdefault(row.weekday, []).append(row)
    return WeeklyHoursOut(
        site_id=access.site_id,
        days=[
            DayHoursOut(
                weekday=weekday,
                spans=[
                    TimeSpanOut(
                        opens_at=row.opens_at,
                        closes_at=row.closes_at,
                        crosses_midnight=row.closes_at <= row.opens_at,
                    )
                    for row in sorted(
                        by_day.get(weekday, []), key=lambda row: row.opens_at
                    )
                ],
            )
            for weekday in range(7)
        ],
    )


def replace_weekly_hours(
    db: Session, access: SiteAccess, payload: WeeklyHoursIn
) -> WeeklyHoursOut:
    """Replace the clinic's whole week. The caller commits.

    Replacement rather than a merge: "what are your hours" has one answer, and a partial update is
    how a clinic keeps last year's Tuesday. Only the weekdays the payload names are touched, so a
    request that says nothing about Sunday leaves Sunday alone — which is what makes this safe to
    call from a form that only renders the weekdays a clinic works.
    """
    named = {day.weekday for day in payload.days}
    for row in db.execute(scoped_select(SiteOpeningHours, access)).scalars().all():
        if row.weekday in named:
            db.delete(row)
    db.flush()
    for day in payload.days:
        for span in day.spans:
            db.add(
                SiteOpeningHours(
                    site_id=access.site_id,
                    weekday=day.weekday,
                    opens_at=span.opens_at,
                    closes_at=span.closes_at,
                )
            )
    db.flush()
    return weekly_hours(db, access)


# --------------------------------------------------------------------------------------
# Public holidays, and what one clinic does on each
# --------------------------------------------------------------------------------------


def _holidays_between(db: Session, first: date, last: date) -> Sequence[PublicHoliday]:
    """The country's holidays in a window. Not site-scoped: 16 June is 16 June everywhere."""
    return (
        db.execute(
            select(PublicHoliday)
            .where(
                PublicHoliday.holiday_date >= first,
                PublicHoliday.holiday_date <= last,
            )
            .order_by(PublicHoliday.holiday_date)
        )
        .scalars()
        .all()
    )


def holiday_calendar(
    db: Session,
    access: SiteAccess,
    *,
    from_day: date | None = None,
    days: int = HOLIDAY_WINDOW_DAYS,
) -> HolidayListOut:
    """The clinic's holiday calendar: every holiday in the window, and its answer for each.

    A holiday with no rule reports ``is_open = False`` and ``has_rule = False`` — closed by the
    safe default, and the screen can show that it is a default rather than a decision.
    """
    first = from_day or business_date()
    last = first + timedelta(days=days)
    rules = {
        rule.holiday_date: rule
        for rule in db.execute(
            scoped_select(SiteHolidayRule, access).where(
                SiteHolidayRule.holiday_date >= first,
                SiteHolidayRule.holiday_date <= last,
            )
        )
        .scalars()
        .all()
    }
    return HolidayListOut(
        site_id=access.site_id,
        items=[
            HolidayOut(
                holiday_date=holiday.holiday_date,
                name=holiday.name,
                observed_for=holiday.observed_for,
                is_open=bool(
                    (rule := rules.get(holiday.holiday_date)) is not None
                    and rule.is_open
                ),
                opens_at=rule.opens_at if rule is not None else None,
                closes_at=rule.closes_at if rule is not None else None,
                has_rule=rule is not None,
            )
            for holiday in _holidays_between(db, first, last)
        ],
    )


def set_holiday_rule(
    db: Session, access: SiteAccess, holiday_date: date, payload: HolidayRuleIn
) -> HolidayOut:
    """Record what this clinic does on one public holiday. The caller commits.

    Raises:
        HolidayNotFoundError: If that date is not a public holiday. A clinic closing on an ordinary
            Tuesday is an ad-hoc closure, not a holiday rule, and conflating the two would let a
            clinic invent public holidays for the whole country's calendar.
    """
    holiday = db.execute(
        select(PublicHoliday).where(PublicHoliday.holiday_date == holiday_date)
    ).scalar_one_or_none()
    if holiday is None:
        raise HolidayNotFoundError(f"{holiday_date} is not a public holiday.")
    rule = db.execute(
        scoped_select(SiteHolidayRule, access).where(
            SiteHolidayRule.holiday_date == holiday_date
        )
    ).scalar_one_or_none()
    if rule is None:
        rule = SiteHolidayRule(site_id=access.site_id, holiday_date=holiday_date)
        db.add(rule)
    rule.opens_at = payload.opens_at
    rule.closes_at = payload.closes_at
    db.flush()
    return HolidayOut(
        holiday_date=holiday.holiday_date,
        name=holiday.name,
        observed_for=holiday.observed_for,
        is_open=rule.is_open,
        opens_at=rule.opens_at,
        closes_at=rule.closes_at,
        has_rule=True,
    )


# --------------------------------------------------------------------------------------
# Ad-hoc closures
# --------------------------------------------------------------------------------------


def list_closures(
    db: Session, access: SiteAccess, *, include_past: bool = False
) -> ClosureListOut:
    """A clinic's closures, most recent first. Past ones are kept, and shown when asked for."""
    statement = scoped_select(SiteClosure, access)
    if not include_past:
        now = now_sast()
        statement = statement.where(
            SiteClosure.lifted_at.is_(None),
            (SiteClosure.ends_at.is_(None)) | (SiteClosure.ends_at > now),
        )
    rows = db.execute(statement.order_by(SiteClosure.starts_at.desc())).scalars().all()
    return ClosureListOut(
        total=len(rows), items=[ClosureOut.model_validate(row) for row in rows]
    )


def announce_closure(
    db: Session, access: SiteAccess, payload: ClosureIn
) -> SiteClosure:
    """Close the clinic, and publish the fact. The caller commits.

    The event is published **after the commit** (:func:`~src.core.domain_events.publish_after_commit`),
    so a subscriber that reads the database sees a closure that is really there. Nothing is sent
    from here: this function's job ends at "the clinic is shut and everyone who cares has been
    told that it is".

    Raises:
        ValueError: If the window ends before it starts.
    """
    starts_at = (payload.starts_at or now_sast()).astimezone(APP_TIMEZONE)
    ends_at = (
        None if payload.ends_at is None else payload.ends_at.astimezone(APP_TIMEZONE)
    )
    if ends_at is not None and ends_at <= starts_at:
        raise ValueError("A closure cannot end before it starts.")
    closure = SiteClosure(
        site_id=access.site_id,
        reason=payload.reason.strip(),
        starts_at=starts_at,
        ends_at=ends_at,
        announced_by=str(access.user.id),
    )
    db.add(closure)
    db.flush()
    publish_after_commit(
        db,
        SiteClosureAnnounced(
            site_id=access.site_id,
            closure_id=closure.id,
            reason=closure.reason,
            starts_at=starts_at,
            ends_at=ends_at,
            announced_by=access.user.email,
        ),
    )
    return closure


def lift_closure(db: Session, access: SiteAccess, closure_id: str) -> SiteClosure:
    """End a closure early. The row stays; ``lifted_at`` records that it was ended. Caller commits.

    Raises:
        ClosureNotFoundError: If the clinic has no such live closure — the same answer for "already
            lifted" and "never existed", like every other lookup in this codebase.
    """
    closure = db.execute(
        scoped_select(SiteClosure, access).where(
            SiteClosure.id == closure_id, SiteClosure.lifted_at.is_(None)
        )
    ).scalar_one_or_none()
    if closure is None:
        raise ClosureNotFoundError("Not found.")
    closure.lifted_at = now_sast()
    db.flush()
    publish_after_commit(
        db,
        SiteClosureLifted(
            site_id=access.site_id,
            closure_id=closure.id,
            lifted_by=access.user.email,
        ),
    )
    return closure


# --------------------------------------------------------------------------------------
# Seeding the country's holiday calendar
# --------------------------------------------------------------------------------------


def seed_public_holidays(db: Session, years: Sequence[int]) -> tuple[int, int]:
    """Write the public holidays for ``years``; return ``(created, unchanged)``. Caller commits.

    Idempotent, and it never removes a row it did not create: a platform admin adds the one-off
    holidays a President proclaims (an election day, a day of mourning), and a re-run has to leave
    those alone.
    """
    from scripts.db.holidays import holidays_between

    created = unchanged = 0
    existing = {
        row.holiday_date: row
        for row in db.execute(select(PublicHoliday)).scalars().all()
    }
    for holiday in holidays_between(min(years), max(years)):
        row = existing.get(holiday.day)
        if row is None:
            db.add(
                PublicHoliday(
                    holiday_date=holiday.day,
                    name=holiday.name,
                    observed_for=holiday.observed_for,
                )
            )
            created += 1
        else:
            unchanged += 1
    db.flush()
    return created, unchanged


def default_horizon_end(moment: datetime | None = None) -> date:
    """The last day :func:`~src.modules.sites.hours.next_open_at` will look at, from ``moment``."""
    return business_date(moment) + timedelta(days=DEFAULT_HORIZON_DAYS)


#: Re-exported so a caller building a schedule by hand does not import from two modules.
__all__ = [
    "ClosureNotFoundError",
    "HolidayNotFoundError",
    "TimeSpan",
    "announce_closure",
    "default_horizon_end",
    "holiday_calendar",
    "lift_closure",
    "list_closures",
    "replace_weekly_hours",
    "seed_public_holidays",
    "set_holiday_rule",
    "weekly_hours",
]
