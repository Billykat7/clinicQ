"""MessageReceipt model: a read receipt — one participant has read one message (Issue #68).

A row exists exactly when ``user_id`` has read ``message_id``; its absence means unread. That
makes a participant's unread set the messages in their threads with no receipt for them, and
marking a thread read an insert of the missing rows. The pair is unique so re-reading a message
is idempotent (``ON CONFLICT DO NOTHING``), and ``read_at`` records when it was first read.

Both foreign keys are ``CASCADE``: a receipt is meaningless once its message is gone or its user's
account is removed, and — unlike a message body — it carries nothing worth preserving as history,
so it is cleaned up with either parent rather than left dangling.

Carries TimestampMixin (created_at/modified_at); ``read_at`` is the domain timestamp and is in
Africa/Johannesburg terms, the project's business timezone.
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
    from src.database.models.message import Message


class MessageReceipt(Base, TimestampMixin):
    """One participant's read receipt for one message."""

    __tablename__ = "message_receipt"
    __table_args__ = (
        # A user reads a message at most once; the pair is the natural key and makes marking a
        # message read idempotent.
        UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_message_receipt_message_user",
        ),
        # Unread lookups and receipt lists fan out from the message.
        Index("ix_clinicq_message_receipt_message_id", "message_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    message_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("message.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The message that was read. ``CASCADE`` so a receipt is removed with its message."""
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The participant who read it. ``CASCADE`` so a receipt is removed with the user's account —
    a receipt carries no history worth keeping once the reader is gone."""
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    """When the message was first read (Africa/Johannesburg)."""

    message: Mapped[Message] = relationship(
        "Message",
        back_populates="receipts",
    )
