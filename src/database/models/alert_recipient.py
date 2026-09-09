"""AlertRecipient model: one member of an alert's resolved audience (Issue #132 follow-up).

Mirrors :class:`~src.database.models.message_participant.MessageParticipant`, but — since an alert
has no reply thread to carry read state on — this single row also carries the read receipt
(``read_at``) that messaging splits into a separate ``MessageReceipt`` table. A row exists for the
author (``role='sender'``, staff alerts only) and for each resolved recipient
(``role='recipient'``); ``read_at`` is ``NULL`` until that recipient opens it.

The ``(alert_id, user_id)`` pair is unique so a user appears once per alert, and both foreign keys
``CASCADE``: this row carries no history worth keeping once its alert or its user is gone.

Carries TimestampMixin (created_at/modified_at).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from src.database.models.alert import Alert


class AlertRecipient(Base, TimestampMixin):
    """One member of an alert's resolved audience (sender or recipient), with its read state."""

    __tablename__ = "alert_recipient"
    __table_args__ = (
        UniqueConstraint("alert_id", "user_id", name="uq_alert_recipient_alert_user"),
        Index("ix_clinicq_alert_recipient_alert_id", "alert_id"),
        # "Which alerts am I in?" fans out from the user (inbox / unread).
        Index("ix_clinicq_alert_recipient_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    alert_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("alert.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    """``sender`` or ``recipient`` — display only, mirrors ``MessageParticipant.role``."""
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When this recipient first read the alert; ``NULL`` means unread. Always ``NULL`` for the
    sender's own ``role='sender'`` row (they authored it, not "read" it)."""

    alert: Mapped[Alert] = relationship(
        "Alert",
        back_populates="recipients",
    )
