"""Notification centre: the per-user in-app feed behind the shell bell (Issue #113).

Where :mod:`src.modules.notifications.service` owns *delivery* (a ledger of email/SMS sends), this
module owns the **in-app notification centre** — the per-user feed the shell bell counts and
previews. It has three jobs:

* **Record** a domain event as an in-app notification (:func:`record` / :func:`record_for_email`),
  honouring the recipient's per-category preference: an event in a category the user has muted is
  never stored, so it never badges. Only the domain-event categories belong here
  (:data:`~src.commons.enums.NOTIFICATION_CENTER_CATEGORIES`) — auth codes and promotional mail are
  not centre items, and messages are surfaced through the messaging machinery, not duplicated here.
* **Count** what is unread. :func:`unread_total` is the bell badge: unread in-app notifications
  **plus** unread messages (Issue #112's :func:`messaging.total_unread_for_user`), so the one number
  the shell polls covers both.
* **List & mark.** :func:`recent_items` merges recent in-app notifications with recent message-thread
  previews into one time-ordered feed for the dropdown; :func:`mark_read` / :func:`mark_unread` /
  :func:`mark_all_read` move an item's read-state (a message item is read by opening its thread).

All timestamps are Africa/Johannesburg (the business timezone).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.commons.enums import (
    NotificationCategory,
    PermissionVerb,
    is_center_category,
)
from src.commons.exceptions import InAppNotificationNotFoundError
from src.core.rbac import get_effective_verb_for_role, granted_covers_required
from src.core.s3_logging import APP_TIMEZONE
from src.database.models import InAppNotification, User
from src.database.session import get_db_context
from src.modules.messaging import service as messaging_service
from src.modules.messaging.enums import MessageAnchorType
from src.modules.notifications import preferences

logger = logging.getLogger(__name__)

# How much of a body to show in a dropdown preview before it is clipped by the UI. Kept generous —
# the template also clamps visually — so the API returns a useful snippet without the whole body.
_SNIPPET_LEN = 200


def _now() -> datetime:
    """Return the current time in the project business timezone (Africa/Johannesburg)."""
    return datetime.now(APP_TIMEZONE)


class CenterItemSource(StrEnum):
    """Which store a merged centre item came from — an in-app notification, or a message thread."""

    NOTIFICATION = "notification"
    MESSAGE = "message"


@dataclass(frozen=True, slots=True)
class CenterItem:
    """One preview in the merged notification-centre feed (a notification or a message)."""

    id: str
    source: CenterItemSource
    category: str
    title: str
    snippet: str
    created_at: datetime | None
    read: bool
    link: str | None


# --------------------------------------------------------------------------------------
# Recording — preference-gated, so a muted category never badges.
# --------------------------------------------------------------------------------------


def record(
    db: Session,
    *,
    user_id: str,
    category: NotificationCategory,
    title: str,
    body: str,
    link: str | None = None,
    now: datetime | None = None,
) -> InAppNotification | None:
    """Record one in-app notification for ``user_id``, unless its category is off for them.

    Returns the created row, or ``None`` when the event does not belong in the centre — either the
    category is not a centre category (auth/marketing/messages), or the user has muted it. Because a
    muted category is never stored, it never contributes to the unread badge. The row starts unread
    (``read_at`` is ``NULL``).
    """
    if not is_center_category(category):
        return None
    # Honour the same per-category preference as delivery: a category the user has switched off is
    # not recorded here either. Essential categories can never resolve to off, so they always land.
    user = db.get(User, user_id)
    if user is None:
        return None
    if (
        preferences.chosen_channel(
            preferences.load_preference(db, user.email), category
        )
        is preferences.NotificationChannelPreference.OFF
    ):
        return None

    notification = InAppNotification(
        user_id=user_id,
        category=category.value,
        title=title[:200],
        body=body,
        link=link,
        read_at=None,
    )
    db.add(notification)
    db.flush()
    return notification


def record_for_email(
    db: Session,
    *,
    email: str,
    category: NotificationCategory,
    title: str,
    body: str,
    link: str | None = None,
) -> InAppNotification | None:
    """Resolve the account for ``email`` and record a centre notification for it (best-effort).

    The seam the transactional-email funnel calls: a domain event that emails a user also lands in
    their in-app centre, keyed by the same address. An address with no account records nothing (there
    is no feed to add to). Preference gating and category eligibility are handled by :func:`record`.
    """
    normalized = (email or "").strip().lower()
    if not normalized:
        return None
    user = db.execute(
        select(User).where(User.email == normalized, User.is_deleted.is_(False))
    ).scalar_one_or_none()
    if user is None:
        return None
    return record(
        db,
        user_id=user.id,
        category=category,
        title=title,
        body=body,
        link=link,
    )


def record_from_email_safely(
    *,
    email: str,
    category: NotificationCategory,
    title: str,
    body: str,
) -> None:
    """Record a centre notification for an emailed event in its own short transaction; never raise.

    Called from the single transactional-email funnel (``email_send.send``) on its SMTP-configured
    delivery path, so every domain notification that goes out by email also surfaces in the bell —
    keyed by the same address, honouring the same per-category preference. Strictly best-effort: any
    failure is logged and swallowed so the centre can never take an email path down, exactly like the
    delivery ledger's own best-effort writes it sits alongside.
    """
    if not is_center_category(category):
        return
    try:
        with get_db_context() as db:
            record_for_email(db, email=email, category=category, title=title, body=body)
    except Exception:  # the centre must never break a send
        logger.exception(
            "Notification centre: failed to record %s for %s", category.value, email
        )


# --------------------------------------------------------------------------------------
# Counts.
# --------------------------------------------------------------------------------------


def unread_notification_count(db: Session, user_id: str) -> int:
    """Return how many in-app notifications ``user_id`` has not yet read."""
    return int(
        db.execute(
            select(func.count())
            .select_from(InAppNotification)
            .where(
                InAppNotification.user_id == user_id,
                InAppNotification.read_at.is_(None),
            )
        ).scalar_one()
        or 0
    )


def unread_total(db: Session, user_id: str) -> int:
    """Return the bell badge total: unread in-app notifications **plus** unread messages.

    The single number the shell polls (Issue #113). Unread messages come from the messaging module's
    live per-thread unread (Issue #112), so a broadcast or a reply the user has not read counts here
    just like a domain-event notification does.
    """
    unread_messages = messaging_service.total_unread_for_user(db, user_id)
    return unread_notification_count(db, user_id) + unread_messages


# --------------------------------------------------------------------------------------
# Listing — a merged, time-ordered feed of notifications and message previews.
# --------------------------------------------------------------------------------------


#: The dropdown title for a thread with no subject of its own, by what it is anchored to. Add a
#: label whenever you add a :class:`~src.modules.messaging.enums.MessageAnchorType` member.
_ANCHOR_LABELS: dict[MessageAnchorType, str] = {
    MessageAnchorType.ANNOUNCEMENT: "Announcement",
}


def list_notifications(
    db: Session, user_id: str, *, limit: int = 20
) -> list[InAppNotification]:
    """Return ``user_id``'s most recent in-app notifications, newest first — the bell dropdown."""
    return list(
        db.execute(
            select(InAppNotification)
            .where(InAppNotification.user_id == user_id)
            .order_by(InAppNotification.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def _notification_item(notification: InAppNotification) -> CenterItem:
    """Project one in-app notification into a merged-feed item."""
    return CenterItem(
        id=notification.id,
        source=CenterItemSource.NOTIFICATION,
        category=notification.category,
        title=notification.title,
        snippet=notification.body[:_SNIPPET_LEN],
        created_at=notification.created_at,
        read=notification.read_at is not None,
        link=notification.link,
    )


def _can_reach_admin_messages(db: Session, user_id: str) -> bool:
    """Return whether the caller holds the ``communications.messages`` READ grant that unlocks the
    staff Messages console — the only place a thread can be opened from. A caller without it would
    only reach a 403, so their message previews are dropped from the bell rather than linking
    somewhere they're refused.
    """
    user = db.get(User, user_id)
    if user is None:
        return False
    role = (user.role or "").strip()
    if not role:
        return False
    return granted_covers_required(
        get_effective_verb_for_role(db, role, "communications.messages"),
        PermissionVerb.READ,
    )


def _message_previews(db: Session, user_id: str, *, limit: int) -> list[CenterItem]:
    """Build message-thread previews for ``user_id`` (newest activity first), capped at ``limit``.

    Empty for a caller who cannot reach ``/admin/messages`` (see :func:`_can_reach_admin_messages`)
    — the staff console is the only messaging surface in the app now, so a portal-role caller's own
    threads are omitted rather than previewed with a link they cannot open. A thread is *read* for
    the preview when the caller has no unread messages in it; the snippet is the most recent
    message's body.
    """
    if not _can_reach_admin_messages(db, user_id):
        return []
    items: list[CenterItem] = []
    for thread in messaging_service.list_threads_for_user(db, user_id)[:limit]:
        messages = messaging_service.list_messages(db, thread.id)
        last = messages[-1] if messages else None
        anchor = MessageAnchorType(thread.anchor_type)
        items.append(
            CenterItem(
                id=thread.id,
                source=CenterItemSource.MESSAGE,
                category=NotificationCategory.MESSAGES.value,
                title=thread.subject or _ANCHOR_LABELS.get(anchor, "Conversation"),
                snippet=(last.body[:_SNIPPET_LEN] if last is not None else ""),
                created_at=(last.created_at if last is not None else thread.created_at),
                read=messaging_service.unread_count(db, thread.id, user_id) == 0,
                link="/admin/messages/inbox",
            )
        )
    return items


def recent_items(db: Session, user_id: str, *, limit: int = 20) -> list[CenterItem]:
    """Return the merged notification-centre feed for the dropdown, newest first, capped at ``limit``.

    Merges recent in-app notifications with recent message-thread previews into one time-ordered
    list. Items with no timestamp sort last. The cap is applied after the merge so the newest across
    both sources wins.
    """
    merged = [
        _notification_item(n) for n in list_notifications(db, user_id, limit=limit)
    ]
    merged.extend(_message_previews(db, user_id, limit=limit))
    merged.sort(
        key=lambda item: item.created_at or datetime.min.replace(tzinfo=APP_TIMEZONE),
        reverse=True,
    )
    return merged[:limit]


# --------------------------------------------------------------------------------------
# Read-state mutations (author-scoped to the signed-in user).
# --------------------------------------------------------------------------------------


def _get_owned(db: Session, user_id: str, item_id: str) -> InAppNotification:
    """Load one of ``user_id``'s notifications, or raise if it is missing or not theirs."""
    notification = db.get(InAppNotification, item_id)
    if notification is None or notification.user_id != user_id:
        raise InAppNotificationNotFoundError(item_id)
    return notification


def mark_read(
    db: Session, user_id: str, item_id: str, *, now: datetime | None = None
) -> InAppNotification:
    """Mark one of ``user_id``'s notifications read (idempotent). Raises if it is not theirs."""
    notification = _get_owned(db, user_id, item_id)
    if notification.read_at is None:
        notification.read_at = now or _now()
        db.flush()
    return notification


def mark_unread(db: Session, user_id: str, item_id: str) -> InAppNotification:
    """Mark one of ``user_id``'s notifications unread (idempotent). Raises if it is not theirs."""
    notification = _get_owned(db, user_id, item_id)
    if notification.read_at is not None:
        notification.read_at = None
        db.flush()
    return notification


def mark_all_read(db: Session, user_id: str, *, now: datetime | None = None) -> int:
    """Mark every unread in-app notification for ``user_id`` read; return how many changed."""
    stamp = now or _now()
    unread = (
        db.execute(
            select(InAppNotification).where(
                InAppNotification.user_id == user_id,
                InAppNotification.read_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for notification in unread:
        notification.read_at = stamp
    db.flush()
    return len(unread)
