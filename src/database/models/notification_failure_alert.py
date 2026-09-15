"""The team was told that one transport's delivery failure rate crossed its threshold (Issue 71).

One row per transport per alert window: the watch posts the team alert only when it inserts the row, so
however many instances run the watch, and however often, a failing transport is announced once per window
and not once per run. The row keeps what was measured, so the alert can be checked later against the
ledger.

No ``site_id``: delivery health is platform-wide. Datetimes are Africa/Johannesburg.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin


class NotificationFailureAlert(Base, TimestampMixin):
    """A delivery failure rate over the threshold, for one transport, in one window: announced once."""

    __tablename__ = "notification_failure_alert"
    __table_args__ = (
        UniqueConstraint(
            "channel", "window_start", name="uq_notification_failure_alert_window"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    """:class:`~src.commons.enums.NotificationChannel`."""
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The start of the alert window the rate was measured for."""
    attempted: Mapped[int] = mapped_column(Integer, nullable=False)
    """Messages that reached an outcome with a provider in the window: sent, delivered or dead."""
    failed: Mapped[int] = mapped_column(Integer, nullable=False)
    """Of those, dead-lettered."""
    failure_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    """``failed / attempted``."""
