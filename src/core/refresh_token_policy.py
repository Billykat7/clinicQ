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

from sqlalchemy import ColumnElement, and_, func, or_, select, update
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


# --------------------------------------------------------------------------------------
# Token families (Issue 16)
# --------------------------------------------------------------------------------------
#
# A sign-in starts a family: its first refresh row and every row rotated from it share
# ``family_id``. The family is the *session* the user sees and revokes, and the unit a replay
# revokes. Rows written by the release before migration 0002 have no ``family_id``; each is a
# family of one, whose id is its own.


def in_family(family_id: str) -> ColumnElement[bool]:
    """SQL condition: the row belongs to ``family_id`` (a pre-0002 row is its own family).

    Written as an ``OR`` so the ``family_id`` index serves it. Never negate it: for a row whose
    ``family_id`` is NULL, ``family_id = :x`` is NULL rather than false, so ``NOT (...)`` would be
    NULL too and the row would silently match nothing. :func:`outside_family` is the negation.
    """
    return or_(
        RefreshToken.family_id == family_id,
        and_(RefreshToken.family_id.is_(None), RefreshToken.id == family_id),
    )


def outside_family(family_id: str) -> ColumnElement[bool]:
    """SQL condition: the row belongs to any family but ``family_id``; NULL-safe."""
    return func.coalesce(RefreshToken.family_id, RefreshToken.id) != family_id


def revoke_family(db: Session, family_id: str, *, now: datetime | None = None) -> int:
    """Revoke every still-live row of the family; return how many were revoked. Commits."""
    result = db.execute(
        update(RefreshToken)
        .where(in_family(family_id), RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now or datetime.now(UTC))
    )
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def revoke_families_except(
    db: Session, user_id: str, keep_family_id: str | None
) -> None:
    """Revoke every live row of ``user_id`` outside ``keep_family_id`` (all when it is None)."""
    stmt = update(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    )
    if keep_family_id is not None:
        stmt = stmt.where(outside_family(keep_family_id))
    db.execute(stmt.values(revoked_at=datetime.now(UTC)))
    db.commit()


def family_is_live(db: Session, family_id: str) -> bool:
    """Whether the family still has an unrevoked, unexpired row (the session is still signed in)."""
    return (
        db.execute(
            select(RefreshToken.id)
            .where(
                in_family(family_id),
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > datetime.now(UTC),
            )
            .limit(1)
        ).first()
        is not None
    )
