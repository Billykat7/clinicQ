"""HTTP routes for staff-authored alerts (Issue #132 follow-up).

Mirrors ``messaging``'s announcement+draft surface, minus the reply half:

* ``POST   /alerts``             — compose and send a staff alert to a resolved audience.
* ``GET    /alerts``             — list the caller's alerts for one console tab (inbox/sent/deleted).
* ``POST   /alerts/{id}/read``   — mark an alert read for the caller.
* ``POST   /alerts/{id}/unread`` — undo that.
* ``DELETE /alerts/{id}``        — soft-delete (author-scoped); ``POST .../restore`` undoes it.
* ``GET/POST/PATCH/DELETE /alerts/drafts`` + ``POST /alerts/drafts/{id}/send`` — the compose seam.

Gated on the ``communications.alerts`` RBAC resource (READ to view, CREATE to send/draft) — unlike
messaging's participation-based access, an alert has a real RBAC floor since it is a genuinely new
console, not an evolution of an always-open messaging surface.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from src.api.rbac_deps import require
from src.commons.enums import GrantScope
from src.commons.exceptions import (
    AlertDraftNotFoundError,
    AudienceNotAuthorisedError,
    EmptyAudienceError,
)
from src.core.rbac import ensure_named_action_permission_key, ensure_permission_key
from src.core.security import get_current_user
from src.database.models import Alert, User
from src.database.session import get_db
from src.modules.account.users import find_user_id_by_email
from src.modules.alerts import drafts as drafts_service
from src.modules.alerts import service
from src.modules.alerts.schemas import (
    AlertCreateIn,
    AlertDraftCreateIn,
    AlertDraftRead,
    AlertDraftUpdateIn,
    AlertListOut,
    AlertMarkReadResult,
    AlertRead,
)
from src.modules.messaging.enums import (
    AlertKind,
    AlertSeverity,
    AlertSource,
    AudienceType,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[dict, Depends(get_current_user)]

# Issue #163: resolved through the generic ``require`` factory (src.api.rbac_deps) against the
# manifest-registered ``communications.alerts`` resource, rather than the hand-written named
# functions the earlier ``communications`` work left behind (see Issue #149's Applications pilot).
CommunicationsAlertsReadDep = Annotated[
    None, Depends(require("communications.alerts", "read", scope=GrantScope.BUSINESS))
]
CommunicationsAlertsCreateDep = Annotated[
    None, Depends(require("communications.alerts", "create"))
]


def _require_user_id(db: Session, current_user: dict) -> str:
    """Resolve the signed-in caller's user id, or raise 401 when it cannot be resolved."""
    email = (current_user.get("email") or "").strip()
    user_id = find_user_id_by_email(db, email) if email else None
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return user_id


def _require_user_row(db: Session, current_user: dict) -> User:
    """Resolve the signed-in caller's full ``User`` row, or raise 401.

    Sending needs the sender's *role* (to authorise the audience), not just their id.
    """
    user_id = _require_user_id(db, current_user)
    user = db.get(User, user_id)
    if user is None:  # pragma: no cover — id came from a live lookup a line earlier
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return user


def _alert_read(db: Session, alert: Alert, *, user_id: str) -> AlertRead:
    """Project one alert, resolving the author's display name and the caller's read state."""
    author_name: str | None = None
    if alert.author_id is not None:
        author = db.get(User, alert.author_id)
        if author is not None:
            parts = [p for p in (author.first_name, author.last_name) if p]
            author_name = " ".join(parts) if parts else None
    recipient_row = service.get_recipient_row(db, alert.id, user_id)
    return AlertRead(
        id=alert.id,
        source=AlertSource(alert.source),
        severity=AlertSeverity(alert.severity),
        kind=AlertKind(alert.kind),
        author_id=alert.author_id,
        author_name=author_name,
        subject=alert.subject,
        body=alert.body,
        audience_type=AudienceType(alert.audience_type)
        if alert.audience_type
        else None,
        audience_ref=alert.audience_ref,
        created_at=alert.created_at,
        is_read=recipient_row is not None and recipient_row.read_at is not None,
        read_at=recipient_row.read_at if recipient_row is not None else None,
    )


def _draft_read(draft: object) -> AlertDraftRead:
    """Project one alert draft for the API (author-private; already author-scoped when loaded)."""
    return AlertDraftRead.model_validate(draft)


def _post_alert(
    db: Session,
    *,
    sender: User,
    audience_type: AudienceType | str,
    audience_ref: str | None,
    subject: str,
    body: str,
    severity: AlertSeverity | str,
) -> AlertRead:
    """Resolve+authorise an audience, create the alert, return its projection.

    Shared by the direct create endpoint and draft-send. Maps the audience errors to HTTP: an
    unauthorised audience is 403, an empty one 409.
    """
    try:
        alert = service.create_alert(
            db,
            sender=sender,
            audience_type=AudienceType(audience_type),
            audience_ref=audience_ref,
            subject=subject,
            body=body,
            severity=AlertSeverity(severity),
        )
    except AudienceNotAuthorisedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except EmptyAudienceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(alert)
    return _alert_read(db, alert, user_id=sender.id)


