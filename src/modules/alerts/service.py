"""Service layer for staff-authored alerts (Issue #132 follow-up).

Reuses ``messaging``'s audience resolution directly (:func:`~src.modules.messaging.audience.
resolve_recipients` is generic, not messaging-specific) rather than duplicating it — a staff alert
is authorised and targeted exactly like an announcement.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.time import now_sast
from src.database.models import Alert, AlertRecipient, User
from src.modules.messaging import audience as audience_service
from src.modules.messaging.enums import (
    AlertKind,
    AlertSeverity,
    AlertSource,
    AudienceType,
)

ROLE_SENDER = "sender"
ROLE_RECIPIENT = "recipient"

_FOLDERS = ("inbox", "sent", "deleted")


def _now() -> datetime:
    """Business time for read receipts: Africa/Johannesburg (Issue 4)."""
    return now_sast()


def create_alert(
    db: Session,
    *,
    sender: User,
    audience_type: AudienceType,
    audience_ref: str | None,
    subject: str,
    body: str,
    severity: AlertSeverity,
) -> Alert:
    """Resolve+authorise an audience, then create a sent ``staff``/``custom`` alert.

    Mirrors :func:`~src.modules.messaging.service.create_announcement_thread`: the audience is
    resolved and authorised *before* any row is created, so an unauthorised or empty audience
    never leaves a stray alert behind.

    Raises:
        AudienceNotAuthorisedError: the sender may not target this audience (mapped to 403).
        EmptyAudienceError: the audience resolves to no reachable recipient (mapped to 409).
    """
    recipient_ids = audience_service.resolve_recipients(
        db,
        sender=sender,
        audience_type=audience_type,
        audience_ref=audience_ref,
    )

    alert = Alert(
        source=AlertSource.STAFF.value,
        severity=severity.value,
        kind=AlertKind.CUSTOM.value,
        author_id=sender.id,
        subject=subject,
        body=body,
        audience_type=audience_type.value,
        audience_ref=audience_ref,
    )
    db.add(alert)
    db.flush()

    db.add(AlertRecipient(alert_id=alert.id, user_id=sender.id, role=ROLE_SENDER))
    for recipient_id in recipient_ids:
        db.add(
            AlertRecipient(alert_id=alert.id, user_id=recipient_id, role=ROLE_RECIPIENT)
        )
    db.flush()
    return alert


def get_alert(db: Session, alert_id: str) -> Alert | None:
    """Return one alert by id, or ``None`` when it does not exist."""
    return db.get(Alert, alert_id)


def get_recipient_row(
    db: Session, alert_id: str, user_id: str
) -> AlertRecipient | None:
    """Return the caller's own recipient/sender row for an alert, or ``None`` if they're neither."""
    return db.execute(
        select(AlertRecipient).where(
            AlertRecipient.alert_id == alert_id, AlertRecipient.user_id == user_id
        )
    ).scalar_one_or_none()


def list_alerts(
    db: Session,
    *,
    user_id: str,
    folder: str,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[Alert], int]:
    """Return a page of the caller's alerts for one console tab, newest first, and the total.

    ``inbox`` — alerts where the caller is a recipient, not deleted. ``sent`` — alerts the caller
    authored, not deleted. ``deleted`` — alerts the caller authored that they have since deleted
    (deletion is author-scoped and alert-wide, mirroring how a manager's thread soft-delete works
    in messaging — there is no separate "hide from my inbox only" concept here).
    """
    if folder not in _FOLDERS:
        raise ValueError(f"folder must be one of {_FOLDERS}, got {folder!r}")
    role = ROLE_SENDER if folder in ("sent", "deleted") else ROLE_RECIPIENT
    stmt = (
        select(Alert)
        .join(AlertRecipient, AlertRecipient.alert_id == Alert.id)
        .where(
            AlertRecipient.user_id == user_id,
            AlertRecipient.role == role,
            Alert.is_deleted.is_(folder == "deleted"),
        )
        .order_by(Alert.created_at.desc())
    )
    alerts = list(db.execute(stmt).scalars().all())
    total = len(alerts)
    return alerts[offset : offset + limit], total


def mark_alert_read(db: Session, alert_id: str, user_id: str) -> AlertRecipient | None:
    """Mark an alert read for the caller's own recipient row; a no-op if already read or absent."""
    row = get_recipient_row(db, alert_id, user_id)
    if row is not None and row.read_at is None:
        row.read_at = _now()
    return row


def mark_alert_unread(
    db: Session, alert_id: str, user_id: str
) -> AlertRecipient | None:
    """Undo :func:`mark_alert_read` for the caller's own recipient row."""
    row = get_recipient_row(db, alert_id, user_id)
    if row is not None:
        row.read_at = None
    return row


def soft_delete_alert(db: Session, alert: Alert) -> None:
    """Move an alert to the console's Deleted tab. Reversible — see :func:`restore_alert`."""
    alert.is_deleted = True


def restore_alert(db: Session, alert: Alert) -> None:
    """Undo :func:`soft_delete_alert`, returning an alert to its sent listing."""
    alert.is_deleted = False
