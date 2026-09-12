"""When a clinic is open, when it is not, and when it closed unexpectedly (Issue 24).

Four tables, and the order of the first three is the order of precedence: an **ad-hoc closure**
beats a **holiday rule**, which beats the **weekly schedule**. That ordering is a rule of the
domain, resolved once in :mod:`src.modules.sites.hours`, and it is why these live together.

* :class:`SiteOpeningHours` — one row per *span*, not per day. A clinic that shuts for lunch has two
  rows for that weekday; a weekday with no rows is closed. Splitting by span rather than storing
  ``opens``/``closes`` on the day is what makes a lunch break and a split shift the same shape, and
  it lets a span cross midnight (``closes <= opens``) without a special case anywhere else.
* :class:`PublicHoliday` — the country's calendar, **not site-scoped**: 16 June is 16 June for every
  clinic. Seeded from ``scripts/db/holidays.py`` for the current and next year.
* :class:`SiteHolidayRule` — one clinic's answer for one holiday. Without a rule a clinic is closed
  on a public holiday, which is the safe default; with one it opens for the hours the rule names.
* :class:`SiteClosure` — "we are shut this afternoon, the water is off". A reason, a window, who
  said so, and whether it was lifted early.
"""

from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

#: Monday is 0, matching :meth:`datetime.date.weekday`. Stored as an integer rather than a name so
#: the comparison is arithmetic and the ordering is the week's.
MONDAY = 0
SUNDAY = 6


class SiteOpeningHours(Base, TimestampMixin):
    """One span of a clinic's ordinary week: "Mondays, 07:00 to 12:30"."""

    __tablename__ = "site_opening_hours"
    __table_args__ = (
        Index("ix_clinicq_site_opening_hours_site_weekday", "site_id", "weekday"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("clinicq.site.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    """0 = Monday ... 6 = Sunday, as :meth:`datetime.date.weekday` numbers them."""
    opens_at: Mapped[time] = mapped_column(Time, nullable=False)
    closes_at: Mapped[time] = mapped_column(Time, nullable=False)
    """Wall-clock in ``Africa/Johannesburg``. ``closes_at <= opens_at`` means the span crosses
    midnight — a 22:00 to 02:00 after-hours service is one span, not two."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"SiteOpeningHours(site_id={self.site_id!r}, weekday={self.weekday}, "
            f"{self.opens_at}-{self.closes_at})"
        )


class PublicHoliday(Base, TimestampMixin):
    """One public holiday in the operating country. Shared by every clinic; not site-scoped."""

    __tablename__ = "public_holiday"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    observed_for: Mapped[str | None] = mapped_column(String(120), nullable=True)
    """The holiday this one is the Monday for, when it exists only because of the Sunday rule."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"PublicHoliday({self.holiday_date}, {self.name!r})"


class SiteHolidayRule(Base, TimestampMixin):
    """What one clinic does on one public holiday: closed, or open for these hours.

    A holiday with **no** rule means closed. A clinic that works public holidays writes a rule with
    ``opens_at``/``closes_at``; one that wants to be explicit about closing writes a rule with both
    null, which reads the same and says somebody decided it.
    """

    __tablename__ = "site_holiday_rule"
    __table_args__ = (
        Index(
            "uq_clinicq_site_holiday_rule_site_date",
            "site_id",
            "holiday_date",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("clinicq.site.id", ondelete="CASCADE"), nullable=False
    )
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    opens_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    closes_at: Mapped[time | None] = mapped_column(Time, nullable=True)

    @property
    def is_open(self) -> bool:
        """Whether this rule opens the clinic at all."""
        return self.opens_at is not None and self.closes_at is not None

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"SiteHolidayRule(site_id={self.site_id!r}, {self.holiday_date}, "
            f"open={self.is_open})"
        )


class SiteClosure(Base, TimestampMixin):
    """An unplanned closure: a reason, a window, and who announced it.

    Kept after it ends. "Why were you shut on the 14th" is a question a clinic has to be able to
    answer, and the notification that went to every waiting patient points at this row.
    """

    __tablename__ = "site_closure"
    __table_args__ = (
        Index("ix_clinicq_site_closure_site_starts_at", "site_id", "starts_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("clinicq.site.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    """The manager's own words; this is what the patient is shown."""
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """``None`` means "until further notice": open-ended, and lifted by hand."""
    announced_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("clinicq.user.id", ondelete="RESTRICT"), nullable=True
    )
    """Who closed the clinic. ``RESTRICT``: a deactivated account is still the author of this."""
    lifted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """Set when a closure is ended early; the row stays, so the trail keeps both facts."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"SiteClosure(site_id={self.site_id!r}, starts_at={self.starts_at})"
