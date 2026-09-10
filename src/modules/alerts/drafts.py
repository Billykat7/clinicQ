"""Alert drafts: an author's private, unsent staff alerts (Issue #132 follow-up).

Mirrors :func:`~src.modules.messaging.drafts` — create, list, read-one, update (partial), delete —
every operation **author-scoped** so one user can never touch, or even learn of, another's drafts.
Sending is not part of this module: it turns a draft into a real alert and then deletes the draft.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.exceptions import AlertDraftNotFoundError
from src.database.models import AlertDraft
from src.modules.messaging.enums import AlertSeverity, AudienceType


def create_draft(
    db: Session,
    *,
    author_id: str,
    audience_type: AudienceType | None = None,
    audience_ref: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    severity: AlertSeverity | None = None,
) -> AlertDraft:
    """Create and flush a new alert draft owned by ``author_id``; return it."""
    draft = AlertDraft(
        author_id=author_id,
        audience_type=audience_type.value if audience_type is not None else None,
        audience_ref=audience_ref,
        subject=subject,
        body=body,
        severity=severity.value if severity is not None else None,
    )
    db.add(draft)
    db.flush()
    return draft


def list_drafts(db: Session, author_id: str) -> list[AlertDraft]:
    """Return ``author_id``'s alert drafts, most-recently-edited first."""
    return list(
        db.execute(
            select(AlertDraft)
            .where(AlertDraft.author_id == author_id)
            .order_by(AlertDraft.modified_at.desc())
        )
        .scalars()
        .all()
    )


def get_draft(db: Session, draft_id: str, author_id: str) -> AlertDraft:
    """Load one of ``author_id``'s alert drafts by id, or raise if missing or not theirs.

    Raises:
        AlertDraftNotFoundError: no such draft owned by ``author_id`` (one belonging to someone
            else is treated as absent, so it leaks nothing).
    """
    draft = db.get(AlertDraft, draft_id)
    if draft is None or draft.author_id != author_id:
        raise AlertDraftNotFoundError(draft_id)
    return draft


# Sentinel so ``update_draft`` can distinguish "leave unchanged" from "set to None" (clear it).
_UNSET = object()


def update_draft(
    db: Session,
    draft_id: str,
    author_id: str,
    *,
    audience_type: AudienceType | None | object = _UNSET,
    audience_ref: str | None | object = _UNSET,
    subject: str | None | object = _UNSET,
    body: str | None | object = _UNSET,
    severity: AlertSeverity | None | object = _UNSET,
) -> AlertDraft:
    """Partially update one of ``author_id``'s alert drafts and return it.

    Only the fields passed are changed; an omitted field is left as-is, while an explicit ``None``
    clears it (the ``_UNSET`` sentinel tells the two apart).

    Raises:
        AlertDraftNotFoundError: no such draft owned by ``author_id``.
    """
    draft = get_draft(db, draft_id, author_id)
    if audience_type is not _UNSET:
        draft.audience_type = (
            audience_type.value  # type: ignore[union-attr]
            if isinstance(audience_type, AudienceType)
            else None
        )
    if audience_ref is not _UNSET:
        draft.audience_ref = audience_ref  # type: ignore[assignment]
    if subject is not _UNSET:
        draft.subject = subject  # type: ignore[assignment]
    if body is not _UNSET:
        draft.body = body  # type: ignore[assignment]
    if severity is not _UNSET:
        draft.severity = (
            severity.value  # type: ignore[union-attr]
            if isinstance(severity, AlertSeverity)
            else None
        )
    db.flush()
    return draft


def delete_draft(db: Session, draft_id: str, author_id: str) -> None:
    """Delete one of ``author_id``'s alert drafts (discard it).

    Raises:
        AlertDraftNotFoundError: no such draft owned by ``author_id``.
    """
    draft = get_draft(db, draft_id, author_id)
    db.delete(draft)
    db.flush()
