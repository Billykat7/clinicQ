"""discovery_event: anonymous records of what patients looked at and joined (Issue 38).

A clinic manager's first question is "how many people saw us and did not come?". One row per
search, clinic view, join started and join completed answers it, and the rows are designed so they
cannot answer anything about a person:

* **No identifier beyond a rotating session reference.** ``session_ref`` is an HMAC of the browser's
  discovery session cookie and the service day, under a server key
  (:func:`src.modules.discovery.analytics.session_ref`). It groups one visit's events on one day, it
  changes at midnight, and it cannot be turned back into the cookie. There is no user, patient, phone
  number or IP address column, and a test reads the stored rows to prove it.
* **No precise location.** A search records *how* it was made (from a position or an area, the
  radius, the sector) and how many clinics it found, never where from.
* **A clinic can opt out** (``site.analytics_enabled``): no view or join of it is recorded.

``site_id`` has no foreign key: a report about last month must outlive a clinic's listing.
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.ids import new_id
from src.database.models.base import Base


class DiscoveryEvent(Base):
    """One anonymous discovery event."""

    __tablename__ = "discovery_event"
    __table_args__ = (
        # The conversion report: one clinic's views and joins over a range of days.
        Index(
            "ix_clinicq_discovery_event_site_kind_day", "site_id", "kind", "service_day"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    """:class:`~src.commons.enums.DiscoveryEventKind`."""
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    """:class:`~src.commons.enums.DiscoveryChannel`."""
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """When it happened (Africa/Johannesburg)."""
    service_day: Mapped[date] = mapped_column(Date, nullable=False)
    """The Johannesburg date, which the reports group by and the session reference rotates on."""
    session_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """The day's HMAC of the discovery session; ``None`` for a caller with no session."""
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The clinic viewed or joined. ``None`` for a search."""
    sector: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """A search's :class:`~src.commons.enums.SectorFilter`."""
    origin_basis: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """A search's :class:`~src.commons.enums.DistanceBasis`: from a position or an area, not where."""
    radius_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
