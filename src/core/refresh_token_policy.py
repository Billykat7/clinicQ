"""Shared refresh-token session checks used by the auth routes.

Ported from the ``maps`` project. Centralizes expiry/revocation rules so they stay
aligned across endpoints. Two optional caps apply on top of plain TTL/revocation:

- **Absolute maximum session** (``SESSION_ABSOLUTE_MAX_DAYS``) revokes a refresh row
  whose ``session_started_at`` chain began too long ago.
- **Server idle timeout** (``SESSION_SERVER_IDLE_TIMEOUT_MINUTES``) revokes a row whose
  ``last_seen_at`` is too old, so re-auth is required even after a full page reload.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.security import hash_refresh_token
from src.database.models import RefreshToken


def as_utc_aware(dt: datetime) -> datetime:
    """Normalize a DB ``datetime`` to UTC-aware (e.g. SQLite returns naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _absolute_max_deadline_violated(row: RefreshToken, now_utc: datetime) -> bool:
    """Return True when the row's session anchor is past the configured absolute cap."""
    max_days = get_settings().session_absolute_max_days
    if max_days is None:
        return False
    if row.session_started_at is None:
        return False
    anchor = as_utc_aware(row.session_started_at)
    return now_utc > anchor + timedelta(days=max_days)


def _server_idle_deadline_exceeded(row: RefreshToken, now_utc: datetime) -> bool:
    """Return True when ``last_seen_at`` is past the configured server idle window."""
    idle_minutes = get_settings().session_server_idle_timeout_minutes
    if idle_minutes is None:
        return False
    if row.last_seen_at is None:
        return False
    last = as_utc_aware(row.last_seen_at)
    return now_utc >= last + timedelta(minutes=idle_minutes)


def _revoke_row_at(db: Session, row: RefreshToken, now_utc: datetime) -> None:
    """Persist ``revoked_at`` on a single refresh row."""
    db.execute(
        update(RefreshToken).where(RefreshToken.id == row.id).values(revoked_at=now_utc)
    )
    db.commit()


def enforce_server_idle_timeout(db: Session, row: RefreshToken) -> bool:
    """Revoke the row when the server-side idle timeout is exceeded.

    Returns True if the row may still be used; False if it was revoked.
    """
    now = datetime.now(UTC)
    if not _server_idle_deadline_exceeded(row, now):
        return True
    _revoke_row_at(db, row, now)
    return False


def enforce_refresh_row_absolute_max(db: Session, row: RefreshToken) -> bool:
    """Revoke the row when the optional absolute session length is exceeded.

    Returns True if the row may still be used; False if it was revoked.
    """
    now = datetime.now(UTC)
    if not _absolute_max_deadline_violated(row, now):
        return True
    _revoke_row_at(db, row, now)
    return False


def get_valid_refresh_token_row(
    db: Session, raw_token: str | None
) -> RefreshToken | None:
    """Resolve a valid refresh row from the raw cookie value, or None.

    A row is valid when it is unexpired, not revoked, within the absolute-max cap, and
    within the server-idle policy.
    """
    if not raw_token or not raw_token.strip():
        return None
    token_hash = hash_refresh_token(raw_token.strip())
    now = datetime.now(UTC)
    row = (
        db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if not enforce_refresh_row_absolute_max(db, row):
        return None
    if not enforce_server_idle_timeout(db, row):
        return None
    return row
