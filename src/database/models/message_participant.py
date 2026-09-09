"""MessageParticipant model: the stored audience of an announcement thread (Issue #112).

Record-anchored threads (a unit, lease, work order or application) never store their participants
— they are *derived* from the anchor's live state on every access (see
``src.modules.messaging.service.derive_participants``). An **announcement** has no single live
record to derive from: it is a broadcast to an *audience* (all users, an owner's tenants, a
property's occupants, a managed scope) that is authorised and resolved once, at send time. This
table is where that resolved audience lives, so opening the broadcast later shows exactly who it
went to and read/unread state works the same as any other thread.

A row exists for the sender and for each resolved recipient. ``role`` records *how* they are in
the conversation (sender vs recipient) purely for display — it is not an RBAC role. The
``(thread_id, user_id)`` pair is unique so a user appears once per broadcast, and both foreign
keys ``CASCADE``: an audience row carries no history worth keeping once its thread or its user is
gone (the messages themselves survive via their own ``SET NULL`` author link).

Carries TimestampMixin (created_at/modified_at).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from src.database.models.message_thread import MessageThread


class MessageParticipant(Base, TimestampMixin):
    """One member of an announcement thread's resolved audience (sender or recipient)."""

    __tablename__ = "message_participant"
    __table_args__ = (
        # A user is in a broadcast's audience at most once; the pair is the natural key.
        UniqueConstraint(
            "thread_id",
            "user_id",
            name="uq_message_participant_thread_user",
        ),
        # Membership tests and audience listing fan out from the thread.
        Index("ix_clinicq_message_participant_thread_id", "thread_id"),
        # "Which broadcasts am I in?" fans out from the user (inbox / unread).
        Index("ix_clinicq_message_participant_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    thread_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("message_thread.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The announcement thread this audience row belongs to. ``CASCADE`` so the audience is
    removed with the thread. Indexed via ``ix_clinicq_message_participant_thread_id``."""
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The user in the audience (sender or recipient). ``CASCADE`` so the row is removed with the
    user's account — it carries no history worth keeping once the user is gone."""
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    """How this user is in the conversation — ``sender`` or ``recipient`` (display only, not an
    RBAC role), mirroring the derived ``role`` on record-anchored participants."""

    thread: Mapped[MessageThread] = relationship(
        "MessageThread",
        back_populates="participants",
    )
