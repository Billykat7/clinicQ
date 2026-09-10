"""MessageThread model: a conversation anchored to a domain record (Issue #68).

Email threads about a leaking tap lose track of *which* unit and *which* job they were about.
A thread pins the conversation to the record it concerns, so the history sits where the next
person will look for it and access follows the record rather than a hand-kept recipient list.

The anchor is a ``(anchor_type, anchor_id)`` pair — the *kind* of record
(:class:`~src.modules.messaging.enums.MessageAnchorType`: unit, lease, work order or
application) and its id. A record has **one** canonical thread: a partial-free unique constraint
on ``(anchor_type, anchor_id)`` makes thread creation an idempotent get-or-create, so two people
opening the conversation from the same record land in the same place.

Participants are never stored here. They are *derived* from the anchor's live state every time
access is checked (see ``src.modules.messaging.service.derive_participants``): the tenant occupying
a unit, the owner of its property, the vendor **currently** assigned to a work order. That is what
lets "unassign the vendor and they lose access going forward, not retroactively" fall out for free
— there is no list to forget to update.

Carries TimestampMixin (created_at/modified_at) and, since Issue #132 follow-up, SoftDeleteMixin —
a manager can now delete a thread (moving it to the admin console's Deleted tab) without losing its
history; a soft-deleted thread stays out of the inbox/sent listings and the anchor's normal
get-or-create until restored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base
from src.database.models.mixins import SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from src.database.models.message import Message
    from src.database.models.message_participant import MessageParticipant


class MessageThread(Base, TimestampMixin, SoftDeleteMixin):
    """A conversation anchored to one domain record; its messages hang off it."""

    __tablename__ = "message_thread"
    __table_args__ = (
        # A record has exactly one canonical thread, so opening the conversation is an idempotent
        # get-or-create. This also indexes the (anchor_type, anchor_id) lookup the service does on
        # every thread resolution.
        UniqueConstraint(
            "anchor_type",
            "anchor_id",
            name="uq_message_thread_anchor",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    anchor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    """Kind of record this thread is anchored to — one of
    :class:`~src.modules.messaging.enums.MessageAnchorType`, stored as text per the project's
    enum-as-text convention. Together with ``anchor_id`` it is the sole source of the thread's
    participants."""
    anchor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    """Id of the anchored record (a unit / lease / work order / application id). Not a database
    foreign key because the column points at one of several tables depending on ``anchor_type``;
    the service validates the record exists when the thread is created."""
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    """Optional human-readable title for the conversation (e.g. "Leaking kitchen tap"); the anchor,
    not this line, is what binds the thread to its record."""
    audience_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    """For an ``announcement`` thread only (Issue #112): the
    :class:`~src.modules.messaging.enums.AudienceType` the audience was resolved from — how the
    broadcast's recipients were chosen (global / an owner's tenants / a property's occupants / a
    managed scope). ``NULL`` for every record-anchored thread, whose participants are derived
    live from the anchor and never stored."""
    audience_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The target id an ``audience_type`` refers to when it needs one — e.g. the property id for a
    ``property_occupants`` broadcast. ``NULL`` for audience kinds that carry no reference (global,
    an owner's whole tenant base) and for every record-anchored thread."""

    messages: Mapped[list[Message]] = relationship(
        "Message",
        back_populates="thread",
        order_by="Message.created_at",
        cascade="all, delete-orphan",
    )
    participants: Mapped[list[MessageParticipant]] = relationship(
        "MessageParticipant",
        back_populates="thread",
        cascade="all, delete-orphan",
    )
    """The **stored** audience of an announcement thread (Issue #112). Empty for record-anchored
    threads, whose participants are derived live from the anchor rather than stored — this list is
    populated only when a broadcast's audience is resolved once at send time."""
