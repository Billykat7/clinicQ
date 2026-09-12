"""Notification service: enqueue, deliver, retry with backoff, dead-letter, delivery status.

The one place transactional email and SMS are delivered from (Issue #67). Every message becomes a
``notification`` row whose ``status`` is the durable record of what happened to it:

* :func:`deliver_email` is the single email send path — ``src.core.email_send.send`` routes through
  it. It records a ``queued`` row, hands the message to SMTP, and flips the row to ``sent`` or,
  on failure, ``failed`` (re-raising :class:`EmailDeliveryError` so existing callers behave exactly
  as before). Recording is best-effort: a ledger write must never take mail delivery down.
* :func:`send_sms` sends through the pluggable SMS provider, recording the row the same way.
* :func:`run_retry_sweep` (driven by ``src.core.scheduler``) re-attempts rows that are due with
  exponential backoff and dead-letters them once the attempt budget is spent — so a failed send is
  retried and eventually parked in ``dead``, never lost silently.
* :func:`record_delivery_status` advances a row to ``delivered`` (or records a bounce) from a
  provider webhook; :func:`get_status` reads one row for the admin status query.

Backoff: the wait before the *n*-th attempt is ``notification_retry_base_seconds * 2**(n-1)``.
All timestamps are Africa/Johannesburg (the business timezone).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.commons.enums import (
    NOTIFICATION_SECRET_FIELDS,
    NotificationChannel,
    NotificationStatus,
    NotificationTemplate,
    notification_category_for,
)
from src.core.config import get_settings
from src.core.s3_logging import APP_TIMEZONE
from src.database.models.notification import Notification
from src.database.session import get_db_context
from src.modules.notifications import preferences, templates
from src.modules.notifications.preferences import DeliveryDecision, DeliveryOutcome
from src.modules.notifications.schemas import DeliveryReceipt, RenderedMessage
from src.modules.notifications.sms import SmsProvider, build_sms_provider

logger = logging.getLogger(__name__)

# Provider label recorded on the row for the SMTP email transport. Terminal states (``sent``,
# ``delivered``, ``dead``, ``suppressed``) are simply those the retry-sweep query below excludes.
_EMAIL_PROVIDER = "smtp"


#: What the ledger stores instead of a secret (Issue 17).
WITHHELD = "[withheld: one-time code]"


def _now() -> datetime:
    """Return the current time in the project business timezone (Africa/Johannesburg)."""
    return datetime.now(APP_TIMEZONE)


def is_secret_bearing(template: NotificationTemplate) -> bool:
    """Whether ``template``'s message carries a secret the ledger must not keep (Issue 17)."""
    return template in NOTIFICATION_SECRET_FIELDS


def ledger_payload(template: NotificationTemplate, context: dict) -> dict:
    """The payload to store on the row: ``context`` with every secret field withheld."""
    secret_fields = NOTIFICATION_SECRET_FIELDS.get(template, frozenset())
    return {
        key: (WITHHELD if key in secret_fields else value)
        for key, value in context.items()
    }


def _backoff_seconds(attempts: int) -> int:
    """Seconds to wait before the ``attempts``-th retry — exponential from the configured base."""
    base = get_settings().notification_retry_base_seconds
    # attempts is the number already made; the next wait doubles per prior attempt.
    return base * (2 ** max(attempts - 1, 0))


# --------------------------------------------------------------------------------------
# Transport — email via the existing SMTP sender, SMS via the pluggable provider.
# --------------------------------------------------------------------------------------


def _deliver_email_transport(
    *,
    to: str,
    message: RenderedMessage,
    from_email: str | None = None,
    list_unsubscribe_url: str | None = None,
) -> str:
    """Hand one email to the SMTP transport and return its Message-ID.

    ``list_unsubscribe_url`` adds RFC 2369 / RFC 8058 unsubscribe headers for non-essential mail
    (one-click unsubscribe honoured without a login). Imported lazily so ``src.core.email_send``
    (whose public ``send`` routes back through this service) and this module do not import each
    other at load time.
    """
    from src.core import email_send

    return email_send.deliver_smtp(
        to=to,
        subject=message.subject or "",
        text=message.text,
        html=message.html,
        from_email=from_email,
        list_unsubscribe_url=list_unsubscribe_url,
    )


