"""Web push as a patient transport: free and instant on an installed PWA (Issue 63 skeleton).

The first transport in the chain. This issue defines its place and its contract; Issue 64 adds the
VAPID keys, the subscription store and the sender. Until a patient has a push subscription there is
no address, so :meth:`WebPushTransport.address_for` answers ``None`` and the chain moves on to the
next transport without recording a failure.
"""

from __future__ import annotations

from src.commons.enums import NotificationChannel
from src.modules.notifications.schemas import RenderedMessage
from src.modules.notifications.transports.base import (
    PatientAddresses,
    Transport,
    TransportError,
    TransportReceipt,
)


class WebPushTransport(Transport):
    """Reach a patient's browser through their push subscription."""

    channel = NotificationChannel.WEB_PUSH
    free = True

    def address_for(self, patient: PatientAddresses) -> str | None:
        """The patient's newest push subscription, or ``None`` when they have none."""
        return (
            patient.push_subscription_ids[0] if patient.push_subscription_ids else None
        )

    def send(self, *, to: str, message: RenderedMessage) -> TransportReceipt:
        """No push sender is configured before Issue 64, so any attempt is a retryable failure."""
        raise TransportError("web push has no VAPID sender configured (Issue 64)")
