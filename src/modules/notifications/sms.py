"""SMS provider interface and built-in implementations (Issues 67, 65).

SMS is the channel for the messages that must actually arrive (one-time codes, urgent alerts), so
it sits behind a small interface rather than a hard-wired gateway: :class:`SmsProvider`. Three
implementations ship here:

* :class:`LoggingSmsProvider` (the default): logs and returns a synthetic id so the app runs end-to-end
  without an SMS account, the ``SMTP_HOST``-unset analogue for mail;
* :class:`FakeSmsProvider`: an in-memory test double the suite asserts against;
* :class:`AfricasTalkingSmsProvider` (Issue 65): the gateway, sandbox by default, reporting what each
  message cost.

Another gateway is one more :class:`SmsProvider`, selected by ``SMS_PROVIDER``, with no change to the
notification service.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final
from uuid import uuid4

import httpx

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


@dataclass(frozen=True, slots=True)
class SmsReceipt:
    """What a gateway said when it accepted a message: its id, and what it charged."""

    message_id: str
    cost: Decimal = Decimal("0")
    currency: str | None = None
    """The currency the gateway charged in, when it said; ``None`` means ``NOTIFICATION_COST_CURRENCY``."""


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

    def send_message(self, *, to: str, text: str, sender: str) -> SmsReceipt:
        """Send one SMS and return what the gateway said: its id and, when it reports one, its cost.

        The notification service calls this. The default sends with :meth:`send` and prices the
        message with :meth:`cost_of`; a gateway that reports the real charge overrides it (Issue 65).
        """
        return SmsReceipt(
            message_id=self.send(to=to, text=text, sender=sender),
            cost=self.cost_of(text),
        )


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


class AfricasTalkingSmsProvider(SmsProvider):
    """An Africa's Talking-class SMS gateway (Issue 65): one HTTPS POST per message, cost in the answer.

    ``POST {base}/version1/messaging`` with the account's ``username``, the number, the text and the
    sender id, authenticated by the ``apiKey`` header. The answer lists each recipient with a status
    code, the gateway's message id and what it cost (``"ZAR 0.2500"``).

    **Sandbox** (``SMS_SANDBOX=true``, the default) sends to the gateway's sandbox with the ``sandbox``
    username: messages reach its simulator, nothing reaches a phone and nothing is charged, so the whole
    flow, receipts included, runs in development with no spend.

    How an answer is treated:

    * ``100``/``101``/``102`` (processed, sent, queued): accepted.
    * ``403``/``404``/``406``/``409`` (invalid number, unsupported number type, the number opted out at
      the gateway, do-not-disturb): :class:`SmsRecipientRejectedError`, never retried.
    * anything else (``401`` risk hold, ``402`` bad sender id, ``405`` insufficient balance, ``5xx``), a
      timeout or an HTTP error: :class:`SmsSendError`, retried with backoff.
    """

    kind = SmsProviderKind.AFRICAS_TALKING
    LIVE_URL: Final = "https://api.africastalking.com"
    SANDBOX_URL: Final = "https://api.sandbox.africastalking.com"
    SANDBOX_USERNAME: Final = "sandbox"
    ACCEPTED: Final = frozenset({100, 101, 102})
    RECIPIENT_REJECTED: Final = frozenset({403, 404, 406, 409})

    def __init__(
        self, settings: Settings | None = None, *, client: httpx.Client | None = None
    ) -> None:
        """Use the configured account; ``client`` lets a test answer for the gateway."""
        self.settings = settings or get_settings()
        self._client = client or httpx.Client(timeout=10.0)

    @property
    def base_url(self) -> str:
        """The sandbox or the live API."""
        return self.SANDBOX_URL if self.settings.sms_sandbox else self.LIVE_URL

    @property
    def username(self) -> str:
        """The sandbox always sends as ``sandbox``, whatever the live username is."""
        return (
            self.SANDBOX_USERNAME
            if self.settings.sms_sandbox
            else self.settings.africas_talking_username
        )

    def send(self, *, to: str, text: str, sender: str) -> str:
        """Send and return the gateway's message id."""
        return self.send_message(to=to, text=text, sender=sender).message_id

    def send_message(self, *, to: str, text: str, sender: str) -> SmsReceipt:
        """Send one SMS; return its id and the charge the gateway reported."""
        form = {"username": self.username, "to": to, "message": text}
        if sender and not self.settings.sms_sandbox:
            form["from"] = sender  # the sandbox only accepts its own sender ids
        try:
            response = self._client.post(
                f"{self.base_url}/version1/messaging",
                data=form,
                headers={
                    "apiKey": self.settings.africas_talking_api_key,
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise SmsSendError(f"gateway unreachable: {type(exc).__name__}") from exc
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise SmsSendError(f"gateway answered HTTP {response.status_code}")
        try:
            recipient = response.json()["SMSMessageData"]["Recipients"][0]
            code = int(recipient["statusCode"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise SmsSendError("gateway answered in an unexpected shape") from exc
        if code in self.RECIPIENT_REJECTED:
            raise SmsRecipientRejectedError(
                f"gateway refused the number: {recipient.get('status', code)}"
            )
        if code not in self.ACCEPTED:
            raise SmsSendError(
                f"gateway did not accept the message: {recipient.get('status', code)}"
            )
        currency, cost = parse_cost(str(recipient.get("cost", "")))
        return SmsReceipt(
            message_id=str(recipient["messageId"]), cost=cost, currency=currency
        )


def parse_cost(value: str) -> tuple[str | None, Decimal]:
    """``"ZAR 0.2500"`` as ``("ZAR", Decimal("0.2500"))``; anything unreadable is ``(None, 0)``."""
    currency, _, amount = value.strip().partition(" ")
    try:
        return (currency.upper() or None), Decimal(amount)
    except InvalidOperation:
        return None, Decimal("0")


def build_sms_provider(settings: Settings | None = None) -> SmsProvider:
    """Return the SMS provider selected by ``SMS_PROVIDER`` settings.

    ``FAKE`` yields a fresh in-memory double (tests usually construct and inject their own instead
    of relying on this); ``AFRICAS_TALKING`` the gateway (Issue 65); everything else the logging
    provider.
    """
    settings = settings or get_settings()
    if settings.sms_provider is SmsProviderKind.FAKE:
        return FakeSmsProvider()
    if settings.sms_provider is SmsProviderKind.AFRICAS_TALKING:
        return AfricasTalkingSmsProvider(settings)
    return LoggingSmsProvider()
