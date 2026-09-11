"""User model: the staff account (Issue 15).

Staff are the only people who sign in with an account, and this is their table: there is no
separate ``staff_users``, because two identity tables are how two sign-in paths drift apart.
Patients are a phone number and a one-time code, never a row here (Issue 17).

* ``password`` is a bcrypt hash (cost 12), NULL for an OTP-only account until one is set. The
  plaintext is never stored; ``tests/integration/database/test_credentials_at_rest.py`` reads the
  raw rows to prove it.
* ``role`` is a mirror of the account's unscoped role for display; RBAC resolves roles from
  ``user_roles`` (:class:`~src.database.models.user_role_assignment.UserRoleAssignment`).
* **A staff member's site is not a column.** It is a role held at that site: a ``user_roles`` row
  with ``scope_type='site'`` (:attr:`~src.commons.enums.AssignmentScopeType.SITE`), so one person
  can hold different roles at two clinics.
* ``is_active`` (ActiveMixin) switches an account off without deleting it: sign-in refuses it, and
  :func:`~src.core.security.find_active_user` refuses its still-unexpired access token on the next
  request. ``is_deleted`` (SoftDeleteMixin) keeps the row, so audit rows stay attributable.
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
