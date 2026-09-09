"""AlertDraft model: a private, unsent staff alert the author is still composing (Issue #132 follow-up).

Mirrors :class:`~src.database.models.message_draft.MessageDraft`, minus the reply half — an alert
never replies to anything, so there is no ``thread_id``; every alert draft is an
announcement-in-progress (``audience_type``/``audience_ref``). ``kind`` is not stored here: every
alert composed through the drafts UI is :class:`~src.modules.messaging.enums.AlertKind` ``CUSTOM``
by definition (only a system-raised alert carries a different kind, and those are never drafted).

``author_id`` is ``CASCADE``: a private draft is meaningless once its author's account is gone.
Carries TimestampMixin (created_at/modified_at); ``modified_at`` is when the draft was last edited.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin


class AlertDraft(Base, TimestampMixin):
    """One author-private, unsent staff alert being composed."""

    __tablename__ = "alert_draft"
    __table_args__ = (
        Index(
            "ix_clinicq_alert_draft_author_id_modified_at",
            "author_id",
            "modified_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    author_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    audience_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    """The :class:`~src.modules.messaging.enums.AudienceType` this draft will broadcast to, once
    set. Resolved and authorised only at send time, never stored here beforehand."""
    audience_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str | None] = mapped_column(String(10), nullable=True)
    """The :class:`~src.modules.messaging.enums.AlertSeverity` chosen so far; send-time validation
    requires one before the draft can become a real alert."""
