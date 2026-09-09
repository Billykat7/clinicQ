"""Message model: one post in a thread, by one author (Issue #68).

A message belongs to a :class:`~src.database.models.message_thread.MessageThread` and is written
by a ``User``. The body is stored verbatim (never pre-escaped): escaping is a *render-time*
concern, done by the portal template's autoescaping so the same bytes are safe in HTML and exact
in the JSON API — storing escaped text would double-escape one of the two.

* ``thread_id`` is ``RESTRICT`` — a thread is never deleted out from under its messages; the
  thread's ``cascade`` handles removal when a thread itself is deleted.
* ``author_id`` is ``SET NULL`` — a message survives its author's account being removed, so the
  history a later reader relies on is not torn out. "Removing a participant's access going forward,
  not retroactively" is the same principle: their past posts remain part of the record.

Carries TimestampMixin (created_at/modified_at); ``created_at`` orders the conversation and is in
Africa/Johannesburg terms, the project's business timezone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from src.database.models.message_receipt import MessageReceipt
    from src.database.models.message_thread import MessageThread


class Message(Base, TimestampMixin):
    """One message posted to a thread by an author."""

    __tablename__ = "message"
    __table_args__ = (
        # A thread is read newest-part-last as an ordered list; index the FK + created_at so
        # loading a conversation stays a cheap ranged read as it grows.
        Index("ix_clinicq_message_thread_id_created_at", "thread_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    thread_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("message_thread.id", ondelete="RESTRICT"),
        nullable=False,
    )
    """The thread this message belongs to. ``RESTRICT`` keeps the thread around while a message
    references it. Indexed via ``ix_clinicq_message_thread_id_created_at``."""
    author_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The user who wrote the message. ``SET NULL`` so the message (and the thread's history)
    survives the author's account being removed; ``NULL`` reads as "a since-removed user"."""
    body: Mapped[str] = mapped_column(Text, nullable=False)
    """The message text, stored verbatim. Escaped at render time by the portal template's
    autoescaping — never pre-escaped here, so the JSON API returns the exact text the author typed
    and no body is double-escaped."""

    thread: Mapped[MessageThread] = relationship(
        "MessageThread",
        back_populates="messages",
    )
    receipts: Mapped[list[MessageReceipt]] = relationship(
        "MessageReceipt",
        back_populates="message",
        cascade="all, delete-orphan",
    )
