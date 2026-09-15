"""Notification preferences: honour a user's channel, quiet-hours and unsubscribe choices (Issue #72).

Once SMS and in-app messaging exist the product can become noisy enough that people mute it
entirely — including the messages that matter. This module is the one place a send is checked
against the recipient's preferences, so every notification path honours them by routing through the
notification service (:mod:`src.modules.notifications.service`).

The heart is :func:`resolve`, which turns *(recipient, template, origin channel)* into a
:class:`DeliveryDecision`:

* **SEND** — deliver now on the resolved channel.
* **DEFER** — the message is non-urgent and lands inside the recipient's quiet hours; deliver it at
  the end of the quiet window instead of dropping it (the notification row is queued with
  ``next_attempt_at`` set, and the retry sweep delivers it when it comes due).
* **SUPPRESS** — the recipient has opted this non-essential category off (or onto a different
  channel than this send is on), so nothing is delivered.

Two rules are load-bearing and enforced here so no caller can get them wrong:

* **Essential categories are never dropped.** ``ACCOUNT`` (security/auth) and ``FINANCIAL`` mail is
  always delivered on at least one channel — an essential category set to ``off`` is coerced back to
  email, and its origin channel is always allowed.
* **Enumeration-safe unsubscribe.** The login-free unsubscribe link carries a signed token, so the
  endpoint acts on it without a session and without a lookup that would reveal whether an account
  exists (:func:`apply_unsubscribe`).

Quiet hours are evaluated as wall-clock times in the user's own timezone and may wrap past midnight.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import (
    NotificationCategory,
    NotificationChannel,
    NotificationChannelPreference,
    NotificationTemplate,
    SecurityAuditEvent,
    SecurityAuditOutcome,
    is_essential_category,
    is_urgent_template,
    notification_category_for,
)
from src.core.config import get_settings
from src.core.s3_logging import APP_TIMEZONE
from src.core.security import create_unsubscribe_token, decode_unsubscribe_token
from src.database.models.notification_preference import NotificationPreference
from src.database.models.user import User
from src.modules.notifications.schemas import (
    CategoryPreference,
    NotificationPreferencesRead,
    NotificationPreferencesUpdate,
)

logger = logging.getLogger(__name__)

# Default channel for any category a user has not explicitly changed: email is universally
# reachable, so a brand-new account with no preferences receives everything by email.
DEFAULT_CHANNEL = NotificationChannelPreference.EMAIL


class DeliveryOutcome(StrEnum):
    """What :func:`resolve` decided should happen to one send."""

    SEND = "send"
    DEFER = "defer"
    SUPPRESS = "suppress"


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    """The outcome of resolving one send against a recipient's preferences.

    ``channel`` is the channel to deliver on for SEND/DEFER (``None`` for SUPPRESS). ``defer_until``
    is the aware datetime (business timezone) a deferred message becomes due. ``reason`` is a short,
    non-sensitive label for logging.
    """

    outcome: DeliveryOutcome
    category: NotificationCategory
    essential: bool
    channel: NotificationChannel | None = None
    defer_until: datetime | None = None
    reason: str = ""


def _now() -> datetime:
    """Current time in the business timezone (Africa/Johannesburg)."""
    return datetime.now(APP_TIMEZONE)


def load_preference(db: Session, email: str) -> NotificationPreference | None:
    """Return the preference row for the account with ``email``, or ``None`` when there is none.

    ``None`` covers both "no such account" and "account with no preferences set" — both take the
    defaults — so a caller never needs to distinguish them (and the difference is never revealed).
    """
    normalized = email.lower().strip()
    return db.execute(
        select(NotificationPreference)
        .join(User, User.id == NotificationPreference.user_id)
        .where(User.email == normalized)
    ).scalar_one_or_none()


def chosen_channel(
    preference: NotificationPreference | None, category: NotificationCategory
) -> NotificationChannelPreference:
    """The channel a user has chosen for ``category`` (the default when unset or invalid).

    Essential categories can never resolve to ``off``: a stored ``off`` (or an unparseable value)
    is coerced back to email, the always-available fallback, so essential mail is never dropped.
    """
    raw = None
    if preference is not None:
        raw = preference.channel_by_category.get(category.value)
    try:
        channel = (
            NotificationChannelPreference(raw) if raw is not None else DEFAULT_CHANNEL
        )
    except ValueError:
        channel = DEFAULT_CHANNEL
    if channel is NotificationChannelPreference.OFF and is_essential_category(category):
        return DEFAULT_CHANNEL
    return channel


def _user_timezone(preference: NotificationPreference | None) -> ZoneInfo:
    """The recipient's timezone for quiet-hours evaluation (business timezone as the fallback)."""
    if preference is None or not preference.timezone:
        return APP_TIMEZONE
    try:
        return ZoneInfo(preference.timezone)
    except ZoneInfoNotFoundError, ValueError:
        # A bad stored zone must not break delivery — fall back to the business timezone.
        return APP_TIMEZONE