def _deliver_sms_transport(
    *, to: str, message: RenderedMessage, provider: SmsProvider
) -> str:
    """Hand one SMS to the provider and return its provider-side message id."""
    return provider.send(to=to, text=message.text, sender=get_settings().sms_from)


# --------------------------------------------------------------------------------------
# Row lifecycle mutators (operate on a passed-in Session; caller controls the transaction).
# --------------------------------------------------------------------------------------


def enqueue(
    db: Session,
    *,
    channel: NotificationChannel,
    template: NotificationTemplate,
    recipient: str,
    subject: str | None,
    payload: dict,
    max_attempts: int | None = None,
    next_attempt_at: datetime | None = None,
) -> Notification:
    """Create and add a ``queued`` notification row (not yet flushed/committed).

    ``next_attempt_at`` schedules the row for a later sweep (used to *defer* a message that lands in
    the recipient's quiet hours); left unset the row is due immediately.
    """
    notification = Notification(
        channel=channel.value,
        template_key=template.value,
        recipient=recipient,
        subject=subject,
        status=NotificationStatus.QUEUED.value,
        payload=payload,
        attempts=0,
        max_attempts=max_attempts or get_settings().notification_max_attempts,
        next_attempt_at=next_attempt_at,
    )
    db.add(notification)
    return notification


def _apply_success(
    notification: Notification,
    *,
    provider: str,
    provider_message_id: str,
    now: datetime,
) -> None:
    """Mark a row ``sent``: record the provider, its message id, and stop further retries."""
    notification.attempts += 1
    notification.status = NotificationStatus.SENT.value
    notification.provider = provider
    notification.provider_message_id = provider_message_id
    notification.sent_at = now
    notification.next_attempt_at = None
    notification.last_error = None


def _apply_failure(notification: Notification, *, error: str, now: datetime) -> None:
    """Record a failed attempt: dead-letter when the budget is spent, else schedule a backoff retry."""
    notification.attempts += 1
    notification.failed_at = now
    notification.last_error = error[:2000]
    if notification.attempts >= notification.max_attempts:
        notification.status = NotificationStatus.DEAD.value
        notification.next_attempt_at = None
        logger.error(
            "Notification %s dead-lettered after %d attempts (%s/%s): %s",
            notification.id,
            notification.attempts,
            notification.channel,
            notification.template_key,
            error,
        )
    else:
        notification.status = NotificationStatus.FAILED.value
        notification.next_attempt_at = now + timedelta(
            seconds=_backoff_seconds(notification.attempts)
        )


def attempt(
    db: Session,
    notification: Notification,
    *,
    provider: SmsProvider,
    now: datetime | None = None,
    context: dict | None = None,
) -> bool:
    """Attempt one delivery of ``notification`` and update its row. Returns True on success.

    Re-renders the message from the row's stored payload (so a queued row is deliverable without
    the originating domain objects), hands it to the right transport, and applies success or a
    failure/backoff/dead-letter transition. Never raises for a transport failure — the outcome is
    recorded on the row.

    ``context`` renders from values the row does not hold: a secret-bearing message (a one-time
    code) is stored with a placeholder and delivered, once, from the real value in memory.
    """
    now = now or _now()
    channel = NotificationChannel(notification.channel)
    template = NotificationTemplate(notification.template_key)
    message = templates.render(channel, template, context or notification.payload)
    try:
        if channel is NotificationChannel.EMAIL:
            message_id = _deliver_email_transport(
                to=notification.recipient,
                message=message,
                list_unsubscribe_url=preferences.unsubscribe_url(
                    notification.recipient, notification_category_for(template)
                ),
            )
            provider_label = _EMAIL_PROVIDER
        else:
            message_id = _deliver_sms_transport(
                to=notification.recipient, message=message, provider=provider
            )
            provider_label = provider.kind.value
    except Exception as exc:  # every transport error is recorded on the row, not raised
        # EmailDeliveryError and SmsSendError both land here; the row captures the reason and the
        # backoff/dead-letter decision so the sweep can act on it.
        _apply_failure(notification, error=str(exc), now=now)
        db.flush()
        return False
    _apply_success(
        notification, provider=provider_label, provider_message_id=message_id, now=now
    )
    db.flush()
    return True


