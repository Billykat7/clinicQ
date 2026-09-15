"""Web push as a patient transport: free and instant on a phone that allowed notifications (Issue 64).

First in the chain. It can reach a patient who has at least one push subscription while web push is
configured (``WEB_PUSH_VAPID_*``); otherwise :meth:`WebPushTransport.address_for` answers ``None`` and the
chain moves on to WhatsApp or SMS, which is also what happens to a patient who declined notifications.

**What a push says is deliberately little**: a title, and a body naming the ticket number and the clinic,
and nothing else. A lock screen is read by whoever picks the phone up, so no queue name (a queue can be
"HIV clinic"), room, reason or name ever goes into a payload (:data:`PAYLOAD_KEYS`, checked by a test).
"""

from __future__ import annotations

from typing import Final

from src.commons.enums import NotificationChannel
from src.modules.notifications.schemas import RenderedMessage
from src.modules.notifications.transports.base import (
    FREE,
    PatientAddresses,
    Transport,
    TransportReceipt,
)
from src.modules.notifications.webpush import (
    PushUrgency,
    SubscriptionGoneError,
    VapidSender,
)

#: Everything a payload may carry. The service worker shows ``title`` and ``body``, opens ``url``.
PAYLOAD_KEYS: Final = frozenset({"title", "body", "url", "tag"})
#: Where a tap opens when the message names no page.
DEFAULT_URL: Final = "/"
#: The provider label recorded on the ledger row.
PROVIDER: Final = "web_push"


def payload_for(message: RenderedMessage) -> dict[str, str]:
    """The JSON a browser receives for ``message``: :data:`PAYLOAD_KEYS` and nothing more."""
    payload = {
        "title": message.subject or "",
        "body": message.text,
        "url": message.link
        if message.link and message.link.startswith("/")
        else DEFAULT_URL,
    }
    if message.tag:
        payload["tag"] = message.tag
    return payload


class WebPushTransport(Transport):
    """Reach a patient's browser through their push subscription."""

    channel = NotificationChannel.WEB_PUSH
    free = True

    def __init__(self, sender: VapidSender | None = None) -> None:
        """``sender`` is ``None`` while web push is not configured: the transport then reaches nobody."""
        self.sender = sender

    def address_for(self, patient: PatientAddresses) -> str | None:
        """The patient's newest push subscription, or ``None`` with none or no sender."""
        if self.sender is None or not patient.push_targets:
            return None
        return patient.push_targets[0].id

    def send(
        self,
        *,
        to: str,
        message: RenderedMessage,
        patient: PatientAddresses | None = None,
    ) -> TransportReceipt:
        """Encrypt and push ``message`` to subscription ``to``.

        Raises:
            SubscriptionGoneError: The subscription no longer exists, here or at the push service.
            PermanentTransportError, TransportError: As :meth:`VapidSender.send`.
        """
        if self.sender is None:
            raise SubscriptionGoneError(to, "web push is not configured")
        target = next(
            (t for t in (patient.push_targets if patient else ()) if t.id == to), None
        )
        if target is None:
            raise SubscriptionGoneError(to, "the subscription was removed")
        message_id = self.sender.send(
            target, payload_for(message), urgency=PushUrgency.HIGH
        )
        return TransportReceipt(
            provider=PROVIDER, provider_message_id=message_id, cost=FREE
        )
