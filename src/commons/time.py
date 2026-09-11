"""Business time: every "when" in ClinicQ is read and presented in ``Africa/Johannesburg``.

The rule (``.cursor/rules/timezone-johannesburg.mdc``, non-negotiable 5 in ``docs/guideline.md``):

* **Business datetimes are timezone-aware and in SAST.** Take "now" from :func:`now_sast`, never
  from a bare ``datetime.now()``: a naive value takes the server's clock zone, which is UTC in the
  container and SAST on a laptop, so the same code would put a ticket on a different service day
  depending on where it runs. ``tests/unit/commons/test_conventions.py`` fails the build on one.
* **Storage is UTC, by the database.** Columns are ``timestamptz``: PostgreSQL stores the instant
  in UTC whatever offset the value arrives with, and hands it back aware. Nothing here converts to
  UTC before writing; an aware SAST value *is* the same instant.
* **UTC in application code only where a standard requires it** (JWT ``exp``/``iat`` are Unix
  times, an X.509 ``notAfter`` is UTC). The conventions guard lists those modules by name.

The **service day** is the calendar day in Johannesburg: queue numbering restarts at SAST midnight,
not UTC midnight (02:00 SAST), which is the bug this module exists to make impossible.
"""

from datetime import date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

#: The application timezone. The one definition: ``src.core.s3_logging`` re-exports it for the
#: kernel modules that import it from there. South Africa has no daylight saving, so SAST is
#: always UTC+02:00, but the zone (not a fixed offset) is what gets stored in settings and shown.
APP_TIMEZONE: Final = ZoneInfo("Africa/Johannesburg")


def now_sast() -> datetime:
    """Return the current instant as a timezone-aware datetime in ``Africa/Johannesburg``."""
    return datetime.now(APP_TIMEZONE)


def to_sast(value: datetime) -> datetime:
    """Return ``value`` expressed in ``Africa/Johannesburg`` (the same instant, SAST wall clock).

    Args:
        value: A timezone-aware datetime in any zone.

    Returns:
        The same instant with ``tzinfo`` set to :data:`APP_TIMEZONE`.

    Raises:
        ValueError: If ``value`` is naive. A naive datetime does not say which instant it means,
            and guessing (as UTC, or as the server's zone) is exactly the ambiguity the rule
            forbids, so the caller must attach the zone it knows the value is in.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"Refusing to convert a naive datetime ({value.isoformat()}): attach its timezone first."
        )
    return value.astimezone(APP_TIMEZONE)


def business_date(value: datetime | None = None) -> date:
    """Return the service day (the Johannesburg calendar date) of ``value``, or of now.

    Use this rather than ``date.today()``, which reads the server's zone, or ``value.date()``,
    which reads whatever zone ``value`` happens to carry: ``2026-09-10T23:30Z`` is already the
    11th in Johannesburg.

    Args:
        value: A timezone-aware datetime; ``None`` means now.

    Returns:
        The calendar date in ``Africa/Johannesburg``.

    Raises:
        ValueError: If ``value`` is naive (see :func:`to_sast`).
    """
    return to_sast(value if value is not None else now_sast()).date()


def business_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Return the half-open ``[start, end)`` of a service day, as aware SAST datetimes.

    The interval to filter ``timestamptz`` columns by when a query means "on this service day"
    (tickets joined today, yesterday's no-shows): ``start <= column < end``. Built from the zone
    rather than by adding 24 hours to a UTC midnight, so it stays right even if the zone's rules
    ever change.

    Args:
        day: The Johannesburg calendar date.

    Returns:
        ``(start, end)``: SAST midnight of ``day`` and SAST midnight of the following day.
    """
    start = datetime.combine(day, time.min, tzinfo=APP_TIMEZONE)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=APP_TIMEZONE)
    return start, end
