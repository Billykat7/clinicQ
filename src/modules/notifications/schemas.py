"""Schemas for the notification service (Issue #67).

:class:`RenderedMessage` is the channel-agnostic body a template renders to — an email uses all
three fields, an SMS uses only ``text``. :class:`NotificationRead` is the read model behind the
admin status-query endpoint. :class:`DeliveryReceipt` is the shape a provider's delivery-status
webhook posts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from src.commons.enums import (
    NotificationCategory,
    NotificationChannel,
    NotificationChannelPreference,
    NotificationStatus,
    NotificationTemplate,
    PatientEvent,
    TemplateSource,
)


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    """A rendered transactional message ready for a channel to deliver.

    ``subject`` and ``html`` are email-only (``None`` for SMS); ``text`` is the always-present
    plain-text body (and the whole message for SMS).
    """

    text: str
    subject: str | None = None
    html: str | None = None
    link: str | None = None
    """Where tapping the message opens (a web push), a path on this site."""
    tag: str | None = None
    """Messages with the same tag replace each other on the phone (a web push)."""


class NotificationRead(BaseModel):
    """Delivery status of one notification, returned by the admin status-query endpoint."""

    id: str = Field(description="Notification id.")
    channel: NotificationChannel = Field(description="Delivery channel.")
    template_key: NotificationTemplate = Field(description="Which template was sent.")
    recipient: str = Field(
        description="Destination address: email, number (SMS), WhatsApp id or push subscription."
    )
    patient_id: str | None = Field(
        default=None, description="The patient it is for (null for an account holder)."
    )
    site_id: str | None = Field(default=None, description="The clinic that sent it.")
    event: PatientEvent | None = Field(
        default=None, description="What happened to the patient's ticket."
    )
    fallback_of_id: str | None = Field(
        default=None,
        description="The row on the transport that failed before this one.",
    )
    cost: Decimal | None = Field(
        default=None, description="What the provider charged (null until sent)."
    )
    cost_currency: str | None = Field(
        default=None, description="ISO 4217 currency of cost."
    )
    template_version_id: str | None = Field(
        default=None,
        description="The exact template version the message was rendered from (Issue 66).",
    )
    language: str | None = Field(
        default=None, description="The language it went out in."
    )
    subject: str | None = Field(default=None, description="Email subject (SMS: null).")
    status: NotificationStatus = Field(description="Current delivery state.")
    provider: str | None = Field(default=None, description="Provider that handled it.")
    provider_message_id: str | None = Field(
        default=None, description="Provider's id for the message (webhook correlation)."
    )
    attempts: int = Field(description="Delivery attempts made so far.")
    max_attempts: int = Field(description="Attempt budget before dead-lettering.")
    last_error: str | None = Field(
        default=None, description="Most recent failure message."
    )
    next_attempt_at: datetime | None = Field(
        default=None, description="When the row is next eligible for retry (SAST)."
    )
    sent_at: datetime | None = Field(
        default=None, description="When a provider accepted it (SAST)."
    )
    delivered_at: datetime | None = Field(
        default=None, description="When delivery was confirmed by webhook (SAST)."
    )
    failed_at: datetime | None = Field(
        default=None, description="When the last attempt failed (SAST)."
    )
    created_at: datetime = Field(description="When the row was created (SAST).")

    model_config = {"from_attributes": True}


class NotificationListOut(BaseModel):
    """Paginated notification listing for the delivery viewer (Issue #87, ``logs`` READ)."""

    items: list[NotificationRead] = Field(description="The page of notification rows.")
    total: int = Field(
        ge=0, description="Total notifications matching the filters (pre-paging)."
    )


class CategoryPreference(BaseModel):
    """One category and the channel the user has chosen for it (Issue #72)."""

    category: NotificationCategory = Field(description="The notification category.")
    channel: NotificationChannelPreference = Field(
        description="Chosen channel: email, sms, or off."
    )
    essential: bool = Field(
        description=(
            "Whether this category is essential — it cannot be turned off, only re-channelled."
        )
    )


class NotificationPreferencesRead(BaseModel):
    """A user's effective notification preferences, returned by the account preferences endpoint."""

    categories: list[CategoryPreference] = Field(
        description="Every category with the user's effective channel choice."
    )
    quiet_hours_start: time | None = Field(
        default=None,
        description="Local start of quiet hours (HH:MM), or null when quiet hours are off.",
    )
    quiet_hours_end: time | None = Field(
        default=None,
        description="Local end of quiet hours (HH:MM), or null when quiet hours are off.",
    )
    timezone: str = Field(
        description="IANA timezone quiet hours are evaluated in (e.g. Africa/Johannesburg)."
    )


class NotificationPreferencesUpdate(BaseModel):
    """A partial update of a user's notification preferences (only provided fields change).

    ``channels`` maps a category to a chosen channel; an essential category set to ``off`` is
    coerced back to ``email`` by the service (essential mail can be re-channelled, not disabled).
    Quiet hours must be set or cleared as a pair.
    """

    channels: dict[NotificationCategory, NotificationChannelPreference] | None = Field(
        default=None,
        description="Per-category channel choices to apply (partial; unlisted categories keep).",
    )
    quiet_hours_start: time | None = Field(
        default=None, description="Local start of quiet hours (HH:MM)."
    )
    quiet_hours_end: time | None = Field(
        default=None, description="Local end of quiet hours (HH:MM)."
    )
    clear_quiet_hours: bool = Field(
        default=False,
        description="When true, turn quiet hours off (takes precedence over start/end).",
    )
    timezone: str | None = Field(
        default=None,
        max_length=64,
        description="IANA timezone quiet hours are evaluated in.",
    )

    @model_validator(mode="after")
    def _quiet_hours_paired(self) -> NotificationPreferencesUpdate:
        """Quiet hours must be given as a pair (both start and end) unless clearing them."""
        if self.clear_quiet_hours:
            return self
        start_set = self.quiet_hours_start is not None
        end_set = self.quiet_hours_end is not None
        if start_set != end_set:
            raise ValueError(
                "quiet_hours_start and quiet_hours_end must be provided together."
            )
        return self


class UnsubscribeConfirm(BaseModel):
    """The body of a one-click unsubscribe POST: the signed token identifying what to unsubscribe."""

    token: str = Field(
        min_length=1, description="The signed unsubscribe token from the link."
    )


class CenterItemRead(BaseModel):
    """One preview in the notification-centre dropdown — a notification or a message (Issue #113)."""

    id: str = Field(
        description="Item id: an in-app notification id, or a message thread id."
    )
    source: str = Field(
        description="Where the item came from: 'notification' or 'message'."
    )
    category: NotificationCategory = Field(description="The notification category.")
    title: str = Field(description="Short headline for the preview.")
    snippet: str = Field(
        description="A trimmed preview of the body (escaped at render time)."
    )
    created_at: datetime | None = Field(
        default=None, description="When the event/last message occurred (SAST)."
    )
    read: bool = Field(
        description="Whether the item is read (no unread content) for the caller."
    )
    link: str | None = Field(
        default=None,
        description="Where 'view' opens the item full-screen, when it has a deep link.",
    )


class CenterUnreadOut(BaseModel):
    """The bell badge total — unread in-app notifications plus unread messages (Issue #113)."""

    unread_total: int = Field(
        ge=0,
        description="Unread in-app notifications plus unread messages across the caller's inbox.",
    )


class CenterSummaryOut(BaseModel):
    """The notification-centre payload the dropdown renders: the badge total and recent previews."""

    unread_total: int = Field(
        ge=0, description="The bell badge total (notifications + messages)."
    )
    items: list[CenterItemRead] = Field(
        description="Recent previews, newest first, merged across notifications and messages."
    )


class CenterMarkResult(BaseModel):
    """Result of a read-state mutation: the new bell total after the change."""

    unread_total: int = Field(
        ge=0, description="The caller's bell total after the read/unread change."
    )
    changed: int = Field(
        default=1,
        ge=0,
        description="How many items changed state (1, or N for mark-all).",
    )


class DeliveryReceipt(BaseModel):
    """A provider delivery-status callback: which message, and whether it landed."""

    provider_message_id: str = Field(
        min_length=1,
        description="The provider's message id, matched against a notification row.",
    )
    delivered: bool = Field(
        description="True when the provider confirms delivery; False for a bounce/failure."
    )
    detail: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional provider-supplied reason, recorded on a failure.",
    )