def _in_quiet_hours(start: time, end: time, moment: time) -> bool:
    """Whether ``moment`` (local wall-clock) falls in the ``[start, end)`` window.

    Equal start/end means the window is empty (quiet hours effectively off). A window with
    ``start > end`` wraps past midnight (e.g. 22:00–07:00).
    """
    if start == end:
        return False
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end


def _quiet_until(preference: NotificationPreference, now: datetime) -> datetime | None:
    """If ``now`` is inside the recipient's quiet hours, the next end-of-window datetime; else None.

    The returned datetime is timezone-aware in the business timezone (the retry sweep compares in
    that zone), and is always strictly after ``now``.
    """
    start = preference.quiet_hours_start
    end = preference.quiet_hours_end
    if start is None or end is None:
        return None
    tz = _user_timezone(preference)
    now_local = now.astimezone(tz)
    if not _in_quiet_hours(start, end, now_local.time()):
        return None
    candidate = now_local.replace(
        hour=end.hour, minute=end.minute, second=0, microsecond=0
    )
    # Advance to the first occurrence of the window's end strictly after now.
    while candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate.astimezone(APP_TIMEZONE)


def _patient_consent_denies(
    db: Session,
    recipient: str,
    template: NotificationTemplate,
    patient_id: str | None = None,
) -> bool:
    """Whether this message needs a patient's consent and does not have it (Issue 21).

    The one place a send is checked against consent, and it is here rather than in a send path
    because *every* send path resolves through this function — so no transport, queue or retry can
    skip it. A recipient who is not a patient (a staff email address, a number nobody has verified)
    is not consent-gated; a sign-in code is not either, because the patient asked for it by typing
    their number.

    ``patient_id`` names the patient directly (Issue 63): a push subscription id or a WhatsApp id is
    not a phone number, so a patient notification is checked by who it is for, not where it goes.
    """
    from src.commons.phone import InvalidPhoneNumberError
    from src.modules.patients import service as patients
    from src.modules.patients.consent import consent_required_for, has_consent

    purpose = consent_required_for(template)
    if purpose is None:
        return False
    if patient_id is not None:
        return not has_consent(db, patient_id, purpose)
    try:
        patient = patients.find_patient(db, recipient)
    except InvalidPhoneNumberError:
        return False  # not a number at all: an email recipient, so not a patient record
    if patient is None:
        return False
    return not has_consent(db, patient.id, purpose)