# --------------------------------------------------------------------------------------
# Public send paths.
# --------------------------------------------------------------------------------------


def deliver_email(
    *,
    to: str,
    subject: str,
    text: str,
    html: str | None = None,
    from_email: str | None = None,
    template: NotificationTemplate = NotificationTemplate.GENERIC,
) -> None:
    """Record and deliver one transactional email through the service.

    The single email send path: ``src.core.email_send.send`` calls this once SMTP is configured.
    Persists a ledger row (best-effort), hands the message to SMTP, and marks the row ``sent`` or
    ``failed``. Re-raises :class:`EmailDeliveryError` on failure so existing callers keep their
    exact behaviour (their own logging/retry is unchanged); the failed row is left for the retry
    sweep to pick up as well.
    """
    from src.core.email_send import EmailDeliveryError

    settings = get_settings()
    secret = is_secret_bearing(template)
    # A secret-bearing email (a sign-in code) is recorded without its body: the row says a code
    # was sent, never which one.
    payload = templates.prerendered_payload(
        subject=subject,
        text=WITHHELD if secret else text,
        html=None if secret else html,
    )
    message = RenderedMessage(text=text, subject=subject, html=html)

    # Honour the recipient's notification preferences (Issue #72): a non-essential category the
    # recipient has opted off is suppressed, and a non-urgent message that lands in their quiet
    # hours is deferred to the end of the window rather than dropped. Fail-open — a preferences
    # lookup error must never block a transactional (possibly essential) email.
    decision = _safe_resolve(recipient=to, template=template)

    if decision.outcome is DeliveryOutcome.SUPPRESS:
        _safe_record_terminal(
            template=template,
            recipient=to,
            subject=subject,
            payload=payload,
            status=NotificationStatus.SUPPRESSED,
            error=f"suppressed by preference ({decision.reason})",
        )
        return

    next_attempt_at = (
        decision.defer_until if decision.outcome is DeliveryOutcome.DEFER else None
    )
    notification_id = _safe_record_queued(
        channel=NotificationChannel.EMAIL,
        template=template,
        recipient=to,
        subject=subject,
        payload=payload,
        max_attempts=1 if secret else settings.notification_max_attempts,
        next_attempt_at=next_attempt_at,
    )

    if decision.outcome is DeliveryOutcome.DEFER:
        # Left queued with a future next_attempt_at; the retry sweep delivers it when it comes due.
        return

    try:
        message_id = _deliver_email_transport(
            to=to,
            message=message,
            from_email=from_email,
            list_unsubscribe_url=preferences.unsubscribe_url(to, decision.category),
        )
    except EmailDeliveryError as exc:
        _safe_finalise(notification_id, success=False, error=str(exc))
        raise
    _safe_finalise(
        notification_id,
        success=True,
        provider=_EMAIL_PROVIDER,
        provider_message_id=message_id,
    )


