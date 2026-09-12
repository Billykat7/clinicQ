"""South Africa's public holidays, computed rather than typed in (Issue 24).

**Why computed.** Two of the twelve move with Easter, and one rule moves the rest: section 2(1) of
the Public Holidays Act 36 of 1994 says that whenever a public holiday falls on a **Sunday**, the
following **Monday** is a public holiday too. A list typed in by hand is right for one year and
quietly wrong for the next, and a clinic that opens on a day the country is closed sends people on
a wasted trip — which is the whole reason Issue 24 exists.

So: the ten fixed dates, Good Friday and Family Day from the Gregorian Easter algorithm, and the
Sunday rule applied to all twelve. Nothing here touches a database; ``scripts/db/seed_dev_data.py``
writes the rows and ``src/modules/sites/hours.py`` reads them.

**What is deliberately not here.** One-off holidays proclaimed by the President for a particular
year (an election day, a day of mourning) are not derivable from a rule. A platform admin adds those
to ``public_holiday`` by hand; the seed is idempotent and leaves rows it did not create alone.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

#: The holidays with a fixed calendar date, as the Act names them.
FIXED_HOLIDAYS: Final[tuple[tuple[int, int, str], ...]] = (
    (1, 1, "New Year's Day"),
    (3, 21, "Human Rights Day"),
    (4, 27, "Freedom Day"),
    (5, 1, "Workers' Day"),
    (6, 16, "Youth Day"),
    (8, 9, "National Women's Day"),
    (9, 24, "Heritage Day"),
    (12, 16, "Day of Reconciliation"),
    (12, 25, "Christmas Day"),
    (12, 26, "Day of Goodwill"),
)

#: The suffix a Monday earns by section 2(1) when the holiday itself fell on a Sunday.
OBSERVED_SUFFIX: Final = "(observed)"


@dataclass(frozen=True, slots=True)
class Holiday:
    """One public holiday on one date.

    ``observed_for`` names the holiday this one exists because of, and is set only on the Mondays
    the Sunday rule creates — so a screen can say "Monday, because Christmas was a Sunday".
    """

    day: date
    name: str
    observed_for: str | None = None


def easter_sunday(year: int) -> date:
    """Return Easter Sunday in the Gregorian calendar for ``year``.

    The anonymous Gregorian algorithm (Meeus/Jones/Butcher). Pure arithmetic, no table and no
    dependency, valid for every year this project will see.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lunar = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lunar) // 451
    month, day = divmod(h + lunar - 7 * m + 114, 31)
    return date(year, month, day + 1)


def holidays_for(year: int) -> tuple[Holiday, ...]:
    """Every South African public holiday in ``year``, in date order.

    The ten fixed dates, Good Friday and Family Day either side of Easter, and one extra Monday for
    each of those that falls on a Sunday (Public Holidays Act 36 of 1994, s 2(1)).

    Args:
        year: The calendar year.

    Returns:
        The holidays, sorted by date. Where the Sunday rule creates a Monday, both days are
        present: the Sunday holiday itself, and the Monday that observes it.
    """
    easter = easter_sunday(year)
    proclaimed = [
        *(Holiday(date(year, month, day), name) for month, day, name in FIXED_HOLIDAYS),
        Holiday(easter - timedelta(days=2), "Good Friday"),
        Holiday(easter + timedelta(days=1), "Family Day"),
    ]
    # Section 2(1). Applied after the whole list is known, so an Easter holiday is covered too —
    # Family Day is always a Monday, which is exactly why the rule cannot be applied selectively.
    observed = [
        Holiday(
            holiday.day + timedelta(days=1),
            f"{holiday.name} {OBSERVED_SUFFIX}",
            observed_for=holiday.name,
        )
        for holiday in proclaimed
        if holiday.day.weekday() == 6  # Sunday
    ]
    return tuple(sorted([*proclaimed, *observed], key=lambda holiday: holiday.day))


def holidays_between(first_year: int, last_year: int) -> tuple[Holiday, ...]:
    """Every holiday from ``first_year`` to ``last_year`` inclusive, in date order."""
    return tuple(
        holiday
        for year in range(first_year, last_year + 1)
        for holiday in holidays_for(year)
    )
