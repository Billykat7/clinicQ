"""API routes for the notification service (Issue #67).

Two endpoints, both operational rather than end-user facing:

* ``GET /notifications/{notification_id}`` — read one message's delivery status. Gated by the
  ``logs`` READ verb (:data:`LogsReadDep`): delivery status is operational data, like the S3 log
  viewer, so it reuses that admin permission rather than introducing a new RBAC resource.
* ``POST /notifications/webhooks/delivery`` — accept a provider's delivery-status callback and
  advance the matching row to ``delivered`` (or record a bounce). Authenticated by a shared secret
  in the ``X-Webhook-Secret`` header (providers cannot present a user session); disabled unless
  ``NOTIFICATION_WEBHOOK_SECRET`` is configured.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.rbac_deps import require
from src.commons.enums import (
    GrantScope,
    NotificationCategory,
    NotificationChannel,
    NotificationStatus,
)
from src.commons.exceptions import InAppNotificationNotFoundError
from src.core.config import Settings, get_settings
from src.core.security import get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.modules.notifications import center, preferences, service
from src.modules.notifications.center import CenterItem
from src.modules.notifications.preferences import InvalidTimezoneError
from src.modules.notifications.schemas import (
    CenterItemRead,
    CenterMarkResult,
    CenterSummaryOut,
    CenterUnreadOut,
    DeliveryReceipt,
    NotificationListOut,
    NotificationPreferencesRead,
    NotificationPreferencesUpdate,
    NotificationRead,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Issue #152: resolved through the generic ``require`` factory (src.api.rbac_deps) instead of the
# named ``require_logs_read`` function — mirrors the Applications pilot (Issue #149).
# Issue #166 (M28): ``business`` tier — this backs a whole-business back-office console,
# and its nav destination declares the same tier, a parity
# ``tests/unit/security/test_nav_enforcement_parity.py`` now asserts. No seeded role loses
# access: every role that holds this grant holds it at ``business`` already.
LogsReadDep = Annotated[
    None, Depends(require("logs", "read", scope=GrantScope.BUSINESS))
]

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CurrentUser = Annotated[dict, Depends(get_current_user)]

# Issue #163: resolved through the generic ``require`` factory (src.api.rbac_deps) against the
# manifest-registered ``communications.notifications`` resource, rather than the hand-written named
# function the earlier ``communications`` work left behind (see Issue #149's Applications pilot).
CommunicationsNotificationsReadDep = Annotated[
    None, Depends(require("communications.notifications", "read"))
]


def _resolve_user(db: Session, claims: dict[str, Any]) -> User:
    """Resolve the signed-in user row from JWT claims; 401 when it cannot be resolved."""
    email = (claims.get("email") or claims.get("sub") or "").strip().lower()
    user = (
        db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if email
        else None
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    return user


@router.get("/preferences", response_model=NotificationPreferencesRead)
def get_my_notification_preferences(
    db: DbSession,
    current_user: CurrentUser,
) -> NotificationPreferencesRead:
    """Return the signed-in user's effective notification preferences (defaults where unset)."""
    user = _resolve_user(db, current_user)
    return preferences.read_preferences(db, user.id)


@router.put("/preferences", response_model=NotificationPreferencesRead)
def update_my_notification_preferences(
    db: DbSession,
    current_user: CurrentUser,
    body: NotificationPreferencesUpdate,
) -> NotificationPreferencesRead:
    """Update the signed-in user's notification preferences (partial) and return the new view.

    An essential category cannot be turned off (an ``off`` is coerced back to email); an unknown
    timezone is a 422.
    """
    user = _resolve_user(db, current_user)
    try:
        view = preferences.update_preferences(db, user.id, body)
    except InvalidTimezoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown timezone: {exc}",
        ) from exc
    db.commit()
    return view


@router.get("", response_model=NotificationListOut)
def list_notifications(
    db: DbSession,
    _: LogsReadDep,
    status_filter: Annotated[
        NotificationStatus | None,
        Query(alias="status", description="Filter by delivery status."),
    ] = None,
    channel: Annotated[
        NotificationChannel | None, Query(description="Filter by delivery channel.")
    ] = None,
    recipient: Annotated[
        str | None,
        Query(description="Case-insensitive match on the recipient address."),
    ] = None,
    offset: Annotated[int, Query(ge=0, description="Row offset for pagination.")] = 0,
    limit: Annotated[int, Query(ge=1, le=100, description="Max rows per page.")] = 20,
) -> NotificationListOut:
    """List recent notifications with their delivery status (admin; ``logs`` READ).

    The operational delivery viewer: newest first, optionally narrowed by status, channel and
    recipient, paginated with ``offset``/``limit``. ``total`` is the match count before paging.
    """
    rows, total = service.list_notifications(
        db,
        status=status_filter,
        channel=channel,
        recipient=recipient,
        offset=offset,
        limit=limit,
    )
    return NotificationListOut(
        items=[NotificationRead.model_validate(r) for r in rows], total=total
    )