def send_sms(
    db: Session,
    *,
    to: str,
    template: NotificationTemplate,
    context: dict,
    provider: SmsProvider | None = None,
    now: datetime | None = None,
) -> Notification:
    """Record and deliver one SMS through the pluggable provider, returning its row.

    Unlike the email path this does not re-raise on failure: the row is left ``failed`` (or
    ``dead`` when the attempt budget is one) for the retry sweep, so a transient gateway error is
    retried rather than surfaced to the caller. The caller owns the transaction (``db``).
    """
    provider = provider or build_sms_provider()
    now = now or _now()
    # Honour preferences (Issue #72): defer a non-urgent SMS that lands in the recipient's quiet
    # hours, and suppress a non-essential category the recipient has opted off. Essential mail
    # (sign-in codes, financial notices) is always delivered.
    decision = preferences.resolve(
        db,
        recipient_email=to,
        template=template,
        channel=NotificationChannel.SMS,
        now=now,
    )
    if decision.outcome is DeliveryOutcome.SUPPRESS:
        notification = enqueue(
            db,
            channel=NotificationChannel.SMS,
            template=template,
            recipient=to,
            subject=None,
            payload=dict(context),
        )
        notification.status = NotificationStatus.SUPPRESSED.value
        notification.last_error = f"suppressed by preference ({decision.reason})"
        db.flush()
        return notification

    next_attempt_at = (
        decision.defer_until if decision.outcome is DeliveryOutcome.DEFER else None
    )
    secret = is_secret_bearing(template)
    notification = enqueue(
        db,
        channel=NotificationChannel.SMS,
        template=template,
        recipient=to,
        subject=None,
        payload=ledger_payload(template, context),
        next_attempt_at=next_attempt_at,
        # A code is sent once or not at all: a retry would render the placeholder, and a late
        # code is no use to anyone.
        max_attempts=1 if secret else None,
    )
    db.flush()
    if decision.outcome is DeliveryOutcome.DEFER:
        # Queued for the retry sweep to deliver once quiet hours end.
        return notification
    attempt(
        db,
        notification,
        provider=provider,
        now=now,
        context=dict(context) if secret else None,
    )
    return notification


# --------------------------------------------------------------------------------------
# Retry sweep, webhook, and status query.
# --------------------------------------------------------------------------------------


