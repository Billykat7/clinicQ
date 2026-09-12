"""Request/response payloads for auth endpoints.

Covers self-registration (signup/activation) and the sign-in surface ported from the
``maps`` project: OTP request/verify, password login, token metadata, public feature
flags, the current-user profile, and refresh-token session listings.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, EmailStr, Field

from src.commons.enums import TokenType
from src.core.security import BCRYPT_MAX_PASSWORD_BYTES


def _within_bcrypt_limit(value: str) -> str:
    """Refuse a password bcrypt cannot hash whole (more than 72 UTF-8 bytes).

    Counted in bytes, not characters: ``max_length`` counts characters, and an accented letter or
    an emoji is two to four bytes, so a 60-character password can still be too long for bcrypt.
    """
    if len(value.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes "
            "(72 plain letters or digits; fewer with accented letters or emoji)."
        )
    return value


#: A password being set (reset, change, invitation): 8 characters to 72 bytes. The lower bound is
#: NIST SP 800-63B's; the upper one is bcrypt's (Issue 15).
NewPassword = Annotated[
    str,
    Field(min_length=8, max_length=BCRYPT_MAX_PASSWORD_BYTES),
    AfterValidator(_within_bcrypt_limit),
]


class SignupRequestIn(BaseModel):
    """Self-registration request body."""

    email: EmailStr = Field(description="Email address to register.")


class SignupResponseOut(BaseModel):
    """Generic signup response (identical for new and duplicate emails to avoid enumeration)."""

    message: str = Field(
        default="Check your email to activate your account. Then you can sign in.",
        description="Human-readable next step.",
    )


class OTPRequestIn(BaseModel):
    """Request body for sending an OTP to an email."""

    email: EmailStr = Field(description="Email address to send the OTP to.")


class OTPRequestOut(BaseModel):
    """Response after successfully requesting an OTP."""

    message: str = Field(default="OTP sent", description="Status message.")


class OTPVerifyIn(BaseModel):
    """Request body for verifying an OTP and signing in."""

    email: EmailStr = Field(description="Email address that received the OTP.")
    code: str = Field(min_length=4, max_length=8, description="OTP code.")


class PasswordLoginIn(BaseModel):
    """Request body for password-based sign-in (fallback when OTP is unavailable)."""

    email: EmailStr = Field(description="Email address for the account.")
    password: str = Field(
        min_length=8,
        max_length=128,
        description="Account password.",
    )


class PasswordForgotIn(BaseModel):
    """Request body for initiating a password reset by email."""

    email: EmailStr = Field(description="Account email to receive the reset link.")


class PasswordForgotOut(BaseModel):
    """Generic password-reset response (identical for known and unknown emails)."""

    message: str = Field(
        default="If an account exists for that email, a password reset link has been sent.",
        description="Generic response to avoid account enumeration.",
    )


class PasswordResetIn(BaseModel):
    """Request body for completing a password reset with a signed token."""

    token: str = Field(min_length=20, description="Signed password reset token.")
    new_password: NewPassword = Field(description="New account password.")


class PasswordResetOut(BaseModel):
    """Response after a successful password reset."""

    message: str = Field(
        default="Password reset successful. You can sign in now.",
        description="Confirmation message for the completed reset.",
    )


class ResendVerificationOut(BaseModel):
    """Response after POST /auth/me/resend-verification."""

    message: str = Field(
        default="Verification email sent. Please check your inbox.",
        description="Confirmation that the verification email was dispatched.",
    )


class TokenResponse(BaseModel):
    """Sign-in response metadata. The JWT is delivered in an httpOnly cookie."""

    access_token: str = Field(
        default="",
        description="Empty for browser sessions (JWT in httpOnly cookie).",
    )
    token_type: str = Field(default=TokenType.BEARER.value, description="Token type.")
    email: str = Field(description="Authenticated user email.")


class AuthConfigResponse(BaseModel):
    """Public auth feature flags for the frontend (no auth required)."""

    signup_enabled: bool = Field(
        default=False,
        description="Whether self-service signup is enabled.",
    )
    otp_login_enabled: bool = Field(
        default=True,
        description="Whether OTP email sign-in is enabled.",
    )
    password_login_enabled: bool = Field(
        default=True,
        description="Whether password sign-in is enabled.",
    )


class MeResponse(BaseModel):
    """Current user profile (GET /auth/me)."""

    email: str = Field(description="User email (username).")
    first_name: str | None = Field(default=None, description="First name for display.")
    last_name: str | None = Field(default=None, description="Last name for display.")
    date_of_birth: date | None = Field(default=None, description="Date of birth.")
    avatar_url: str | None = Field(default=None, description="Avatar image URL if set.")
    auth_provider: str | None = Field(
        default=None,
        description="OAuth provider name, or null for email/OTP users.",
    )
    is_verified: bool = Field(
        description="Whether the email address has been verified."
    )
    last_login: datetime | None = Field(
        default=None,
        description="Last successful login (timezone-aware).",
    )
    created_at: datetime = Field(description="Account creation time (timezone-aware).")
    has_password: bool = Field(
        description="Whether the user has set a password (hash is never exposed).",
    )
    role: str = Field(description="Application role for RBAC (e.g. user, admin).")
    permissions: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Effective per-resource permission verbs for the caller's role "
            "(resource key -> max verb), after parent-resource cascade."
        ),
    )


class ProfileUpdateIn(BaseModel):
    """Request body for PATCH /auth/me (update profile).

    Email is deliberately absent: an address change is a sensitive action handled by the
    dedicated, re-verified flow (POST /auth/me/email), never a plain profile edit.
    """

    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    date_of_birth: date | None = Field(default=None)
    avatar_url: str | None = Field(
        default=None,
        max_length=500,
        description="URL of the user's avatar image (empty string clears it).",
    )


class PasswordChangeIn(BaseModel):
    """Request body for POST /auth/me/password (change password while signed in)."""

    current_password: str = Field(
        min_length=1,
        max_length=128,
        description="The account's current password (proves the request is the account holder).",
    )
    new_password: NewPassword = Field(description="The new account password.")


class PasswordChangeOut(BaseModel):
    """Response after a successful in-session password change."""

    message: str = Field(
        default="Password changed. Your other sessions have been signed out.",
        description="Confirmation that the password was changed and other sessions revoked.",
    )


class EmailChangeIn(BaseModel):
    """Request body for POST /auth/me/email (start a re-verified email change)."""

    current_password: str = Field(
        min_length=1,
        max_length=128,
        description="The account's current password (sensitive actions require it).",
    )
    new_email: EmailStr = Field(
        description="The new email address to move the account to."
    )


class EmailChangeRequestOut(BaseModel):
    """Response after requesting an email change (before it is confirmed)."""

    message: str = Field(
        default=(
            "Check your new inbox for a confirmation link. Your email will not change "
            "until you confirm it."
        ),
        description="Confirmation that a verification link was sent to the new address.",
    )


class SessionOut(BaseModel):
    """One active refresh-token session row."""

    id: str = Field(description="Session (refresh token row) id.")
    expires_at: datetime = Field(description="When the refresh token expires.")
    is_current: bool = Field(
        description="True when this row matches the current request refresh cookie.",
    )
    user_agent: str | None = Field(
        default=None,
        description="User-Agent captured at session creation (truncated server-side).",
    )
    sign_in_ip: str | None = Field(
        default=None,
        description="Client IP when this session was created (sign-in).",
    )
    last_seen_at: datetime | None = Field(
        default=None,
        description="Last time this refresh token was validated.",
    )


class SessionListOut(BaseModel):
    """Response for GET /auth/me/sessions."""

    sessions: list[SessionOut]
