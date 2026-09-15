"""Notification template versions: every set of words a patient message has ever been sent with (Issue 66).

A message is reproducible only if the words it was rendered from still exist. So a template is never edited
in place: each change is a new, immutable row, and the ledger row of every message names the version it was
sent with (``notification.template_version_id``). Retrying a message renders the same version again, even
if a newer one has been published since.

Versions come from two places:

* **Built in**, from ``src/locales/<language>/notifications.toml``. The service writes each file version it
  meets into this table (``source`` ``builtin``), so a version that later leaves the file is still here.
* **Edited** in the admin editor (``source`` ``edited``), always a higher number than anything before it for
  that template, channel and language, and validated exactly as a built-in one is.

The newest version is the one sent. ``reviewed_by`` credits the fluent speaker who checked a translation; it
is empty until someone has.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.ids import new_id
from src.database.models.base import Base


class NotificationTemplateVersion(Base):
    """One immutable version of one message template, in one channel and language."""

    __tablename__ = "notification_template_version"
    __table_args__ = (
        UniqueConstraint(
            "template_key",
            "channel",
            "language",
            "version",
            name="uq_notification_template_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    template_key: Mapped[str] = mapped_column(String(50), nullable=False)
    """:class:`~src.commons.enums.NotificationTemplate`."""
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    """:class:`~src.commons.enums.NotificationChannel`."""
    language: Mapped[str] = mapped_column(String(5), nullable=False)
    """:class:`~src.commons.enums.BoardLanguage` code."""
    version: Mapped[int] = mapped_column(nullable=False)
    subject: Mapped[str | None] = mapped_column(String(120), nullable=True)
    """A web push's title; ``NULL`` for SMS and WhatsApp."""
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    """:class:`~src.commons.enums.TemplateSource`: from the locale file, or from the editor."""
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Who wrote an edited version (their email); ``NULL`` for a built-in one."""
    reviewed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    """The fluent speaker who checked the words, credited; ``NULL`` until someone has."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"NotificationTemplateVersion({self.template_key}/{self.channel}/{self.language}"
            f" v{self.version})"
        )