def _load_own_alert(db: Session, alert_id: str) -> Alert:
    alert = service.get_alert(db, alert_id)
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found"
        )
    return alert


@router.post("", response_model=AlertRead, status_code=status.HTTP_201_CREATED)
def create_alert(
    _rbac: CommunicationsAlertsCreateDep,
    body: AlertCreateIn,
    db: DbSession,
    current_user: CurrentUser,
) -> AlertRead:
    """Compose and send a staff alert to a resolved audience in one step."""
    sender = _require_user_row(db, current_user)
    return _post_alert(
        db,
        sender=sender,
        audience_type=body.audience_type,
        audience_ref=body.audience_ref,
        subject=body.subject,
        body=body.body,
        severity=body.severity,
    )


@router.get("", response_model=AlertListOut)
def list_alerts(
    db: DbSession,
    current_user: CurrentUser,
    folder: Annotated[
        str,
        Query(description="``inbox`` (default), ``sent`` or ``deleted``."),
    ] = "inbox",
    offset: Annotated[int, Query(ge=0, description="Row offset for pagination.")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Max rows per page.")] = 20,
) -> AlertListOut:
    """List the caller's alerts for one console tab, newest first.

    Gated per tab (Issue #145) — ``communications.alerts.{folder}`` READ, e.g.
    ``communications.alerts.sent`` for the Sent tab — instead of the coarse module verb, so a role
    can be scoped to a single tab. A coarse ``communications.alerts`` grant still reaches every tab
    unchanged, via inheritance.
    """
    if folder not in ("inbox", "sent", "deleted"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="folder must be one of: inbox, sent, deleted.",
        )
    ensure_permission_key(db, current_user, f"communications.alerts.{folder}", "read")
    user_id = _require_user_id(db, current_user)
    alerts, total = service.list_alerts(
        db, user_id=user_id, folder=folder, offset=offset, limit=limit
    )
    return AlertListOut(
        items=[_alert_read(db, a, user_id=user_id) for a in alerts], total=total
    )


@router.post("/{alert_id}/read", response_model=AlertMarkReadResult)
def mark_alert_read(
    _rbac: CommunicationsAlertsReadDep,
    alert_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> AlertMarkReadResult:
    """Mark an alert read for the caller; 404 if they are not a recipient/sender of it."""
    user_id = _require_user_id(db, current_user)
    row = service.mark_alert_read(db, alert_id, user_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found"
        )
    db.commit()
    return AlertMarkReadResult(is_read=row.read_at is not None)


@router.post("/{alert_id}/unread", response_model=AlertMarkReadResult)
def mark_alert_unread(
    _rbac: CommunicationsAlertsReadDep,
    alert_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> AlertMarkReadResult:
    """Undo :func:`mark_alert_read`."""
    user_id = _require_user_id(db, current_user)
    row = service.mark_alert_unread(db, alert_id, user_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found"
        )
    db.commit()
    return AlertMarkReadResult(is_read=row.read_at is not None)


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert(
    _rbac: CommunicationsAlertsCreateDep,
    alert_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    """Soft-delete an alert — the console's Deleted tab. Author-scoped; reversible, see restore."""
    user_id = _require_user_id(db, current_user)
    alert = _load_own_alert(db, alert_id)
    if alert.author_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found"
        )
    service.soft_delete_alert(db, alert)
    db.commit()


@router.post("/{alert_id}/restore", response_model=AlertRead)
def restore_alert(
    _rbac: CommunicationsAlertsCreateDep,
    alert_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> AlertRead:
    """Undo a soft-delete, returning an alert to its Sent tab. Author-scoped."""
    user_id = _require_user_id(db, current_user)
    alert = _load_own_alert(db, alert_id)
    if alert.author_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found"
        )
    service.restore_alert(db, alert)
    db.commit()
    return _alert_read(db, alert, user_id=user_id)


# --------------------------------------------------------------------------------------
# Drafts (Issue #132 follow-up).
# --------------------------------------------------------------------------------------


@router.post(
    "/drafts", response_model=AlertDraftRead, status_code=status.HTTP_201_CREATED
)
def create_draft(
    body: AlertDraftCreateIn,
    db: DbSession,
    current_user: CurrentUser,
) -> AlertDraftRead:
    """Save a new private alert draft for the caller. Validated only at send time.

    Gated on ``communications.alerts.drafts`` CREATE (Issue #145) rather than the coarse module
    verb — alerts drafts are their own table (``AlertDraft``), cleanly separable from the other
    tabs, unlike messages/announcements drafts (see ``rbac_manifest.py``'s module docstring).
    """
    ensure_permission_key(db, current_user, "communications.alerts.drafts", "create")
    user_id = _require_user_id(db, current_user)
    draft = drafts_service.create_draft(
        db,
        author_id=user_id,
        audience_type=body.audience_type,
        audience_ref=body.audience_ref,
        subject=body.subject,
        body=body.body,
        severity=body.severity,
    )
    db.commit()
    db.refresh(draft)
    return _draft_read(draft)


@router.get("/drafts", response_model=list[AlertDraftRead])
def list_drafts(db: DbSession, current_user: CurrentUser) -> list[AlertDraftRead]:
    """List the caller's private alert drafts, most-recently-edited first.

    Gated on ``communications.alerts.drafts`` READ (Issue #145) — see :func:`create_draft`.
    """
    ensure_permission_key(db, current_user, "communications.alerts.drafts", "read")
    user_id = _require_user_id(db, current_user)
    return [_draft_read(d) for d in drafts_service.list_drafts(db, user_id)]


@router.get("/drafts/{draft_id}", response_model=AlertDraftRead)
def get_draft(
    draft_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> AlertDraftRead:
    """Return one of the caller's alert drafts; one that is not theirs is 404.

    Gated on ``communications.alerts.drafts`` READ (Issue #145) — see :func:`create_draft`.
    """
    ensure_permission_key(db, current_user, "communications.alerts.drafts", "read")
    user_id = _require_user_id(db, current_user)
    try:
        draft = drafts_service.get_draft(db, draft_id, user_id)
    except AlertDraftNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return _draft_read(draft)


@router.patch("/drafts/{draft_id}", response_model=AlertDraftRead)
def update_draft(
    draft_id: str,
    body: AlertDraftUpdateIn,
    db: DbSession,
    current_user: CurrentUser,
) -> AlertDraftRead:
    """Partially update one of the caller's alert drafts — only the fields sent are changed.

    Gated on ``communications.alerts.drafts`` CREATE (Issue #145) — see :func:`create_draft`.
    """
    ensure_permission_key(db, current_user, "communications.alerts.drafts", "create")
    user_id = _require_user_id(db, current_user)
    changes = body.model_dump(exclude_unset=True)
    try:
        draft = drafts_service.update_draft(db, draft_id, user_id, **changes)
    except AlertDraftNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(draft)
    return _draft_read(draft)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    draft_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    """Discard one of the caller's alert drafts. One that is not theirs is 404.

    Gated on ``communications.alerts.drafts`` CREATE (Issue #145) — see :func:`create_draft`.
    """
    ensure_permission_key(db, current_user, "communications.alerts.drafts", "create")
    user_id = _require_user_id(db, current_user)
    try:
        drafts_service.delete_draft(db, draft_id, user_id)
    except AlertDraftNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    db.commit()


@router.post(
    "/drafts/{draft_id}/send",
    response_model=AlertRead,
    status_code=status.HTTP_201_CREATED,
)
def send_draft(
    draft_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> AlertRead:
    """Send one of the caller's alert drafts: create the real alert, then discard the draft.

    A draft missing a body, subject, audience or severity is 422 — every field a real alert
    requires. Requires ``communications.alerts.drafts`` CREATE (Issue #145, same as every other
    drafts mutation) **and** the named ``send`` action on the same resource — this is a real,
    irreversible dispatch transition (a private draft becomes a real broadcast with resolved
    recipients), the same shape ``lease.signature:sign``/``application.screening:approve`` already
    cover, so it is independently grantable/revocable from the plain drafts CRUD verb.
    """
    ensure_permission_key(db, current_user, "communications.alerts.drafts", "create")
    ensure_named_action_permission_key(
        db, current_user, "communications.alerts.drafts", "send"
    )
    sender = _require_user_row(db, current_user)
    try:
        draft = drafts_service.get_draft(db, draft_id, sender.id)
    except AlertDraftNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    clean_body = (draft.body or "").strip()
    clean_subject = (draft.subject or "").strip()
    if not clean_body or not clean_subject:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An alert draft needs a subject and body before it can be sent.",
        )
    if draft.audience_type is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An alert draft needs an audience before it can be sent.",
        )
    if draft.severity is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An alert draft needs a severity before it can be sent.",
        )

    result = _post_alert(
        db,
        sender=sender,
        audience_type=draft.audience_type,
        audience_ref=draft.audience_ref,
        subject=clean_subject,
        body=clean_body,
        severity=draft.severity,
    )
    drafts_service.delete_draft(db, draft_id, sender.id)
    db.commit()
    return result
