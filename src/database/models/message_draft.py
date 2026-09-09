"""MessageDraft model: a private, unsent message the author is still composing (Issue #112).

A draft is a message-in-progress that only its author can see, edit or delete. It is deliberately
*not* a ``message`` row — nothing has been posted, no thread activity, no notification, no read
receipt — until the author sends it, at which point the service turns the draft into a real
message (a reply to a thread, or a resolved-audience announcement) and deletes the draft.

A draft names *where it will go* without committing to it yet:

* ``thread_id`` set — a reply-in-progress to an existing thread. ``SET NULL`` so a draft survives
  the (rare) removal of its target thread; the author simply re-picks a destination before sending.
* ``audience_type`` set — an announcement-in-progress; ``audience_ref`` names the target the kind
  needs (e.g. a property id). The audience is only *resolved and authorised* at send time, never
  stored here, so a draft can never smuggle in a recipient the author is not allowed to reach.

Both may be absent while the author is still deciding; ``subject`` and ``body`` may likewise be
partial. Send-time validation (a non-empty body, a resolvable destination) lives in the service —
a draft is intentionally permissive so composing is never blocked mid-thought.

``author_id`` is ``CASCADE``: a private draft is meaningless once its author's account is gone and,
unlike a posted message, carries no shared history worth preserving. Carries TimestampMixin
(created_at/modified_at); ``modified_at`` is when the draft was last edited.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin


class MessageDraft(Base, TimestampMixin):
    """One author-private, unsent message being composed."""

    __tablename__ = "message_draft"
    __table_args__ = (
        # A draft list is "my drafts, newest edit first"; index the owner + edit time for it.
        Index(
            "ix_clinicq_message_draft_author_id_modified_at",
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
    """The user composing the draft — its sole owner. ``CASCADE`` so a private draft is removed
    with the author's account. Indexed via ``ix_clinicq_message_draft_author_id_modified_at``."""
    thread_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("message_thread.id", ondelete="SET NULL"),
        nullable=True,
    )
    """When set, the existing thread this draft will reply to. ``SET NULL`` so the draft survives
    its target thread's removal (the author re-picks a destination before sending). Mutually
    exclusive with ``audience_type`` in practice — a draft is a reply *or* an announcement."""
    audience_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    """When set, the :class:`~src.modules.messaging.enums.AudienceType` this announcement draft
    will broadcast to. The audience is resolved and authorised only at send time, never stored on
    the draft, so a draft cannot carry recipients the author is not entitled to reach."""
    audience_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The target id an ``audience_type`` needs (e.g. a property id for ``property_occupants``);
    ``NULL`` for audience kinds that carry no reference."""
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    """Optional title carried onto the thread when the draft opens a new announcement."""
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    """The message text so far — nullable because a draft may be saved mid-thought. Send-time
    validation requires a non-empty body; stored verbatim and escaped only at render time."""
