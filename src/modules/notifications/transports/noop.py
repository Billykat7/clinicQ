"""A transport that talks to no provider: the swappable test double (Issue 63).

It can stand in for any channel, reach or not reach a patient, fail a set number of times, fail
permanently, or take a while to answer, and it keeps every message it accepted in :attr:`sent`. With
it a test exercises selection, retries, fallback and post-commit dispatch without a network, a
credential or a real provider.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from threading import Lock
from uuid import uuid4

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


@dataclass(frozen=True, slots=True)
class NoopDelivery:
    """One message a :class:`NoopTransport` accepted."""

    to: str
    text: str
    message_id: str


@dataclass(eq=False)
class NoopTransport(Transport):
    """Accept messages in memory; fail, refuse or stall on demand."""

    channel: NotificationChannel = NotificationChannel.SMS
    free: bool = False
    #: The address it reports for every patient; ``None`` means it cannot reach anyone.
    address: str | None = "noop-address"
    #: Fail this many sends with a retryable error before accepting one.
    fail_times: int = 0
    #: Fail every send permanently (a dead subscription, a malformed number).
    fail_permanently: bool = False
    #: Seconds each send takes, to prove a slow provider cannot hold a queue transition.
    delay_seconds: float = 0.0
    cost: Decimal = FREE
    sent: list[NoopDelivery] = field(default_factory=list)
    attempts: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def address_for(self, patient: PatientAddresses) -> str | None:
        """The configured address, whoever the patient is."""
        return self.address

    def send(self, *, to: str, message: RenderedMessage) -> TransportReceipt:
        """Record the message, or raise the configured failure."""
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        with self._lock:
            self.attempts += 1
            if self.fail_permanently:
                raise PermanentTransportError(f"noop {self.channel.value}: gone")
            if self.fail_times > 0:
                self.fail_times -= 1
                raise TransportError(f"noop {self.channel.value}: provider down")
            delivery = NoopDelivery(
                to=to, text=message.text, message_id=f"noop-{uuid4()}"
            )
            self.sent.append(delivery)
        return TransportReceipt(
            provider=f"noop-{self.channel.value}",
            provider_message_id=delivery.message_id,
            cost=self.cost,
        )
