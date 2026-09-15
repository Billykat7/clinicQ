"""Which transport serves which channel, and the one seam tests swap them through (Issue 63).

:func:`active_transports` is what the notification service asks. In production it builds the
configured set; inside :func:`use_transports` it returns the set a test supplied, so a test replaces
every provider at once and nothing reaches a network. The override is process-wide rather than a
context variable on purpose: post-commit delivery may run on a worker thread, and it must see the
same transports the test installed.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import Lock
from types import MappingProxyType

from src.commons.enums import NotificationChannel
from src.core.config import Settings, get_settings
from src.modules.notifications.sms import SmsProvider, build_sms_provider
from src.modules.notifications.transports.base import Transport
from src.modules.notifications.transports.sms import SmsTransport
from src.modules.notifications.transports.webpush import WebPushTransport
from src.modules.notifications.transports.whatsapp import WhatsAppTransport
from src.modules.notifications.webpush import VapidSender

type TransportSet = Mapping[NotificationChannel, Transport]

_override: TransportSet | None = None
_override_lock = Lock()


def build_transports(
    settings: Settings | None = None, *, sms_provider: SmsProvider | None = None
) -> TransportSet:
    """The configured transports: web push (when VAPID keys are set), WhatsApp (not yet), and SMS."""
    cfg = settings or get_settings()
    return MappingProxyType(
        {
            NotificationChannel.WEB_PUSH: WebPushTransport(
                VapidSender(cfg) if cfg.web_push_enabled else None
            ),
            NotificationChannel.WHATSAPP: WhatsAppTransport(),
            NotificationChannel.SMS: SmsTransport(
                sms_provider or build_sms_provider(cfg)
            ),
        }
    )


def active_transports(*, sms_provider: SmsProvider | None = None) -> TransportSet:
    """The transports to deliver with now.

    ``sms_provider`` (an explicit caller's choice, such as a test driving the retry sweep) wins over
    everything; then a :func:`use_transports` override; then the configured set.
    """
    if _override is not None and sms_provider is None:
        return _override
    return build_transports(sms_provider=sms_provider)


@contextmanager
def use_transports(*transports: Transport) -> Iterator[TransportSet]:
    """Deliver through ``transports`` (one per channel) until the block ends. For tests."""
    global _override
    chosen = MappingProxyType(
        {transport.channel: transport for transport in transports}
    )
    with _override_lock:
        previous, _override = _override, chosen
    try:
        yield chosen
    finally:
        with _override_lock:
            _override = previous