def resolve(
    db: Session,
    *,
    recipient_email: str,
    template: NotificationTemplate,
    channel: NotificationChannel,
    now: datetime | None = None,
    patient_id: str | None = None,
) -> DeliveryDecision:
    """Decide how one send should be handled against the recipient's consent and preferences.

    ``channel`` is the origin channel of the send (email for the transactional email path, sms for
    the SMS path). **Consent first** (Issue 21): a message to a patient that needs their agreement
    and does not have it is suppressed, whatever their preferences say and whatever category it is
    in — a patient who has not agreed to be messaged is not "opted out of a category", they never
    opted in. Then the recipient's per-category channel choice, quiet hours (deferred, not dropped)
    and the rule that essential mail to an *account holder* is never dropped.

    ``patient_id`` marks a patient notification (Issue 63): consent is checked for that patient, and
    the account-holder preferences below do not apply, because a patient has no account. A patient's
    own preferences (quiet hours, opt-outs) are Issue 67's, and they belong in this same function.
    """
    now = now or _now()
    category = notification_category_for(template)
    essential = is_essential_category(category)
    if _patient_consent_denies(db, recipient_email, template, patient_id):
        logger.info(
            "Notification suppressed: no patient consent for %s (%s)",
            template.value,
            category.value,
        )
        return DeliveryDecision(
            outcome=DeliveryOutcome.SUPPRESS,
            category=category,
            essential=essential,
            reason="no-patient-consent",
        )
    if patient_id is not None:
        return DeliveryDecision(
            outcome=DeliveryOutcome.SEND,
            category=category,
            essential=essential,
            channel=channel,
            reason="ok",
        )
    preference = load_preference(db, recipient_email)
    choice = chosen_channel(preference, category)

    # Channel gating. Essential mail is always allowed on its origin channel; non-essential mail is
    # delivered only on the channel the user chose for its category.
    if not essential:
        wanted = (
            NotificationChannel.EMAIL
            if choice is NotificationChannelPreference.EMAIL
            else NotificationChannel.SMS
            if choice is NotificationChannelPreference.SMS
            else None  # OFF
        )
        if wanted is not channel:
            return DeliveryDecision(
                outcome=DeliveryOutcome.SUPPRESS,
                category=category,
                essential=False,
                reason=(
                    "opted-out"
                    if choice is NotificationChannelPreference.OFF
                    else "other-channel"
                ),
            )

    # Quiet hours: defer non-urgent mail to the end of the window rather than dropping it.
    if preference is not None and not is_urgent_template(template):
        defer_until = _quiet_until(preference, now)
        if defer_until is not None:
            return DeliveryDecision(
                outcome=DeliveryOutcome.DEFER,
                category=category,
                essential=essential,
                channel=channel,
                defer_until=defer_until,
                reason="quiet-hours",
            )

    return DeliveryDecision(
        outcome=DeliveryOutcome.SEND,
        category=category,
        essential=essential,
        channel=channel,
        reason="ok",
    )


def email_allowed(
    db: Session, recipient_email: str, template: NotificationTemplate
) -> bool:
    """Whether ``template`` may be emailed to ``recipient_email`` right now (the messaging seam).

    A convenience predicate for callers that decide *whether to enqueue at all* before handing a
    message to the service (e.g. the in-app message notifier). A deferred message is still "allowed"
    — the service enqueues it for later — so only an outright SUPPRESS returns False.
    """
    decision = resolve(
        db,
        recipient_email=recipient_email,
        template=template,
        channel=NotificationChannel.EMAIL,
    )
    return decision.outcome is not DeliveryOutcome.SUPPRESS


# --------------------------------------------------------------------------------------
# Login-free unsubscribe (enumeration-safe).
# --------------------------------------------------------------------------------------


def unsubscribe_url(email: str, category: NotificationCategory) -> str | None:
    """Absolute, login-free unsubscribe URL for ``category`` mail to ``email`` — or ``None``.

    Returns ``None`` for essential categories (they cannot be unsubscribed) and when no
    ``public_base_url`` is configured (development), so the ``List-Unsubscribe`` header is simply
    omitted rather than pointing at a wrong host.
    """
    if is_essential_category(category):
        return None
    base = get_settings().public_base_url
    if not base:
        return None
    token = create_unsubscribe_token(email, category.value)
    return f"{base}/notifications/unsubscribe?token={token}"


@dataclass(frozen=True, slots=True)
class UnsubscribeResult:
    """The outcome of applying an unsubscribe token.

    ``valid`` is False for a bad/expired token. ``category`` and ``essential`` describe what the
    (valid) token referenced. ``changed`` is True when a preference was actually turned off. None of
    these fields reveal whether an account exists — a valid token for an unknown email reports
    success just like a known one.
    """

    valid: bool
    category: NotificationCategory | None = None
    essential: bool = False
    changed: bool = False


def _category_from_token(token: str) -> NotificationCategory | None:
    """Return the category a valid unsubscribe ``token`` references, or ``None`` when it is invalid."""
    payload = decode_unsubscribe_token(token)
    if payload is None:
        return None
    raw = payload.get("cat")
    if not isinstance(raw, str):
        return None
    try:
        return NotificationCategory(raw)
    except ValueError:
        return None


def preview_unsubscribe(token: str) -> UnsubscribeResult:
    """Decode an unsubscribe ``token`` for display, without changing anything.

    Backs the GET confirmation page: an invalid/expired token yields ``valid=False`` (a generic
    "link is no longer valid" page) and a valid one names the category — never touching the database,
    so it can never be used to probe which addresses exist.
    """
    category = _category_from_token(token)
    if category is None:
        return UnsubscribeResult(valid=False)
    return UnsubscribeResult(
        valid=True, category=category, essential=is_essential_category(category)
    )


