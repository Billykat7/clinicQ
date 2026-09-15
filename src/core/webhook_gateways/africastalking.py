"""Africa's Talking SMS delivery receipts: verify, parse, record once, apply (Issue 65).

The gateway calls back, form-encoded, each time a message's delivery status changes: ``id`` (its message
id), ``status`` (``Success``, ``Failed``, ``Rejected``, ``Buffered``, ``Sent``, ``Submitted``,
``Expired``, ``AbsentSubscriber``…), ``phoneNumber``, ``networkCode``, ``failureReason``, ``retryCount``.

**The gateway does not sign its callbacks**, so the payment gateways' HMAC check has nothing to check here.
What stands in for the signature is a secret in the callback URL the operator registers with the gateway
(``SMS_WEBHOOK_TOKEN``, 256 random bits), compared in constant time **before the body is parsed**, as the
payment webhooks verify before parsing. Anyone without the URL gets the same ``404`` as a path that does
not exist. The request log masks the token (:mod:`src.core.log_redaction`).

**Idempotent, like the payment webhooks.** Each receipt is recorded as an
:class:`~src.database.models.sms_budget.SmsDeliveryEvent` keyed ``africas_talking:<id>:<status>`` before it
is applied, so the gateway retrying a receipt changes the ledger once.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qs

from sqlalchemy.orm import Session

from src.commons.enums import SmsDeliveryState, SmsProviderKind
from src.commons.time import now_sast
from src.core.config import Settings
from src.database.models.sms_budget import SmsDeliveryEvent
from src.modules.notifications import service as notifications

#: What each gateway status means for the ledger. A status not listed is treated as in transit.
STATUS_STATE: Final[dict[str, SmsDeliveryState]] = {
    "Success": SmsDeliveryState.DELIVERED,
    "Failed": SmsDeliveryState.FAILED,
    "Rejected": SmsDeliveryState.FAILED,
    "Expired": SmsDeliveryState.FAILED,
    "AbsentSubscriber": SmsDeliveryState.FAILED,
    "Sent": SmsDeliveryState.IN_TRANSIT,
    "Submitted": SmsDeliveryState.IN_TRANSIT,
    "Buffered": SmsDeliveryState.IN_TRANSIT,
}
PROVIDER: Final = SmsProviderKind.AFRICAS_TALKING.value


class SmsWebhookNotConfiguredError(Exception):
    """``SMS_WEBHOOK_TOKEN`` is unset: the receipt webhook is off."""


class SmsWebhookRefusedError(Exception):
    """The token in the URL is not the configured one, or the body is not a receipt."""


@dataclass(frozen=True, slots=True)
class Receipt:
    """One delivery receipt, parsed."""

    message_id: str
    status: str
    failure_reason: str | None

    @property
    def state(self) -> SmsDeliveryState:
        """What the status means for the ledger."""
        return STATUS_STATE.get(self.status, SmsDeliveryState.IN_TRANSIT)

    @property
    def event_id(self) -> str:
        """The idempotency key."""
        return f"{PROVIDER}:{self.message_id}:{self.status}"


@dataclass(frozen=True, slots=True)
class ProcessedReceipt:
    """What happened to a receipt: ``processed``, ``duplicate`` or ``ignored`` (an unknown message)."""

    outcome: str
    event_id: str


def verify_token(token: str, settings: Settings) -> None:
    """Refuse the call unless ``token`` is the configured secret (constant time).

    Raises:
        SmsWebhookNotConfiguredError: No token is configured.
        SmsWebhookRefusedError: The token does not match.
    """
    expected = settings.sms_webhook_token
    if not expected:
        raise SmsWebhookNotConfiguredError
    if not hmac.compare_digest(token.encode(), expected.encode()):
        raise SmsWebhookRefusedError("unknown callback")


def parse_receipt(payload: bytes) -> Receipt:
    """Read a form-encoded receipt.

    Raises:
        SmsWebhookRefusedError: No ``id`` or ``status``.
    """
    try:
        fields = parse_qs(
            payload.decode("utf-8"), keep_blank_values=True, strict_parsing=False
        )
    except UnicodeDecodeError as exc:
        raise SmsWebhookRefusedError("the body is not a receipt") from exc
    message_id = (fields.get("id") or [""])[0].strip()
    status = (fields.get("status") or [""])[0].strip()
    if not message_id or not status or len(message_id) > 255 or len(status) > 40:
        raise SmsWebhookRefusedError("the body is not a receipt")
    reason = (fields.get("failureReason") or [""])[0].strip() or None
    return Receipt(message_id=message_id, status=status, failure_reason=reason)


def process_receipt(db: Session, receipt: Receipt) -> ProcessedReceipt:
    """Record the receipt once and apply it to its ledger row. The caller commits."""
    if db.get(SmsDeliveryEvent, receipt.event_id) is not None:
        return ProcessedReceipt("duplicate", receipt.event_id)
    moment = now_sast()
    db.add(
        SmsDeliveryEvent(
            id=receipt.event_id,
            provider=PROVIDER,
            provider_message_id=receipt.message_id,
            status=receipt.status,
            state=receipt.state.value,
            received_at=moment,
        )
    )
    row = notifications.apply_sms_receipt(
        db,
        provider_message_id=receipt.message_id,
        state=receipt.state,
        detail=receipt.failure_reason or receipt.status,
        now=moment,
    )
    return ProcessedReceipt(
        "processed" if row is not None else "ignored", receipt.event_id
    )


# --- Replies (Issue 67) -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reply:
    """One SMS a patient sent to the service's number, parsed."""

    message_id: str
    phone: str
    text: str

    @property
    def event_id(self) -> str:
        """The idempotency key."""
        return f"{PROVIDER}:{self.message_id}"


def parse_reply(payload: bytes) -> Reply:
    """Read a form-encoded incoming message: ``id``, ``from``, ``text``.

    Raises:
        SmsWebhookRefusedError: No ``id`` or ``from``.
    """
    try:
        fields = parse_qs(payload.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError as exc:
        raise SmsWebhookRefusedError("the body is not a message") from exc
    message_id = (fields.get("id") or [""])[0].strip()
    phone = (fields.get("from") or [""])[0].strip()
    if not message_id or not phone or len(message_id) > 255 or len(phone) > 32:
        raise SmsWebhookRefusedError("the body is not a message")
    return Reply(
        message_id=message_id, phone=phone, text=(fields.get("text") or [""])[0][:480]
    )


def process_reply(db: Session, reply: Reply) -> ProcessedReceipt:
    """Record the reply once and apply its keyword. The caller commits."""
    from src.database.models.sms_budget import SmsInboundEvent
    from src.modules.notifications import patient_preferences

    if db.get(SmsInboundEvent, reply.event_id) is not None:
        return ProcessedReceipt("duplicate", reply.event_id)
    moment = now_sast()
    result = patient_preferences.apply_reply(db, reply.phone, reply.text, now=moment)
    words = reply.text.strip().split()
    db.add(
        SmsInboundEvent(
            id=reply.event_id,
            provider=PROVIDER,
            keyword=words[0].upper()[:16]
            if words and result.outcome.value != "ignored"
            else None,
            outcome=result.outcome.value,
            patient_id=result.patient_id,
            received_at=moment,
        )
    )
    return ProcessedReceipt(result.outcome.value, reply.event_id)