def run_retry_sweep(
    db: Session,
    *,
    provider: SmsProvider | None = None,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """Re-attempt every due notification and return how many were delivered this run.

    A row is *due* when it is not terminal (``queued``/``failed``), its budget is not spent, and
    its ``next_attempt_at`` is in the past (or unset). Each is retried once here; a still-failing
    row is rescheduled with a longer backoff or dead-lettered. Safe to run repeatedly and on more
    than one instance (the caller elects a single runner via an advisory lock).
    """
    now = now or _now()
    provider = provider or build_sms_provider()
    due = (
        db.execute(
            select(Notification)
            .where(
                Notification.status.in_(
                    [NotificationStatus.QUEUED.value, NotificationStatus.FAILED.value]
                ),
                Notification.attempts < Notification.max_attempts,
                or_(
                    Notification.next_attempt_at.is_(None),
                    Notification.next_attempt_at <= now,
                ),
            )
            .order_by(Notification.next_attempt_at.asc().nulls_first())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    delivered = 0
    for notification in due:
        if attempt(db, notification, provider=provider, now=now):
            delivered += 1
    return delivered


def record_delivery_status(db: Session, receipt: DeliveryReceipt) -> bool:
    """Apply a provider delivery-status callback to its notification row. Returns True if matched.

    A confirmed delivery advances the row to ``delivered`` (terminal, better than ``sent``); a
    reported bounce/failure records the reason. An unknown ``provider_message_id`` returns False so
    the endpoint can answer 404.
    """
    notification = db.execute(
        select(Notification).where(
            Notification.provider_message_id == receipt.provider_message_id
        )
    ).scalar_one_or_none()
    if notification is None:
        return False
    now = _now()
    if receipt.delivered:
        notification.status = NotificationStatus.DELIVERED.value
        notification.delivered_at = now
        notification.last_error = None
    else:
        notification.status = NotificationStatus.FAILED.value
        notification.failed_at = now
        if receipt.detail:
            notification.last_error = receipt.detail[:2000]
    db.flush()
    return True


def get_status(db: Session, notification_id: str) -> Notification | None:
    """Return one notification row by id, or ``None`` when it does not exist."""
    return db.get(Notification, notification_id)


def list_notifications(
    db: Session,
    *,
    status: NotificationStatus | None = None,
    channel: NotificationChannel | None = None,
    recipient: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[Notification], int]:
    """List notification rows (newest first) for the delivery viewer, with the total before paging.

    Operational read behind the ``logs`` verb (see the router). Optional filters narrow by delivery
    ``status``, ``channel`` and a case-insensitive ``recipient`` substring; ``total`` is the count
    of matching rows before ``offset``/``limit`` are applied so the caller can page.
    """
    filters = []
    if status is not None:
        filters.append(Notification.status == status.value)
    if channel is not None:
        filters.append(Notification.channel == channel.value)
    if recipient and recipient.strip():
        filters.append(Notification.recipient.ilike(f"%{recipient.strip()}%"))

    total = int(
        db.execute(
            select(func.count()).select_from(Notification).where(*filters)
        ).scalar_one()
    )
    rows = list(
        db.execute(
            select(Notification)
            .where(*filters)
            .order_by(Notification.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return rows, total


# --------------------------------------------------------------------------------------
# Best-effort persistence for the no-session email path (its own short transactions).
# --------------------------------------------------------------------------------------


def _safe_resolve(
    *, recipient: str, template: NotificationTemplate
) -> DeliveryDecision:
    """Resolve email preferences in a short read transaction; fail *open* to a plain SEND.

    A preferences lookup must never take mail delivery down or silently drop an essential message,
    so any error resolves to sending now on email — the safe default.
    """
    try:
        with get_db_context() as db:
            return preferences.resolve(
                db,
                recipient_email=recipient,
                template=template,
                channel=NotificationChannel.EMAIL,
            )
    except Exception:
        logger.exception(
            "Notification preferences: resolve failed for %s to %s — sending anyway",
            template.value,
            recipient,
        )
        return DeliveryDecision(
            outcome=DeliveryOutcome.SEND,
            category=notification_category_for(template),
            essential=True,
            channel=NotificationChannel.EMAIL,
            reason="resolve-error-failopen",
        )


def _safe_record_queued(
    *,
    channel: NotificationChannel,
    template: NotificationTemplate,
    recipient: str,
    subject: str | None,
    payload: dict,
    max_attempts: int,
    next_attempt_at: datetime | None = None,
) -> str | None:
    """Record a ``queued`` row in its own transaction; return its id, or ``None`` on any failure.

    Best-effort by design: the notification ledger must never prevent an email from being sent, so
    a persistence error is logged and swallowed and delivery proceeds without a row.
    ``next_attempt_at`` defers the row to a later sweep (quiet-hours deferral).
    """
    try:
        with get_db_context() as db:
            notification = enqueue(
                db,
                channel=channel,
                template=template,
                recipient=recipient,
                subject=subject,
                payload=payload,
                max_attempts=max_attempts,
                next_attempt_at=next_attempt_at,
            )
            db.flush()
            return notification.id
    except Exception:
        logger.exception(
            "Notification ledger: failed to record %s to %s", channel.value, recipient
        )
        return None


def _safe_record_terminal(
    *,
    template: NotificationTemplate,
    recipient: str,
    subject: str | None,
    payload: dict,
    status: NotificationStatus,
    error: str,
) -> None:
    """Record a message that was *not* sent (e.g. suppressed by preference) in its own transaction.

    Best-effort: an operator can see the message was intentionally not delivered (and why) without
    the ledger ever being able to break the caller.
    """
    try:
        with get_db_context() as db:
            notification = enqueue(
                db,
                channel=NotificationChannel.EMAIL,
                template=template,
                recipient=recipient,
                subject=subject,
                payload=payload,
            )
            notification.status = status.value
            notification.last_error = error[:2000]
            db.flush()
    except Exception:
        logger.exception(
            "Notification ledger: failed to record suppressed %s to %s",
            template.value,
            recipient,
        )


def _safe_finalise(
    notification_id: str | None,
    *,
    success: bool,
    provider: str | None = None,
    provider_message_id: str | None = None,
    error: str | None = None,
) -> None:
    """Update a row to its post-delivery state in its own transaction; swallow ledger errors."""
    if notification_id is None:
        return
    try:
        with get_db_context() as db:
            notification = db.get(Notification, notification_id)
            if notification is None:
                return
            now = _now()
            if success:
                _apply_success(
                    notification,
                    provider=provider or _EMAIL_PROVIDER,
                    provider_message_id=provider_message_id or "",
                    now=now,
                )
            else:
                _apply_failure(notification, error=error or "unknown error", now=now)
    except Exception:
        logger.exception("Notification ledger: failed to finalise %s", notification_id)
