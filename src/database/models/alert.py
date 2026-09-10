"""Alert model: a one-way broadcast, staff-authored or system-raised (Issue #132 follow-up).

Unlike a :class:`~src.database.models.message_thread.MessageThread`, an alert is never a
conversation — there is no reply, so its subject/body live directly on this row rather than on a
separate message hanging off a thread. Two origins, distinguished by
:class:`~src.modules.messaging.enums.AlertSource`:

* ``STAFF`` — a manager/admin composes it and sends to a resolved
  :class:`~src.modules.messaging.enums.AudienceType` (the same audience machinery announcements
  use — ``audience_type``/``audience_ref``). Has an ``author_id``, supports drafts and always
  carries :class:`~src.modules.messaging.enums.AlertKind` ``CUSTOM``.
* ``SYSTEM`` — the platform raises it from a domain condition (rent overdue, a lease expiring); no
  author, links the record it concerns via ``entity_type``/``entity_id`` instead of an audience.
  Building the generator that raises these is a separate, later piece of work — this column shape
  just leaves room for it so the schema will not need to change when it lands.

Recipients are stored explicitly (see
:class:`~src.database.models.alert_recipient.AlertRecipient`), resolved once at send time exactly
like an announcement's audience — not re-derived, so opening an old alert shows exactly who it
reached.

Carries TimestampMixin (created_at/modified_at) and SoftDeleteMixin (the console's Deleted tab).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base
from src.database.models.mixins import SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from src.database.models.alert_recipient import AlertRecipient


class Alert(Base, TimestampMixin, SoftDeleteMixin):
    """A one-way broadcast — staff-composed or system-raised."""

    __tablename__ = "alert"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    """:class:`~src.modules.messaging.enums.AlertSource` — ``staff`` or ``system``."""
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    """:class:`~src.modules.messaging.enums.AlertSeverity` — orders the Inbox, drives its badge."""
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    """:class:`~src.modules.messaging.enums.AlertKind` — ``custom`` for every staff alert; a
    ``system`` alert names the domain condition that raised it."""
    author_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The staff member who composed it; ``NULL`` for a system-raised alert. ``SET NULL`` so the
    alert (and its history) survives the author's account being removed."""
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    """Stored verbatim; escaped only at render time, same convention as a message body."""
    audience_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    """For a ``staff`` alert: the :class:`~src.modules.messaging.enums.AudienceType` its audience
    was resolved from. ``NULL`` for a ``system`` alert, which targets specific recipients directly
    rather than a resolved audience."""
    audience_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The target id ``audience_type`` refers to when it needs one (e.g. a property id)."""
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    """For a ``system`` alert: the kind of record it concerns (e.g. ``lease``). ``NULL`` for a
    ``staff`` alert."""
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The id of the record ``entity_type`` names."""

    recipients: Mapped[list[AlertRecipient]] = relationship(
        "AlertRecipient",
        back_populates="alert",
        cascade="all, delete-orphan",
    )
