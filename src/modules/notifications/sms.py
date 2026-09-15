"""SMS provider interface and built-in implementations (Issue #67).

SMS is the channel for the messages that must actually arrive (one-time codes, urgent alerts), so
it sits behind a small interface rather than a hard-wired gateway: :class:`SmsProvider`. Two
implementations ship here — :class:`LoggingSmsProvider` (the default; logs and returns a synthetic
id so the app runs end-to-end without an SMS account, the ``SMTP_HOST``-unset analogue for mail)
and :class:`FakeSmsProvider` (an in-memory test double the suite asserts against). Real gateways
(Twilio, Vonage, …) are added as further :class:`SmsProvider` implementations and selected by
``SMS_PROVIDER`` without touching the notification service.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

from src.commons.enums import NotificationChannel, SmsProviderKind
from src.core.config import Settings, get_settings
from src.modules.notifications import dev_outbox

logger = logging.getLogger(__name__)


class SmsSendError(Exception):
    """Raised when an SMS provider fails to accept a message (network, auth, gateway error).

    Treated as transient by the notification service: the message is retried with backoff and
    dead-lettered only once the attempt budget is spent.
    """


class SmsRecipientRejectedError(SmsSendError):
    """The gateway refused the number itself (malformed, not a mobile, barred). Never retried."""


class SmsProvider(ABC):
    """Interface every SMS gateway implementation satisfies.

    One method: hand a message to the gateway and return its provider-side message id (used later
    to correlate a delivery-status webhook). Raise :class:`SmsSendError` on any failure.
    """

    #: Stable provider identifier recorded on the notification row (``notification.provider``).
    kind: SmsProviderKind

    @abstractmethod
    def send(self, *, to: str, text: str, sender: str) -> str:
        """Send one SMS and return the provider's message id.

        Args:
            to: Destination phone number (E.164).
            text: Message body.
            sender: Sender id / from-number configured for the app.

        Returns:
            The provider's id for the accepted message.

        Raises:
            SmsRecipientRejectedError: When the gateway refuses the number itself.
            SmsSendError: When the gateway rejects or fails to accept the message.
        """
        raise NotImplementedError

    def cost_of(self, text: str) -> Decimal:
        """What sending ``text`` costs (Issue 63). Nothing, for a provider that sends nothing."""
        return Decimal("0")


class LoggingSmsProvider(SmsProvider):
    """Default provider: accept the message and return a synthetic id (no gateway account needed).

    Lets the whole notification flow run in development and CI without an SMS account, exactly as
    unset ``SMTP_HOST`` lets email flow no-op. Never raises.

    **It logs that a message was accepted, never what it said or whom it was for** (Issue 17): an
    SMS carries one-time codes, and the number is personal information. In development the message
    goes to the outbox at ``GET /dev/outbox`` (:mod:`src.modules.notifications.dev_outbox`) instead,
    so a developer can still read the code they were sent.
    """

    kind = SmsProviderKind.LOGGING

    def send(self, *, to: str, text: str, sender: str) -> str:
        """Accept the outbound SMS (outbox in development) and return a synthetic message id."""
        message_id = f"log-{uuid4()}"
        in_outbox = dev_outbox.record(NotificationChannel.SMS, to, text)
        logger.info(
            "SMS accepted by the logging provider [%s], %d characters%s",
            message_id,
            len(text),
            " (see /dev/outbox)" if in_outbox else "",
        )
        return message_id


@dataclass
class SentSms:
    """One message captured by :class:`FakeSmsProvider`, for test assertions."""

    to: str
    text: str
    sender: str
    message_id: str


@dataclass
class FakeSmsProvider(SmsProvider):
    """In-memory SMS provider for tests: records every send, or fails on demand.

    Set ``fail_times`` to make the next N sends raise :class:`SmsSendError` (to exercise the
    retry/backoff and dead-letter paths); each failure decrements it. All accepted sends are kept
    in :attr:`sent` for assertions.
    """

    kind: SmsProviderKind = SmsProviderKind.FAKE
    sent: list[SentSms] = field(default_factory=list)
    fail_times: int = 0
    #: Numbers the fake gateway refuses outright, as a real one refuses a malformed number.
    rejected_numbers: frozenset[str] = frozenset()
    #: What each accepted message costs, so cost recording is testable.
    cost_per_message: Decimal = Decimal("0")

    def send(self, *, to: str, text: str, sender: str) -> str:
        """Record the message and return a fake id, or raise while ``fail_times`` remains."""
        if to in self.rejected_numbers:
            raise SmsRecipientRejectedError(f"FakeSmsProvider rejects {to}")
        if self.fail_times > 0:
            self.fail_times -= 1
            raise SmsSendError(f"FakeSmsProvider forced failure to {to}")
        message_id = f"fake-{uuid4()}"
        self.sent.append(
            SentSms(to=to, text=text, sender=sender, message_id=message_id)
        )
        return message_id

    def cost_of(self, text: str) -> Decimal:
        """The configured per-message cost."""
        return self.cost_per_message


def build_sms_provider(settings: Settings | None = None) -> SmsProvider:
    """Return the SMS provider selected by ``SMS_PROVIDER`` settings.

    ``FAKE`` yields a fresh in-memory double (tests usually construct and inject their own instead
    of relying on this); everything else falls back to the logging provider.
    """
    settings = settings or get_settings()
    if settings.sms_provider is SmsProviderKind.FAKE:
        return FakeSmsProvider()
    return LoggingSmsProvider()
