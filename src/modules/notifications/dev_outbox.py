"""The development outbox: where a message goes when nothing can deliver it (Issue 17).

Without an SMS account or an SMTP server, a developer still has to read the one-time code the app
just sent. It used to be written to the log, which is exactly where a code must never be: logs are
shipped, kept and read by people who are not the recipient. So in development the logging SMS
provider, and the email sign-in's no-SMTP fallback, put the message here instead: an in-memory list
of the last few messages, readable at ``GET /dev/outbox`` (a route that exists only in
development, like the rest of ``/dev``).

Nothing is kept outside development; :func:`record` is a no-op there.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from threading import Lock
from typing import Any

from src.commons.enums import AppEnvironment, NotificationChannel
from src.commons.time import now_sast
from src.core.config import get_settings

#: How many messages the outbox keeps; the oldest drop off.
OUTBOX_SIZE = 50


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    """One message that would have been delivered."""

    channel: NotificationChannel
    to: str
    text: str
    at: datetime


_messages: deque[OutboxMessage] = deque(maxlen=OUTBOX_SIZE)
_lock = Lock()


def record(channel: NotificationChannel, to: str, text: str) -> bool:
    """Keep ``text`` for ``to`` when running in development; return whether it was kept."""
    if get_settings().environment is not AppEnvironment.DEVELOPMENT:
        return False
    with _lock:
        _messages.append(
            OutboxMessage(channel=channel, to=to, text=text, at=now_sast())
        )
    return True


def messages() -> list[dict[str, Any]]:
    """The kept messages, newest first, as JSON-ready dicts."""
    with _lock:
        kept = list(_messages)
    return [
        {
            **asdict(message),
            "channel": message.channel.value,
            "at": message.at.isoformat(),
        }
        for message in reversed(kept)
    ]


def clear() -> None:
    """Empty the outbox (tests)."""
    with _lock:
        _messages.clear()
