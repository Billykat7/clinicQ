"""WhatsApp as a patient transport: free inside a conversation session (Issue 63 skeleton).

Second in the chain, after web push. This issue defines its place and its contract; Issue 76 adds
the WhatsApp Business provider and its template approval. With no provider configured there is no
address to send to, so :meth:`WhatsAppTransport.address_for` answers ``None`` and the chain moves on.
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


class WhatsAppTransport(Transport):
    """Reach a patient on WhatsApp by the id WhatsApp gave their conversation."""

    channel = NotificationChannel.WHATSAPP
    free = True

    def __init__(self, *, configured: bool = False) -> None:
        """``configured`` is true once a WhatsApp provider exists (Issue 76)."""
        self.configured = configured

    def address_for(self, patient: PatientAddresses) -> str | None:
        """The patient's WhatsApp id when a provider is configured, else ``None``."""
        return patient.whatsapp_id if self.configured else None

    def send(
        self,
        *,
        to: str,
        message: RenderedMessage,
        patient: PatientAddresses | None = None,
    ) -> TransportReceipt:
        """No WhatsApp provider exists before Issue 76, so any attempt is a retryable failure."""
        raise TransportError("WhatsApp has no provider configured (Issue 76)")
