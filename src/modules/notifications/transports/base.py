"""The transport adapter interface every patient notification goes out through (Issue 63).

A transport knows two things and nothing else: **whether it can reach a patient** (and at which
address), and **how to hand one message to its provider**. It knows nothing about queues, tickets,
templates, consent or retries; the notification service owns all of that and calls a transport only
after the send-time gate (:func:`src.modules.notifications.preferences.resolve`) has said yes.

Failures come in two kinds, because they call for different next steps:

* :class:`TransportError`: the provider could not take the message *this time* (a timeout, a 5xx).
  The row is retried with backoff on the same transport, or falls back once its budget is spent.
* :class:`PermanentTransportError`: the address will never work (a push subscription the browser
  revoked, a malformed number). Retrying is pointless, so the row ends at once and the next
  transport in the chain is tried.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from src.commons.enums import NotificationChannel
from src.modules.notifications.schemas import RenderedMessage

#: What a free transport costs.
FREE = Decimal("0")


class TransportError(Exception):
    """The provider did not accept the message this time; worth retrying."""


class PermanentTransportError(TransportError):
    """The address can never receive this message; retrying would only repeat the failure."""


@dataclass(frozen=True, slots=True)
class PatientAddresses:
    """Every way the service knows to reach one patient, read once per send.

    ``push_subscription_ids`` is empty until Issue 64 stores subscriptions; ``whatsapp_id`` is set
    once a patient has used WhatsApp (Issue 75).
    """

    patient_id: str
    phone_e164: str | None = None
    whatsapp_id: str | None = None
    push_subscription_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TransportReceipt:
    """What a provider said when it accepted a message."""

    provider: str
    provider_message_id: str
    cost: Decimal = FREE


class Transport(ABC):
    """One way of reaching a patient: web push, WhatsApp, SMS, or a test double."""

    #: The channel recorded on the ledger row.
    channel: NotificationChannel
    #: Whether a message costs nothing to send. The fallback chain tries free transports first.
    free: bool

    @abstractmethod
    def address_for(self, patient: PatientAddresses) -> str | None:
        """The address this transport would send to, or ``None`` when it cannot reach the patient.

        ``None`` covers "the patient has no such address" and "this transport is not configured";
        either way the chain moves on without recording a failure, because nothing was attempted.
        """
        raise NotImplementedError

    @abstractmethod
    def send(self, *, to: str, message: RenderedMessage) -> TransportReceipt:
        """Hand one message to the provider.

        Raises:
            PermanentTransportError: The address will never work.
            TransportError: The provider did not accept it this time.
        """
        raise NotImplementedError