def apply_unsubscribe(db: Session, token: str) -> UnsubscribeResult:
    """Apply a login-free unsubscribe: turn the token's category off for its recipient.

    Enumeration-safe: an invalid/expired token returns ``valid=False``; a valid token for an
    unknown address, or for an essential category, returns ``valid=True`` with ``changed=False`` —
    the caller shows the same confirmation in every "valid token" case, so the response never
    discloses whether an account exists. Idempotent: unsubscribing an already-off category is a
    no-op that still reports success.
    """
    payload = decode_unsubscribe_token(token)
    category = _category_from_token(token)
    if payload is None or category is None:
        return UnsubscribeResult(valid=False)
    essential = is_essential_category(category)
    email = str(payload.get("email", "")).lower().strip()

    # Essential categories cannot be unsubscribed; report a valid, unchanged result so the page can
    # explain that without revealing anything about the account.
    if essential or not email:
        return UnsubscribeResult(valid=True, category=category, essential=essential)

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        # Unknown address: still a valid token. Report success without a change (no enumeration).
        return UnsubscribeResult(valid=True, category=category, changed=False)

    preference = _get_or_create(db, user.id)
    channels = dict(preference.channel_by_category)
    already_off = (
        channels.get(category.value) == NotificationChannelPreference.OFF.value
    )
    channels[category.value] = NotificationChannelPreference.OFF.value
    preference.channel_by_category = channels
    db.flush()
    if not already_off:
        logger.info(
            "SECURITY_AUDIT %s outcome=%s category=%s",
            SecurityAuditEvent.NOTIFICATION_UNSUBSCRIBED.value,
            SecurityAuditOutcome.SUCCESS.value,
            category.value,
        )
    return UnsubscribeResult(valid=True, category=category, changed=not already_off)


# --------------------------------------------------------------------------------------
# Account-facing read / update.
# --------------------------------------------------------------------------------------


def _get_or_create(db: Session, user_id: str) -> NotificationPreference:
    """Return the user's preference row, creating an empty (all-default) one if absent."""
    preference = db.execute(
        select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    ).scalar_one_or_none()
    if preference is None:
        preference = NotificationPreference(user_id=user_id, channel_by_category={})
        db.add(preference)
        db.flush()
    return preference


def read_preferences(db: Session, user_id: str) -> NotificationPreferencesRead:
    """Build the effective preferences view for one account (defaults where unset)."""
    preference = db.execute(
        select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    ).scalar_one_or_none()
    categories = [
        CategoryPreference(
            category=category,
            channel=chosen_channel(preference, category),
            essential=is_essential_category(category),
        )
        for category in NotificationCategory
    ]
    return NotificationPreferencesRead(
        categories=categories,
        quiet_hours_start=preference.quiet_hours_start if preference else None,
        quiet_hours_end=preference.quiet_hours_end if preference else None,
        timezone=preference.timezone if preference else APP_TIMEZONE.key,
    )


class InvalidTimezoneError(ValueError):
    """Raised when an update supplies a timezone name that is not a known IANA zone."""


def update_preferences(
    db: Session, user_id: str, update: NotificationPreferencesUpdate
) -> NotificationPreferencesRead:
    """Apply a partial preferences update for one account and return the new effective view.

    Only provided fields change. An essential category set to ``off`` is coerced back to ``email``
    (essential mail can be re-channelled, not disabled). An unknown timezone is rejected with
    :class:`InvalidTimezoneError`.
    """
    preference = _get_or_create(db, user_id)

    if update.channels is not None:
        channels = dict(preference.channel_by_category)
        for category, channel in update.channels.items():
            if channel is NotificationChannelPreference.OFF and is_essential_category(
                category
            ):
                channel = DEFAULT_CHANNEL
            channels[category.value] = channel.value
        preference.channel_by_category = channels

    if update.clear_quiet_hours:
        preference.quiet_hours_start = None
        preference.quiet_hours_end = None
    elif update.quiet_hours_start is not None and update.quiet_hours_end is not None:
        preference.quiet_hours_start = update.quiet_hours_start
        preference.quiet_hours_end = update.quiet_hours_end

    if update.timezone is not None:
        name = update.timezone.strip()
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise InvalidTimezoneError(name) from exc
        preference.timezone = name

    db.flush()
    return read_preferences(db, user_id)
