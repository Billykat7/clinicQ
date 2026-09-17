"""Email as a patient transport: free, and the only one that reaches a patient with no phone (Issue 220).

:data:`~src.commons.enums.NotificationChannel.EMAIL` used to be for account holders only, because a
patient had no address on record. Issue 219 gave them one, and a patient who signed in with an
address may have **no mobile number at all** — so without this adapter such a patient joins a queue
and is never told their ticket was called: the chain is web push, WhatsApp, SMS, and they match none
of it.

It sits **second** in :data:`~src.commons.enums.PATIENT_TRANSPORT_CHAIN`, after web push and ahead of
WhatsApp, for one reason: we hold an address for every patient who signed in with one, and only
sometimes a WhatsApp id. Both are free, so the order between them is about which is likelier to
arrive, not cost; SMS stays last because it is the one that costs money.

Two things it deliberately does **not** do:

* **It does not go through** :func:`src.core.email_send.send` **or** :func:`~src.modules.notifications.service.deliver_email`.
  Those are the account-holder path: they write their own ledger row and resolve the *recipient's*
  staff preferences. A patient notification already has its row and has already been through the
  patient's own consent, opt-out and quiet-hours gate before a transport is called. Going through
  them would mean two rows and two preference systems for one message. This hands the SMTP
  transport the message directly, the same way :class:`~src.modules.notifications.transports.sms.SmsTransport`
  hands its provider one.
* **It adds no unsubscribe header.** A patient's opt-out is per category in their own preferences
  (Issue 67) and is applied before the send; a one-click header pointing at the staff unsubscribe
  flow would be a second, inconsistent switch.
"""

from __future__ import annotations

from src.commons.enums import NotificationChannel
from src.modules.notifications.schemas import RenderedMessage
from src.modules.notifications.transports.base import (
    FREE,
    PatientAddresses,
    PermanentTransportError,
    Transport,
    TransportError,
    TransportReceipt,
)

#: What the ledger records as having carried the message, matching the account-holder path's name.
EMAIL_PROVIDER = "smtp"


class EmailTransport(Transport):
    """Send a patient notification as one email through the configured SMTP server."""

    channel = NotificationChannel.EMAIL
    free = True

    def __init__(self, *, configured: bool = True) -> None:
        """``configured`` is false when ``SMTP_HOST`` is unset: there is then nowhere to send."""
        self.configured = configured

    def address_for(self, patient: PatientAddresses) -> str | None:
        """The patient's verified address, when this deployment can send mail at all.

        ``None`` for a patient who has never signed in with an address — which is most of them — and
        for a deployment with no SMTP server, so the chain moves on without recording a failure.
        """
        return patient.email if self.configured else None

    def send(
        self,
        *,
        to: str,
        message: RenderedMessage,
        patient: PatientAddresses | None = None,
    ) -> TransportReceipt:
        """Hand one message to SMTP and return its Message-ID.

        Raises:
            PermanentTransportError: The server refused the address; retrying it cannot work.
            TransportError: The server did not accept the message this time.
        """
        # Imported here, not at module load: ``src.core.email_send`` imports this package's service,
        # so a top-level import would be circular — the same reason the service's own email helper
        # imports it lazily.
        from src.core.email_send import EmailDeliveryError, deliver_smtp

        try:
            message_id = deliver_smtp(
                to=to,
                subject=message.subject or "",
                text=message.text,
                html=message.html,
            )
        except EmailDeliveryError as exc:
            raise (PermanentTransportError if _is_permanent(exc) else TransportError)(
                str(exc)
            ) from exc
        return TransportReceipt(
            provider=EMAIL_PROVIDER, provider_message_id=message_id, cost=FREE
        )


#: What an SMTP server says when the address itself is the problem: RFC 5321's 5.1.x replies, and the
#: words the common servers put beside them. A 4.x.x reply, a timeout or a refused connection is the
#: server having a bad moment, which is what :class:`TransportError` means.
_PERMANENT_MARKERS = (
    "550",
    "551",
    "553",
    "5.1.1",
    "5.1.2",
    "5.1.3",
    "recipient rejected",
    "user unknown",
    "no such user",
    "address rejected",
    "does not exist",
)


def _is_permanent(error: Exception) -> bool:
    """Whether this failure means the address will never work, rather than not right now."""
    said = str(error).lower()
    return any(marker in said for marker in _PERMANENT_MARKERS)
