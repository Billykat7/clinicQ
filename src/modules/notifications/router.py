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
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from src.api.rbac_deps import require, require_patient
from src.commons.enums import (
    GrantScope,
    NotificationCategory,
    NotificationChannel,
    NotificationStatus,
    PermissionVerb,
    PreferenceSource,
)
from src.commons.exceptions import InAppNotificationNotFoundError
from src.commons.time import now_sast
from src.core.config import Settings, get_settings
from src.core.s3_logging import APP_TIMEZONE
from src.core.security import get_current_user, resolve_active_user
from src.database.models.patient import Patient
from src.database.models.user import User
from src.database.session import get_db
from src.modules.notifications import (
    CENTRE_RESOURCE_KEY,
    budget,
    center,
    delivery_stats,
    patient_preferences,
    preferences,
    service,
    webpush,
)
from src.modules.notifications.center import CenterItem
from src.modules.notifications.preferences import InvalidTimezoneError
from src.modules.notifications.schemas import (
    CenterItemRead,
    CenterMarkResult,
    CenterSummaryOut,
    CenterUnreadOut,
    ChannelDeliveryStatsOut,
    DeliveryReceipt,
    DeliveryStatsOut,
    NotificationListOut,
    NotificationPreferencesRead,
    NotificationPreferencesUpdate,
    NotificationRead,
    PatientPreferencesIn,
    PatientPreferencesOut,
    PushSubscriptionIn,
    PushSubscriptionOut,
    PushUnsubscribeIn,
    SmsKillSwitchIn,
    SmsKillSwitchOut,
    WebPushKeyOut,
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
#: A patient acting for themselves: their own browsers' push subscriptions (Issue 64).
PatientSelf = Annotated[Patient, Depends(require_patient("patients.self", "update"))]

# Issue #163: resolved through the generic ``require`` factory (src.api.rbac_deps) against the
# manifest-registered ``communications.notifications`` resource, rather than the hand-written named
# function the earlier ``communications`` work left behind (see Issue #149's Applications pilot).
CommunicationsNotificationsReadDep = Annotated[
    None, Depends(require(CENTRE_RESOURCE_KEY, PermissionVerb.READ.value))
]


def _resolve_user(db: Session, claims: dict[str, Any]) -> User:
    """Resolve the signed-in user row from JWT claims; 401 when it cannot act (Issue 15)."""
    return resolve_active_user(db, claims)


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
# The grants ship in ``src/modules/communications/rbac_manifest.py`` (``admin``, and ``platform_admin``
# at ``own``), and the dashboard layout offers the bell only to a caller who holds one (Refs #48).
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


# ---------------------------------------------------------------------------------------------------
# A patient's preferences, by their ticket page's link (Issue 67): no account.
# ---------------------------------------------------------------------------------------------------


def _patient_for_link(db: Session, page_token: str) -> str:
    """The patient a ticket page link belongs to, or 404 (an unknown link, or a walk-in with no patient)."""
    from src.modules.queue.ticket_page import find_by_page_token

    ticket = find_by_page_token(db, page_token)
    if ticket is None or ticket.patient_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such ticket.")
    return ticket.patient_id


@router.get("/patient-preferences/{page_token}", response_model=PatientPreferencesOut)
def get_patient_preferences(page_token: str, db: DbSession) -> PatientPreferencesOut:
    """How and when the ticket's patient is told about it: their preferences, for every clinic.

    Reached by the ticket page's unguessable link, because a patient has no account. The link finds the
    patient's preferences and nothing else about them.
    """
    return patient_preferences.read(db, _patient_for_link(db, page_token))


@router.put("/patient-preferences/{page_token}", response_model=PatientPreferencesOut)
def update_patient_preferences(
    page_token: str, payload: PatientPreferencesIn, db: DbSession
) -> PatientPreferencesOut:
    """Change the ticket's patient's preferences; the next message, on every channel, follows them.

    ``opted_out: true`` stops every message at once, including any already waiting to be sent. ``422`` for a
    channel a patient cannot prefer, a language messages are not written in, or half a quiet-hours window.
    """
    patient_id = _patient_for_link(db, page_token)
    try:
        out = patient_preferences.update(
            db, patient_id, payload, source=PreferenceSource.TICKET_PAGE
        )
    except patient_preferences.PreferenceError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    db.commit()
    return out


# ---------------------------------------------------------------------------------------------------
# The SMS kill switch (Issue 65): an operator stops every SMS at once, with no deploy.
# ---------------------------------------------------------------------------------------------------

LogsUpdateDep = Annotated[
    None, Depends(require("logs", "update", scope=GrantScope.BUSINESS))
]


def _kill_switch_out(db: Session) -> SmsKillSwitchOut:
    """The switch as it stands (off when nobody has ever flipped it), and the deployment's SMS flag."""
    sms_enabled = get_settings().sms_enabled
    row = budget.sms_kill_switch(db)
    if row is None:
        return SmsKillSwitchOut(enabled=False, sms_enabled=sms_enabled)
    return SmsKillSwitchOut(
        enabled=row.enabled,
        sms_enabled=sms_enabled,
        reason=row.reason,
        changed_by=row.changed_by,
        changed_at=row.changed_at,
    )


@router.get("/sms/kill-switch", response_model=SmsKillSwitchOut)
def get_sms_kill_switch(_: LogsReadDep, db: DbSession) -> SmsKillSwitchOut:
    """Whether every SMS is stopped (operators; ``logs`` READ)."""
    return _kill_switch_out(db)


@router.put("/sms/kill-switch", response_model=SmsKillSwitchOut)
def set_sms_kill_switch(
    payload: SmsKillSwitchIn, _: LogsUpdateDep, claims: CurrentUser, db: DbSession
) -> SmsKillSwitchOut:
    """Stop every SMS, or let them send again (operators; ``logs`` UPDATE). Takes effect on the next message.

    The switch is read on every send, so no SMS is handed to the gateway after this commits: no deploy,
    no restart. Flipping it posts a team alert naming who and why.
    """
    user = _resolve_user(db, claims)
    budget.set_sms_kill_switch(
        db, enabled=payload.enabled, reason=payload.reason, actor=user.email
    )
    db.commit()
    return _kill_switch_out(db)


# ---------------------------------------------------------------------------------------------------
# Web push (Issue 64). Declared before ``GET /{notification_id}`` so ``web-push`` is never read as an id.
# ---------------------------------------------------------------------------------------------------


@router.get("/web-push/key", response_model=WebPushKeyOut)
def get_web_push_key(settings: SettingsDep) -> WebPushKeyOut:
    """The VAPID public key a browser subscribes with, or ``enabled: false`` while web push is off.

    Public: the key is public by design (it is in every subscription a browser makes), and a patient's
    phone asks for it before it has anything else.
    """
    if not settings.web_push_enabled:
        return WebPushKeyOut(enabled=False)
    return WebPushKeyOut(enabled=True, public_key=settings.web_push_vapid_public_key)


@router.post(
    "/web-push/subscriptions",
    response_model=PushSubscriptionOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": PushSubscriptionOut,
            "description": "This browser was already subscribed: its keys were updated.",
        }
    },
)
def subscribe_to_web_push(
    payload: PushSubscriptionIn,
    request: Request,
    response: Response,
    patient: PatientSelf,
    db: DbSession,
    settings: SettingsDep,
) -> PushSubscriptionOut:
    """Store the signed-in patient's browser subscription: ``201`` when new, ``200`` when refreshed.

    Only a patient's own session may subscribe, so a phone that was only sent a ticket link cannot sign
    itself up for someone else's messages. The endpoint must be a known push service over https (the
    server POSTs to it later); anything else is ``422``, as are keys that are not a P-256 point and a
    16-byte secret. Refused with ``409`` while web push is off.
    """
    if not settings.web_push_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Web push is not available."
        )
    expires_at = (
        datetime.fromtimestamp(payload.expiration_time / 1000, APP_TIMEZONE)
        if payload.expiration_time
        else None
    )
    try:
        row, created = webpush.subscribe(
            db,
            patient.id,
            endpoint=payload.endpoint,
            p256dh=payload.keys.p256dh,
            auth=payload.keys.auth,
            expires_at=expires_at,
            user_agent=request.headers.get("User-Agent"),
            settings=settings,
        )
    except webpush.InvalidSubscriptionError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    db.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    return PushSubscriptionOut(id=row.id, created=created)


