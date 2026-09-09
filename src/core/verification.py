"""Verification-email resend policy shared by self-service and admin flows.

Enforces a per-user cooldown between activation/verification email resends using
``User.last_verification_email_sent_at``. Ported from the ``maps`` project.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status

from src.core.config import Settings
from src.database.models import User


def _as_utc_aware(dt: datetime) -> datetime:
    """Normalize a DB ``datetime`` to UTC-aware (SQLite returns naive values)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def enforce_verification_resend_cooldown(user: User, settings: Settings) -> None:
    """Raise 429 (with ``Retry-After``) when the resend cooldown has not elapsed."""
    sent_at = user.last_verification_email_sent_at
    if sent_at is None:
        return
    cooldown = timedelta(minutes=settings.verification_resend_cooldown_minutes)
    elapsed = datetime.now(UTC) - _as_utc_aware(sent_at)
    if elapsed < cooldown:
        retry_after = max(1, int((cooldown - elapsed).total_seconds()))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Please wait before requesting another verification email.",
            headers={"Retry-After": str(retry_after)},
        )