# --- Web push (Issue 64) ---------------------------------------------------------------------------

#: The longest endpoint a browser's push service hands out that the server accepts.
MAX_PUSH_ENDPOINT_LENGTH = 2048


class WebPushKeyOut(BaseModel):
    """Whether web push is on, and the key a browser subscribes with."""

    enabled: bool
    public_key: str | None = Field(
        default=None,
        description="VAPID application server key, base64url; null while web push is off.",
    )


class PushSubscriptionKeys(BaseModel):
    """The browser's keys, as ``PushSubscription.toJSON()`` gives them."""

    p256dh: str = Field(min_length=80, max_length=128)
    auth: str = Field(min_length=16, max_length=48)


class PushSubscriptionIn(BaseModel):
    """A browser's push subscription, as ``PushSubscription.toJSON()`` gives it."""

    model_config = {"populate_by_name": True}

    endpoint: str = Field(min_length=12, max_length=MAX_PUSH_ENDPOINT_LENGTH)
    keys: PushSubscriptionKeys
    expiration_time: int | None = Field(
        default=None,
        alias="expirationTime",
        ge=0,
        description="When the browser says the subscription expires, epoch milliseconds; usually null.",
    )


class PushSubscriptionOut(BaseModel):
    """The stored subscription."""

    id: str
    created: bool
    """False when this browser had already subscribed (its keys were refreshed)."""