@router.delete("/web-push/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe_from_web_push(
    payload: PushUnsubscribeIn, patient: PatientSelf, db: DbSession
) -> Response:
    """Stop notifying one of the signed-in patient's browsers. ``204`` whether or not it was subscribed."""
    webpush.unsubscribe(db, patient.id, payload.endpoint)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/delivery-stats", response_model=DeliveryStatsOut)
def get_delivery_stats(
    db: DbSession,
    _: LogsReadDep,
    settings: Annotated[Settings, Depends(get_settings)],
    hours: Annotated[
        int,
        Query(ge=1, le=168, description="How many hours back to count, up to a week."),
    ] = 24,
) -> DeliveryStatsOut:
    """Sends, failures and cost per transport over the last ``hours`` (admin; ``logs`` READ).

    The delivery-rate panel on the notifications page reads this. ``alerting`` marks a transport whose
    failure rate crosses the threshold the alert watch uses.
    """
    until = now_sast()
    since = until - timedelta(hours=hours)
    return DeliveryStatsOut(
        since=since,
        until=until,
        currency=settings.notification_cost_currency,
        alert_rate=settings.notification_failure_alert_rate,
        alert_min_attempts=settings.notification_failure_alert_min_attempts,
        channels=[
            ChannelDeliveryStatsOut(
                channel=row.channel,
                total=row.total,
                sent=row.sent,
                delivered=row.delivered,
                failed=row.dead,
                suppressed=row.suppressed,
                waiting=row.waiting,
                failure_rate=delivery_stats.failure_rate(row),
                cost=f"{row.cost:.4f}",
                alerting=delivery_stats.is_alerting(row, settings),
            )
            for row in delivery_stats.channel_stats(db, since=since, until=until)
        ],
    )


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
    ``provider_message_id``. A match advances the row to ``delivered``, or ends it ``dead`` for a bounce.
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
