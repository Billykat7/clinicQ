"""Notification preference model: one row per user controlling how the product reaches them.

Once SMS and in-app messaging exist (M11), the product can become noisy enough that a user mutes
it entirely — including the messages that matter. This row keeps the essential ones deliverable
while giving the user control over the rest (Issue #72):

* ``channel_by_category`` snapshots the chosen channel per :class:`~src.commons.enums.NotificationCategory`
  (email / sms / off). Only the categories the user has *changed* are stored; an absent category
  uses the sensible default (email on). Essential categories can never be fully disabled — the
  resolution layer (:mod:`src.modules.notifications.preferences`) coerces an essential ``off`` back
  to email — so the row can hold any value without ever muting security or money mail.
* ``quiet_hours_start`` / ``quiet_hours_end`` are wall-clock times in ``timezone``; a non-urgent
  message that lands inside that window is *deferred* to the end of it rather than dropped.
* ``timezone`` is the IANA zone quiet hours are evaluated in (defaults to the business timezone,
  Africa/Johannesburg).

The row is optional: a user with no preferences set gets the defaults, so nothing needs to be
seeded and a brand-new account behaves sensibly. Unsubscribe carries no state of its own — a
login-free unsubscribe simply sets the relevant category to ``off`` here.

Includes TimestampMixin (created_at/modified_at).
"""

from datetime import time
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, ForeignKey, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from src.core.s3_logging import APP_TIMEZONE
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin


class NotificationPreference(Base, TimestampMixin):
    """One user's notification preferences: channel per category, quiet hours, and timezone."""

    __tablename__ = "notification_preference"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    """The account these preferences belong to. ``CASCADE`` so the row goes with the user; ``UNIQUE``
    so there is exactly one preference row per account."""
    channel_by_category: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    """Chosen channel per category: ``{category_value: channel_value}`` where channel is one of
    :class:`~src.commons.enums.NotificationChannelPreference` (``email`` / ``sms`` / ``off``). Only
    categories the user has changed are stored; an absent category uses the default (email on)."""
    quiet_hours_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    """Local wall-clock start of quiet hours (in ``timezone``); ``NULL`` disables quiet hours.
    Paired with ``quiet_hours_end`` — a window may wrap past midnight (e.g. 22:00–07:00)."""
    quiet_hours_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    """Local wall-clock end of quiet hours (in ``timezone``); ``NULL`` disables quiet hours."""
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=APP_TIMEZONE.key,
        server_default=APP_TIMEZONE.key,
    )
    """IANA timezone name quiet hours are evaluated in (default: the business timezone,
    Africa/Johannesburg)."""
