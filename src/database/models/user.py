"""User model: email identity, optional password, verification, role, OAuth linkage.

Ported from the ``maps`` project. OTP-only / OAuth users have ``password`` NULL
until they set one. ``role`` is a string for verb-based RBAC (see later issues).
Includes TimestampMixin (created_at/modified_at), ActiveMixin (is_active), and
SoftDeleteMixin (is_deleted).
"""

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import UserRole
from src.database.models.base import Base
from src.database.models.mixins import ActiveMixin, SoftDeleteMixin, TimestampMixin


class User(Base, TimestampMixin, ActiveMixin, SoftDeleteMixin):
    """User account: email, optional password hash, verification, profile, role."""

    __tablename__ = "user"
    __table_args__ = (
        UniqueConstraint(
            "auth_provider",
            "auth_provider_sub",
            name="uq_user_auth_provider_sub",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Bcrypt hash; set when the user chooses a password; NULL for OTP/OAuth-only."""
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default="false",
    )
    """True after the user has clicked the activation link from the signup email."""
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=UserRole.USER.value,
        server_default=UserRole.USER.value,
    )
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    """An externally hosted avatar image URL, set directly through ``PATCH /auth/me``. Superseded
    by :attr:`avatar_key` when the user uploads a picture (Issue #130): the uploaded one wins, so
    there is exactly one answer to "what is this user's avatar"."""
    avatar_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Opaque token identifying the user's **uploaded** profile picture in private storage
    (Issue #130), or NULL when they have none. Random and re-issued on every upload, so it
    discloses nothing about the user, cannot be guessed from another's, and retires the previous
    serving URL the moment a picture is replaced or removed."""
    auth_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    """OAuth provider name (e.g. google); NULL for email/OTP users."""
    auth_provider_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Provider's user id (sub); NULL for email/OTP users. Unique with auth_provider."""
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """Datetime of the user's last successful login."""
    last_verification_email_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    """When the last activation/verification email was sent (drives the resend cooldown)."""