# --------------------------------------------------------------------------------------
# Notification centre — the per-user in-app feed behind the shell bell (Issue #113).
#
# Declared before the admin ``GET /{notification_id}`` status route so ``/center`` is never
# captured as a notification id. Every route is session-scoped to the signed-in user's own feed
# **and** gated on ``communications.notifications`` READ (Issue #125): a user only ever sees,
# counts and marks their own notifications, and must hold the centre grant to reach it at all.
# The base ``user`` role holds that grant (migration ``0044``), so a standard signed-in user keeps
# their bell; a custom role can be scoped out of the centre.
# --------------------------------------------------------------------------------------


def _center_item_read(item: CenterItem) -> CenterItemRead:
    """Project one merged centre item for the API."""
    return CenterItemRead(
        id=item.id,
        source=item.source.value,
        category=NotificationCategory(item.category),
        title=item.title,
        snippet=item.snippet,
        created_at=item.created_at,
        read=item.read,
        link=item.link,
    )


@router.get("/center", response_model=CenterSummaryOut)
def get_notification_center(
    db: DbSession,
    current_user: CurrentUser,
    _: CommunicationsNotificationsReadDep,
    limit: Annotated[
        int, Query(ge=1, le=50, description="Max previews to return.")
    ] = 20,
) -> CenterSummaryOut:
    """Return the bell payload: the unread total and the recent merged previews for the caller."""
    user = _resolve_user(db, current_user)
    return CenterSummaryOut(
        unread_total=center.unread_total(db, user.id),
        items=[
            _center_item_read(i) for i in center.recent_items(db, user.id, limit=limit)
        ],
    )


@router.get("/center/unread-count", response_model=CenterUnreadOut)
def get_notification_center_unread_count(
    db: DbSession,
    current_user: CurrentUser,
    _: CommunicationsNotificationsReadDep,
) -> CenterUnreadOut:
    """Return just the bell badge total — the lightweight endpoint the shell polls."""
    user = _resolve_user(db, current_user)
    return CenterUnreadOut(unread_total=center.unread_total(db, user.id))


@router.post("/center/read-all", response_model=CenterMarkResult)
def mark_notification_center_all_read(
    db: DbSession,
    current_user: CurrentUser,
    _: CommunicationsNotificationsReadDep,
) -> CenterMarkResult:
    """Mark every in-app notification read for the caller; return the new bell total."""
    user = _resolve_user(db, current_user)
    changed = center.mark_all_read(db, user.id)
    db.commit()
    return CenterMarkResult(
        unread_total=center.unread_total(db, user.id), changed=changed
    )


@router.post("/center/{item_id}/read", response_model=CenterMarkResult)
def mark_notification_read(
    item_id: str,
    db: DbSession,
    current_user: CurrentUser,
    _: CommunicationsNotificationsReadDep,
) -> CenterMarkResult:
    """Mark one of the caller's in-app notifications read; return the new bell total (404 if not theirs)."""
    user = _resolve_user(db, current_user)
    try:
        center.mark_read(db, user.id, item_id)
    except InAppNotificationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    db.commit()
    return CenterMarkResult(unread_total=center.unread_total(db, user.id))


@router.post("/center/{item_id}/unread", response_model=CenterMarkResult)
def mark_notification_unread(
    item_id: str,
    db: DbSession,
    current_user: CurrentUser,
    _: CommunicationsNotificationsReadDep,
) -> CenterMarkResult:
    """Mark one of the caller's in-app notifications unread; return the new bell total (404 if not theirs)."""
    user = _resolve_user(db, current_user)
    try:
        center.mark_unread(db, user.id, item_id)
    except InAppNotificationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    db.commit()
    return CenterMarkResult(unread_total=center.unread_total(db, user.id))


@router.get("/{notification_id}", response_model=NotificationRead)
def get_notification_status(
    notification_id: str,
    db: DbSession,
    _: LogsReadDep,
) -> NotificationRead:
    """Return the delivery status of one notification (admin; ``logs`` READ)."""
    notification = service.get_status(db, notification_id)
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    return NotificationRead.model_validate(notification)


@router.post(
    "/webhooks/delivery",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delivery_status_webhook(
    receipt: DeliveryReceipt,
    db: DbSession,
    settings: SettingsDep,
    x_webhook_secret: Annotated[str | None, Header()] = None,
) -> None:
    """Apply a provider delivery-status callback to its notification row.

    Rejects the call with 404 when the webhook is not configured (no shared secret set), 401 when
    the presented secret does not match, and 404 when no row carries the given
    ``provider_message_id``. A match advances the row to ``delivered`` (or records a bounce).
    """
    expected = settings.notification_webhook_secret
    if not expected:
        # Feature-off: do not reveal the endpoint exists when no secret is configured.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret"
        )
    if not service.record_delivery_status(db, receipt):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown provider_message_id"
        )
    # get_db does not commit; persist the status advance before the 204.
    db.commit()
