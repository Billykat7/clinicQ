"""Refresh token model for hybrid session auth (short-lived access + refresh).

The raw refresh token is an opaque, URL-safe secret sent only in an httpOnly
cookie. Only its SHA-256 hash is stored here for lookup and revocation.
``expires_at`` / ``revoked_at`` support TTL and rotation; ``user_agent`` /
``sign_in_ip`` / ``last_seen_at`` support the session list, and
``session_started_at`` anchors an optional absolute session cap.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class RefreshToken(Base):
    """Long-lived refresh token: hashed value, owning user, expiry, and revocation."""

    __tablename__ = "refresh_token"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    """SHA-256 hex digest of the raw refresh token (never store the raw value)."""
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sign_in_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    session_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """UTC anchor for an optional absolute max session; copied on refresh rotation."""
