"""SMS as a patient transport: the paid last resort that reaches every phone (Issues 63, 65).

A thin adapter over the one :class:`~src.modules.notifications.sms.SmsProvider` interface: the logging and
fake providers, and the Africa's Talking gateway (Issue 65), all sit behind it.

Before a message is handed over it is made to cost what it should (Issue 65):

* **normalised to the GSM 7-bit alphabet** (:func:`~src.modules.notifications.sms_segments.to_gsm7`), so a
  stray en dash cannot turn a one-segment message into a UCS-2 message billed twice;
* **counted**, and refused as a permanent failure when it would be more than ``SMS_MAX_SEGMENTS`` parts,
  rather than silently sent as three.

What the gateway charged is what is recorded.
"""

from __future__ import annotations

from src.commons.enums import NotificationChannel, SmsBlockReason
from src.core.config import get_settings
from src.modules.notifications.schemas import RenderedMessage
from src.modules.notifications.sms import (
    SmsProvider,
    SmsRecipientRejectedError,
    SmsSendError,
)
from src.modules.notifications.sms_segments import count_segments, to_gsm7
from src.modules.notifications.transports.base import (
    PatientAddresses,
    PermanentTransportError,
    Transport,
    TransportError,
    TransportReceipt,
)


class SmsTooLongError(PermanentTransportError):
    """The message would be split into more billable parts than ``SMS_MAX_SEGMENTS`` allows."""


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
        """Send the message's text in as few parts as it can be; a rejected number is a permanent failure.

        Raises:
            SmsTooLongError: More than ``SMS_MAX_SEGMENTS`` parts, even in GSM 7-bit.
            PermanentTransportError: The gateway refused the number.
            TransportError: The gateway did not accept the message this time.
        """
        text = to_gsm7(message.text)
        count = count_segments(text)
        limit = get_settings().sms_max_segments
        if count.segments > limit:
            raise SmsTooLongError(
                f"{SmsBlockReason.TOO_LONG.value}: {count.segments} {count.encoding.value} "
                f"segments ({count.units} characters), more than SMS_MAX_SEGMENTS={limit}"
            )
        try:
            receipt = self.provider.send_message(
                to=to, text=text, sender=get_settings().sms_from
            )
        except SmsRecipientRejectedError as exc:
            raise PermanentTransportError(str(exc)) from exc
        except SmsSendError as exc:
            raise TransportError(str(exc)) from exc
        return TransportReceipt(
            provider=self.provider.kind.value,
            provider_message_id=receipt.message_id,
            cost=receipt.cost,
            currency=receipt.currency,
        )
