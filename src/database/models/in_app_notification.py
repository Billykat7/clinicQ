"""InAppNotification model: a per-user in-app notification with read-state (Issue #113).

The :class:`~src.database.models.notification.Notification` ledger records *delivery* — one row
per email/SMS send, keyed by the recipient address, tracking whether the transport landed. The
notification **centre** is a different thing: a per-*user* feed of domain events (a maintenance
request logged, a lease expiring, an application decided) that the shell bell counts and previews,
each carrying its own **read-state**. This model is that feed's store.

A row is *unread* exactly when ``read_at`` is ``NULL`` — the same absence-means-unread rule the
message read-receipt uses — so the bell's unread count is "rows for this user with no ``read_at``"
and marking one read is a single timestamp write (marking unread clears it back to ``NULL``).

``category`` is the :class:`~src.commons.enums.NotificationCategory` the event belongs to, so the
centre honours the same per-category preferences as delivery: an event in a category the user has
muted is never recorded here, and therefore never badges. ``link`` is where "view" navigates to
open the item full-screen (the relevant record); it is optional because not every event has a
deep link. Bodies are stored verbatim and escaped at render time.

``user_id`` is ``CASCADE`` — a personal feed is meaningless once its user's account is gone.
Carries TimestampMixin (created_at/modified_at); ``created_at`` orders the feed and is in
Africa/Johannesburg terms, the project's business timezone.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin


class InAppNotification(Base, TimestampMixin):
    """One in-app notification for one user, with its read-state."""

    __tablename__ = "in_app_notification"
    __table_args__ = (
        # The centre feed is "my notifications, newest first"; index the owner + created_at.
        Index(
            "ix_clinicq_in_app_notification_user_id_created_at",
            "user_id",
            "created_at",
        ),
        # The unread count filters unread rows per user; index the owner + read_at for it.
        Index(
            "ix_clinicq_in_app_notification_user_id_read_at",
            "user_id",
            "read_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The user this notification belongs to — its sole owner. ``CASCADE`` so the feed row is
    removed with the account. Indexed via the two composite indexes above."""
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    """The :class:`~src.commons.enums.NotificationCategory` this event belongs to, so the centre
    honours the same per-category preferences as delivery (a muted category is never recorded)."""
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    """Short headline shown in the preview (e.g. "Maintenance request received")."""
    body: Mapped[str] = mapped_column(Text, nullable=False)
    """The preview snippet — stored verbatim, escaped only at render time."""
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    """Where "view" navigates to open the item full-screen (the relevant record); ``NULL`` when the
    event has no deep link, in which case the preview is informational only."""
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """When the user first read this item (Africa/Johannesburg), or ``NULL`` while unread. Marking
    read stamps it; marking unread clears it back to ``NULL``."""
