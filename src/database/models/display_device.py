"""A kiosk box that shows a clinic's waiting-room board (Issue 61).

A box is installed once and left alone for months, so the row is designed around three moments:

* **Pairing.** A fresh box opens the one start address every box uses (``/display``) and is given a
  device row with no clinic yet, a long secret in an httpOnly cookie, and a short pairing code shown
  on the screen. A clinic manager types the code into the dashboard, which binds the row to their
  clinic. Nobody at the clinic types an address or a password into the box.
* **Showing the board.** The cookie is the box's credential. Like a refresh token (``src/core/security.py``)
  it is stored only as its SHA-256 digest (``token_hash``), so the table cannot be used to impersonate a
  box, and a board address copied from the box's screen shows nothing in another browser.
* **Being watched.** The board page reports every minute (``last_seen_at``, ``app_version``). A paired
  box silent for ``DISPLAY_DEVICE_SILENT_MINUTES`` raises one alert naming its clinic
  (``silent_alerted_at`` keeps it to one per silence), and a revoked box (``revoked_at``) is refused
  everywhere at once.

The pairing code is short because a person reads it off a TV; it is safe because it is stored hashed,
lives ``DISPLAY_PAIRING_CODE_MINUTES``, is useful only to someone signed in as that clinic's manager, and
binds nothing but a board. Datetimes are aware; business time is Africa/Johannesburg.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value

#: JSONB on PostgreSQL, plain JSON elsewhere (the SQLite tests).
_QueueIdsType = JSON().with_variant(JSONB(), "postgresql")


class DisplayDevice(Base, TimestampMixin):
    """One kiosk box: pairing, the clinic and queues it shows, and when it was last heard from."""

    __tablename__ = "display_device"
    __table_args__ = (
        Index("uq_display_device_token_hash", "token_hash", unique=True),
        Index("uq_display_device_pairing_code_hash", "pairing_code_hash", unique=True),
        Index("ix_clinicq_display_device_site", "site_id", "paired_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=True
    )
    """The clinic whose board the box shows. ``None`` until a manager pairs it."""
    label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    """What the manager calls it ("TV by reception"), so an alert says which screen."""
    queue_ids: Mapped[list[str] | None] = mapped_column(_QueueIdsType, nullable=True)
    """The queues this screen shows, or ``None`` for all of the clinic's open queues."""
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    """SHA-256 of the box's secret. The secret itself exists only in the box's cookie."""
    pairing_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """SHA-256 of the code on the box's screen while it waits to be paired; ``None`` once paired."""
    pairing_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the code on the screen stops working; the box then shows a new one."""
    paired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paired_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The manager who typed the code. No foreign key: the record outlives their account."""
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """Set when a manager removes the box; it is refused from that moment."""
    revoked_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """The box's last heartbeat (Africa/Johannesburg)."""
    app_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    """The application version the box's page was loaded from, as it last reported."""
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)
    """The box's browser, as it last reported: helps support tell a Pi from a mini PC."""
    silent_alerted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the team was told this box went silent; cleared when it is heard from again."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures: never the secret or the code."""
        return f"DisplayDevice(id={self.id!r}, site_id={self.site_id!r})"
