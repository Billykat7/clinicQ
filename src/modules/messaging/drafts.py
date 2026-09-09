"""Message drafts: an author's private, unsent messages (Issue #112).

A draft is a message-in-progress only its author can see, edit or delete. This module is the CRUD
seam for that — create, list, read-one, update (partial), delete — every operation **author-scoped**
so one user can never touch, or even learn of, another's drafts: a draft owned by someone else
surfaces as :class:`~src.commons.exceptions.MessageDraftNotFoundError`, indistinguishable from one
that never existed.

Drafts are intentionally permissive (a partial subject/body, a not-yet-chosen destination) so
composing is never blocked mid-thought; the *send-time* rules — a non-empty body, a resolvable and
authorised destination — are enforced when a draft is actually sent (in the router, which owns the
transaction and the notification hook). Sending is not part of this module: it turns a draft into a
real posted message and then deletes it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.exceptions import MessageDraftNotFoundError
from src.database.models import MessageDraft
from src.modules.messaging.enums import AudienceType


def create_draft(
    db: Session,
    *,
    author_id: str,
    thread_id: str | None = None,
    audience_type: AudienceType | None = None,
    audience_ref: str | None = None,
    subject: str | None = None,
    body: str | None = None,
) -> MessageDraft:
    """Create and flush a new draft owned by ``author_id``; return it.

    Every field is optional so a draft can be saved the moment composing begins. The destination
    (a ``thread_id`` reply or an ``audience_type`` announcement) is recorded but not validated or
    authorised here — that happens at send time, so a draft can never smuggle in a destination the
    author is not entitled to use.
    """
    draft = MessageDraft(
        author_id=author_id,
        thread_id=thread_id,
        audience_type=audience_type.value if audience_type is not None else None,
        audience_ref=audience_ref,
        subject=subject,
        body=body,
    )
    db.add(draft)
    db.flush()
    return draft


def list_drafts(db: Session, author_id: str) -> list[MessageDraft]:
    """Return ``author_id``'s drafts, most-recently-edited first (their private compose list)."""
    return list(
        db.execute(
            select(MessageDraft)
            .where(MessageDraft.author_id == author_id)
            .order_by(MessageDraft.modified_at.desc())
        )
        .scalars()
        .all()
    )


def get_draft(db: Session, draft_id: str, author_id: str) -> MessageDraft:
    """Load one of ``author_id``'s drafts by id, or raise if it is missing or not theirs.

    Raises:
        MessageDraftNotFoundError: no such draft owned by ``author_id`` (a draft belonging to
            someone else is treated as absent, so it leaks nothing).
    """
    draft = db.get(MessageDraft, draft_id)
    if draft is None or draft.author_id != author_id:
        raise MessageDraftNotFoundError(draft_id)
    return draft


# Sentinel so ``update_draft`` can distinguish "leave unchanged" from "set to None" (clear it).
_UNSET = object()


def update_draft(
    db: Session,
    draft_id: str,
    author_id: str,
    *,
    thread_id: str | None | object = _UNSET,
    audience_type: AudienceType | None | object = _UNSET,
    audience_ref: str | None | object = _UNSET,
    subject: str | None | object = _UNSET,
    body: str | None | object = _UNSET,
) -> MessageDraft:
    """Partially update one of ``author_id``'s drafts and return it.

    Only the fields passed are changed; an omitted field is left as-is, while an explicit ``None``
    clears it (the ``_UNSET`` sentinel tells the two apart). Author-scoped via :func:`get_draft`.

    Raises:
        MessageDraftNotFoundError: no such draft owned by ``author_id``.
    """
    draft = get_draft(db, draft_id, author_id)
    if thread_id is not _UNSET:
        draft.thread_id = thread_id  # type: ignore[assignment]
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
    db.flush()
    return draft


def delete_draft(db: Session, draft_id: str, author_id: str) -> None:
    """Delete one of ``author_id``'s drafts (discard it).

    Raises:
        MessageDraftNotFoundError: no such draft owned by ``author_id``.
    """
    draft = get_draft(db, draft_id, author_id)
    db.delete(draft)
    db.flush()
