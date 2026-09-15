"""SMS as a patient transport: the paid last resort that reaches every phone (Issue 63).

A thin adapter over the existing :class:`~src.modules.notifications.sms.SmsProvider`, so there is
still exactly one SMS provider interface. Issue 65 adds the real gateway, its cost and its caps
behind that interface; this adapter does not change when it does.
"""

from __future__ import annotations

from src.commons.enums import NotificationChannel
from src.core.config import get_settings
from src.modules.notifications.schemas import RenderedMessage
from src.modules.notifications.sms import (
    SmsProvider,
    SmsRecipientRejectedError,
    SmsSendError,
)
from src.modules.notifications.transports.base import (
    PatientAddresses,
    PermanentTransportError,
    Transport,
    TransportError,
    TransportReceipt,
)


class SmsTransport(Transport):
    """Send a patient notification as one SMS through the configured provider."""

    channel = NotificationChannel.SMS
    free = False

    def __init__(self, provider: SmsProvider) -> None:
        """Wrap ``provider``; the provider decides the gateway, this class only adapts it."""
        self.provider = provider

    def address_for(self, patient: PatientAddresses) -> str | None:
        """The patient's verified number: every patient has one, so SMS always can."""
        return patient.phone_e164

    def send(
        self,
        *,
        to: str,
        message: RenderedMessage,
        patient: PatientAddresses | None = None,
    ) -> TransportReceipt:
        """Send the message's text; a number the gateway rejects is a permanent failure."""
        try:
            message_id = self.provider.send(
                to=to, text=message.text, sender=get_settings().sms_from
            )
        except SmsRecipientRejectedError as exc:
            raise PermanentTransportError(str(exc)) from exc
        except SmsSendError as exc:
            raise TransportError(str(exc)) from exc
        return TransportReceipt(
            provider=self.provider.kind.value,
            provider_message_id=message_id,
            cost=self.provider.cost_of(message.text),
        )
