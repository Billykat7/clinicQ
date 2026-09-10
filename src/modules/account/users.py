"""User lookups shared across modules.

One question, asked from several places — "is there an account for this address?" — and answered
in one place so the normalisation and the soft-delete filter cannot drift between callers.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import User


def find_user_id_by_email(db: Session, email: str) -> str | None:
    """Return the id of a non-deleted user with ``email``, or ``None`` if there is none.

    The address is normalised the way sign-up stores it (trimmed, lower-cased), so a lookup by
    what someone typed matches the row that was written from what they typed.
    """
    normalized = email.strip().lower()
    if not normalized:
        return None
    return db.execute(
        select(User.id).where(User.email == normalized, User.is_deleted.is_(False))
    ).scalar_one_or_none()


def user_exists(db: Session, user_id: str) -> bool:
    """Return whether a non-deleted user with ``user_id`` exists."""
    found = db.execute(
        select(User.id).where(User.id == user_id, User.is_deleted.is_(False))
    ).scalar_one_or_none()
    return found is not None