class PushUnsubscribeIn(BaseModel):
    """Which of the signed-in patient's browsers to stop notifying."""

    endpoint: str = Field(min_length=12, max_length=MAX_PUSH_ENDPOINT_LENGTH)


# --- SMS budget and kill switch (Issue 65) --------------------------------------------------------


class SmsKillSwitchOut(BaseModel):
    """Whether every SMS is stopped, and who said so."""

    enabled: bool
    reason: str | None = None
    changed_by: str | None = None
    changed_at: datetime | None = None


class SmsKillSwitchIn(BaseModel):
    """Stop every SMS (``enabled: true``) or let them send again, with a reason for the team."""

    enabled: bool
    reason: str | None = Field(default=None, max_length=200)


class SmsBudgetOut(BaseModel):
    """A clinic's SMS today: its cap, what it has sent and what that cost (Issue 65)."""

    site_id: str
    day: date
    cap: int = Field(ge=0)
    own_cap: int | None = Field(
        default=None,
        description="The clinic's own cap; null uses the platform default.",
    )
    sent: int = Field(ge=0)
    remaining: int = Field(ge=0)
    spend: Decimal
    currency: str


class SmsBudgetIn(BaseModel):
    """Set a clinic's own daily SMS cap, or ``null`` to use the platform default."""

    daily_cap: int | None = Field(default=None, ge=0, le=100_000)


# --- Template registry and editor (Issue 66) ---------------------------------------------------------


class TemplateVersionOut(BaseModel):
    """One version of one message template: its words and where they came from."""

    id: str | None = Field(
        default=None,
        description="The stored version's id; null for a locale file version not yet sent.",
    )
    template: NotificationTemplate
    channel: NotificationChannel
    language: str
    version: int
    subject: str | None = None
    body: str
    source: TemplateSource
    created_by: str | None = None
    reviewed_by: str | None = Field(
        default=None,
        description="The fluent speaker who checked the words; null until someone has.",
    )
    created_at: datetime | None = None


class TemplateKeyOut(BaseModel):
    """A template in one channel and language: what is sent now, the blanks it may use, and its history."""

    current: TemplateVersionOut
    variables: list[str]
    versions: list[TemplateVersionOut]


class TemplateListOut(BaseModel):
    """Every patient message template, per channel and language, as it is sent now."""

    languages: list[str]
    items: list[TemplateVersionOut]


class TemplateDraftIn(BaseModel):
    """Words to preview, or to publish as a new version."""

    body: str = Field(min_length=1, max_length=1000)
    subject: str | None = Field(default=None, max_length=120)
    reviewed_by: str | None = Field(default=None, max_length=200)


class TemplatePreviewIn(TemplateDraftIn):
    """Words to preview for a template, channel and language."""

    template: NotificationTemplate
    channel: NotificationChannel
    language: str = Field(min_length=2, max_length=5)


class TemplatePreviewOut(BaseModel):
    """Exactly what a patient would receive, and whether the words may be published.

    ``text``/``subject`` are rendered from a sample ticket, as the channel sends them (an SMS after its
    GSM 7-bit normalisation). For an SMS, ``characters``/``segments`` count that message, and
    ``worst_characters``/``worst_segments`` the same words with the longest names the database allows,
    which is what must fit one segment.
    """

    valid: bool
    error: str | None = None
    subject: str | None = None
    text: str = ""
    characters: int | None = None
    encoding: str | None = None
    segments: int | None = None
    worst_characters: int | None = None
    worst_segments: int | None = None


# --- Patient preferences (Issue 67) ----------------------------------------------------------------


class PatientPreferencesOut(BaseModel):
    """How and when a patient is told about their ticket, as they set it."""

    opted_out: bool
    """True when the patient stopped every message, on every channel."""
    opted_out_at: datetime | None = None
    preferred_channel: NotificationChannel | None = None
    language: str | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    muted_events: list[PatientEvent]
    quiet_hours_exempt: list[PatientEvent]
    """The messages quiet hours never hold back: each says "come now" about a visit happening now."""
    languages: list[str]
    """The languages a patient may choose."""


class PatientPreferencesIn(BaseModel):
    """A change to a patient's preferences. A field left out is not changed; ``null`` clears it."""

    model_config = {"extra": "forbid"}

    opted_out: bool | None = None
    preferred_channel: NotificationChannel | None = None
    language: str | None = Field(default=None, min_length=2, max_length=5)
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    muted_events: list[PatientEvent] | None = None

    @model_validator(mode="after")
    def quiet_hours_come_in_pairs(self) -> PatientPreferencesIn:
        """A quiet-hours window needs a start and an end, or neither."""
        fields = self.model_fields_set
        if ("quiet_hours_start" in fields) != ("quiet_hours_end" in fields) or (
            (self.quiet_hours_start is None) != (self.quiet_hours_end is None)
        ):
            raise ValueError("Quiet hours need both a start and an end, or neither.")
        return self
