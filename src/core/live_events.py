"""Live updates over server-sent events: one envelope, one broker, per clinic (Issues 49 and 57).

There was no server-sent events (SSE) code in the kernel. The staff dashboard needs it now (Issue 49)
and the waiting-room board needs it next (Issue 57), so both use this module and speak one format:

.. code-block:: text

    id: 42
    event: queue.updated
    data: {"type": "queue.updated", "site_id": "…", "queue_id": "…", "at": "2026-09-14T09:12:03+02:00"}

The ``event`` names are :class:`~src.commons.enums.LiveEventType`; ``data`` is JSON that always
carries ``type``, ``site_id`` and ``at``. **An event says what changed, never the new state**: a
screen that hears ``queue.updated`` reads the queue again through its own gate, so the stream carries
no patient data and needs no second privacy rule. (The board's stream may add the privacy projection
of Issue 58 to its own events; the envelope stays the same.)

**How an event gets here.** A queue write already calls
:func:`~src.modules.queue.snapshot.on_queue_changed`; that hook queues a
:class:`~src.core.domain_events.QueueChanged` to publish **after the commit**, and a subscriber below
hands it to :data:`broker`. A change that rolls back is never announced.

**The broker is in-process.** The production image runs one process per instance, and every
subscriber of a clinic on that instance hears every event. Several instances behind a balancer would
each hear only their own writes: that is what Issue 57's Redis fan-out adds, behind the same
:meth:`SiteEventBroker.publish`. Until then the dashboard also re-reads the board on a slow timer, so
a missed event costs freshness, never correctness.

**Connections are bounded and cleaned up.** Each clinic has at most :data:`MAX_SUBSCRIBERS_PER_SITE`
open streams; a heartbeat every :data:`HEARTBEAT_SECONDS` both proves the connection is alive to the
screen and lets the server notice a client that has gone, and a subscriber whose queue fills (a
client that stopped reading) is dropped rather than allowed to hold memory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from typing import Any, Final

from src.commons.enums import LiveEventType
from src.commons.time import now_sast
from src.core.domain_events import BoardSettingsChanged, QueueChanged, subscribe

logger = logging.getLogger(__name__)

#: How often a silent stream says it is still alive. Issue 57 asks for 15 seconds, so a screen can
#: notice a dead connection within two missed beats.
HEARTBEAT_SECONDS: Final = 15.0
#: The most open streams one clinic may hold on one instance: every reception PC, room tablet and
#: board screen of a large clinic, with room to spare, and a ceiling on what a leak can cost.
MAX_SUBSCRIBERS_PER_SITE: Final = 100
#: Events a subscriber may have waiting before it is treated as gone. A live screen reads each one in
#: microseconds; one that has 256 unread has stopped reading.
SUBSCRIBER_BACKLOG: Final = 256
#: The media type of a server-sent events response.
EVENT_STREAM_MEDIA_TYPE: Final = "text/event-stream"
#: Response headers every stream sends: never cached, and never buffered by a proxy in front of us.
STREAM_HEADERS: Final = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@dataclass(frozen=True, slots=True)
class LiveEvent:
    """One thing a screen should know, for one clinic."""

    type: LiveEventType
    site_id: str
    at: datetime = field(default_factory=now_sast)
    queue_id: str | None = None
    #: Extra fields for this type (the called number, say). Never patient data.
    extra: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        """The JSON ``data`` of the event: ``type``, ``site_id``, ``at``, and what applies."""
        body: dict[str, Any] = {
            "type": self.type.value,
            "site_id": self.site_id,
            "at": self.at.isoformat(),
        }
        if self.queue_id is not None:
            body["queue_id"] = self.queue_id
        body.update(self.extra)
        return body


def format_event(event: LiveEvent, event_id: int) -> str:
    """One event in the server-sent events wire format, ending with the blank line that sends it."""
    data = json.dumps(event.payload(), separators=(",", ":"), ensure_ascii=False)
    return f"id: {event_id}\nevent: {event.type.value}\ndata: {data}\n\n"


class TooManySubscribersError(RuntimeError):
    """This clinic already has :data:`MAX_SUBSCRIBERS_PER_SITE` open streams on this instance."""


@dataclass(eq=False, slots=True)
class _Subscriber:
    """One open stream: its queue, and the event loop that owns the queue."""

    queue: asyncio.Queue[LiveEvent | None]
    loop: asyncio.AbstractEventLoop
    dropped: bool = False


class SiteEventBroker:
    """Fans events out to every open stream of a clinic, from any thread.

    Queue writes happen in FastAPI's worker threads (the routes are synchronous) while streams live
    on the event loop, so :meth:`publish` hands each event to a subscriber's loop with
    ``call_soon_threadsafe`` and never touches an ``asyncio.Queue`` from the wrong thread.
    """

    def __init__(
        self,
        *,
        max_per_site: int = MAX_SUBSCRIBERS_PER_SITE,
        backlog: int = SUBSCRIBER_BACKLOG,
    ) -> None:
        self._max_per_site = max_per_site
        self._backlog = backlog
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[_Subscriber]] = {}
        self._ids = count(1)

    def subscriber_count(self, site_id: str | None = None) -> int:
        """Open streams for one clinic, or for every clinic when ``site_id`` is ``None``."""
        with self._lock:
            if site_id is not None:
                return len(self._subscribers.get(site_id, ()))
            return sum(len(group) for group in self._subscribers.values())

    def subscribe(self, site_id: str) -> _Subscriber:
        """Open a subscription for ``site_id`` on the running loop.

        Raises:
            TooManySubscribersError: The clinic is at :data:`MAX_SUBSCRIBERS_PER_SITE`.
        """
        subscriber = _Subscriber(
            queue=asyncio.Queue(maxsize=self._backlog), loop=asyncio.get_running_loop()
        )
        with self._lock:
            group = self._subscribers.setdefault(site_id, set())
            if len(group) >= self._max_per_site:
                raise TooManySubscribersError(site_id)
            group.add(subscriber)
        return subscriber

    def unsubscribe(self, site_id: str, subscriber: _Subscriber) -> None:
        """Close a subscription. Safe to call twice."""
        with self._lock:
            group = self._subscribers.get(site_id)
            if group is None:
                return
            group.discard(subscriber)
            if not group:
                del self._subscribers[site_id]

    def publish(self, event: LiveEvent) -> None:
        """Hand ``event`` to every open stream of its clinic. Never raises, never blocks."""
        with self._lock:
            targets = tuple(self._subscribers.get(event.site_id, ()))
        for subscriber in targets:
            try:
                subscriber.loop.call_soon_threadsafe(
                    self._offer, event.site_id, subscriber, event
                )
            except RuntimeError:  # the loop has closed: the stream is gone
                self.unsubscribe(event.site_id, subscriber)

    def _offer(self, site_id: str, subscriber: _Subscriber, event: LiveEvent) -> None:
        """Queue ``event`` for one subscriber, on its own loop; drop a subscriber that stopped reading."""
        try:
            subscriber.queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "A live stream for site %s stopped reading; closing it.", site_id
            )
            subscriber.dropped = True
            self.unsubscribe(site_id, subscriber)
            # Wake the stream so it ends now rather than at its next heartbeat.
            subscriber.queue.get_nowait()
            subscriber.queue.put_nowait(None)

    def next_id(self) -> int:
        """A new, increasing event id for this process."""
        return next(self._ids)

    async def stream(
        self,
        site_id: str,
        *,
        is_disconnected: Callable[[], Awaitable[bool]],
        still_allowed: Callable[[], Awaitable[bool]] | None = None,
        accept: Callable[[LiveEvent], bool] | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> AsyncIterator[str]:
        """The server-sent events of one clinic, until the client goes or loses its access.

        Args:
            site_id: The clinic whose events to send.
            is_disconnected: The request's disconnect check, asked at every beat.
            still_allowed: Asked at every beat: a stream whose caller lost their access (an
                assignment removed, an account switched off) ends at the next heartbeat rather than
                running until the browser closes.
            accept: Asked of every event before it is sent; one it refuses is skipped. A room's
                stream (Issue 50) takes only its own queues' events.
            heartbeat_seconds: How long a silent stream waits before saying it is alive.

        Yields:
            Formatted events, starting with a heartbeat so the client knows the stream is open.

        Raises:
            TooManySubscribersError: The clinic is at its limit (raised before anything is sent).
        """
        subscriber = self.subscribe(site_id)
        try:
            yield format_event(
                LiveEvent(LiveEventType.HEARTBEAT, site_id), self.next_id()
            )
            loop = asyncio.get_running_loop()
            last_sent = loop.time()
            while True:
                # The beat is due a heartbeat after the last thing *sent*: events a filtered stream
                # skips must not keep it silent past the client's grace period.
                wait = max(0.0, heartbeat_seconds - (loop.time() - last_sent))
                try:
                    event = await asyncio.wait_for(subscriber.queue.get(), wait)
                except TimeoutError:
                    if await is_disconnected() or (
                        still_allowed is not None and not await still_allowed()
                    ):
                        return
                    event = LiveEvent(LiveEventType.HEARTBEAT, site_id)
                if event is None or subscriber.dropped:
                    return
                if accept is not None and not accept(event):
                    continue
                last_sent = loop.time()
                yield format_event(event, self.next_id())
        finally:
            self.unsubscribe(site_id, subscriber)


#: The process's broker: every stream subscribes to it, every committed change is published to it.
broker = SiteEventBroker()


@subscribe(QueueChanged)
def _queue_changed(event: QueueChanged) -> None:
    """A committed queue change becomes ``ticket.called`` (with the number) or ``queue.updated``."""
    if event.called_number is not None:
        broker.publish(
            LiveEvent(
                LiveEventType.TICKET_CALLED,
                event.site_id,
                queue_id=event.queue_id,
                extra={"number": event.called_number},
            )
        )
        return
    broker.publish(
        LiveEvent(LiveEventType.QUEUE_UPDATED, event.site_id, queue_id=event.queue_id)
    )


@subscribe(BoardSettingsChanged)
def _board_settings_changed(event: BoardSettingsChanged) -> None:
    """A committed display-settings change becomes ``board.config_changed``."""
    broker.publish(LiveEvent(LiveEventType.BOARD_CONFIG_CHANGED, event.site_id))
