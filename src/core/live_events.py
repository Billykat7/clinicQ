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

**One broker per process, joined by Redis.** Every subscriber of a clinic on an instance hears every
event published on that instance. With ``REDIS_URL`` set, :func:`publish_live` also hands each event to
:class:`RedisFanout` (Issue 57), which publishes it on :data:`FANOUT_CHANNEL`; every other instance's
fan-out hears it there and gives it to its own broker, so a call made through one worker reaches boards
connected to another. An instance ignores its own messages (it delivered them already), and a Redis
outage is logged once and costs other instances' screens freshness, never this one's: the dashboard
re-reads its cards on a slow timer and a board resyncs on every reconnection.

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
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from itertools import count
from typing import Any, Final

from prometheus_client import Gauge

from src.commons.enums import LiveEventType
from src.commons.time import now_sast
from src.core.config import Settings
from src.core.domain_events import BoardSettingsChanged, QueueChanged, subscribe

logger = logging.getLogger(__name__)

#: How often a silent stream says it is still alive. Issue 57 asks for 15 seconds, so a screen can
#: notice a dead connection within two missed beats.
HEARTBEAT_SECONDS: Final = 15.0
#: The staff dashboard's beat (Issue 55). A reception PC must say "Offline" within 10 seconds of losing
#: the network, and a page can only tell a quiet stream from a dead one by the beat, so staff streams beat
#: every 5 seconds. A beat costs a few bytes and no database read: access is re-checked on its own clock.
STAFF_HEARTBEAT_SECONDS: Final = 5.0
#: How often a stream re-checks that its caller may still read it: a database read, so not every beat.
ACCESS_CHECK_SECONDS: Final = 15.0
#: The most open streams one clinic may hold on one instance: every reception PC, room tablet and
#: board screen of a large clinic, with room to spare, and a ceiling on what a leak can cost.
MAX_SUBSCRIBERS_PER_SITE: Final = 100
#: Events a subscriber may have waiting before it is treated as gone. A live screen reads each one in
#: microseconds; one that has 256 unread has stopped reading.
SUBSCRIBER_BACKLOG: Final = 256
#: Patients following their own tickets (Issue 68) have a budget of their own, far larger than the
#: screens', so a full waiting room of phones can never take a stream from the clinic's board.
MAX_PATIENT_STREAMS_PER_SITE: Final = 500
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
    #: When this process learned of the event, on its monotonic clock: lets a board stream reuse a board
    #: it projected after the event rather than project it again (Issue 57). Not sent.
    stamp: float = field(default_factory=time.monotonic, compare=False)

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

    @classmethod
    def from_payload(cls, body: dict[str, Any]) -> LiveEvent:
        """The event another instance published (:meth:`payload` read back).

        Raises:
            ValueError: ``body`` is not an event's payload.
            KeyError: A required field is missing.
        """
        extra = {
            key: value
            for key, value in body.items()
            if key not in {"type", "site_id", "at", "queue_id"}
        }
        return cls(
            type=LiveEventType(body["type"]),
            site_id=str(body["site_id"]),
            at=datetime.fromisoformat(body["at"]),
            queue_id=body.get("queue_id"),
            extra=extra,
        )


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

    async def events(
        self,
        site_id: str,
        *,
        is_disconnected: Callable[[], Awaitable[bool]],
        still_allowed: Callable[[], Awaitable[bool]] | None = None,
        accept: Callable[[LiveEvent], bool] | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        access_check_seconds: float = ACCESS_CHECK_SECONDS,
    ) -> AsyncGenerator[LiveEvent]:
        """The events of one clinic, as objects, until the client goes or loses its access.

        What :meth:`stream` formats. A stream that sends something other than the event itself (the
        waiting-room board's, Issue 57, which sends the board as it now is) reads these instead.

        Args:
            site_id: The clinic whose events to send.
            is_disconnected: The request's disconnect check, asked at every beat.
            still_allowed: Asked at a beat once ``access_check_seconds`` have passed since it was last
                asked: a stream whose caller lost their access (an assignment removed, an account
                switched off) ends then rather than running until the browser closes.
            accept: Asked of every event before it is sent; one it refuses is skipped. A room's
                stream (Issue 50) takes only its own queues' events.
            heartbeat_seconds: How long a silent stream waits before saying it is alive.
            access_check_seconds: How long between two asks of ``still_allowed``.

        Yields:
            Events, starting with a heartbeat so the client knows the stream is open.

        Raises:
            TooManySubscribersError: The clinic is at its limit (raised before anything is sent).
        """
        subscriber = self.subscribe(site_id)
        try:
            yield LiveEvent(LiveEventType.HEARTBEAT, site_id)
            loop = asyncio.get_running_loop()
            last_sent = last_checked = loop.time()
            while True:
                # The beat is due a heartbeat after the last thing *sent*: events a filtered stream
                # skips must not keep it silent past the client's grace period.
                wait = max(0.0, heartbeat_seconds - (loop.time() - last_sent))
                try:
                    event = await asyncio.wait_for(subscriber.queue.get(), wait)
                except TimeoutError:
                    if await is_disconnected():
                        return
                    if (
                        still_allowed is not None
                        and loop.time() - last_checked >= access_check_seconds
                    ):
                        last_checked = loop.time()
                        if not await still_allowed():
                            return
                    event = LiveEvent(LiveEventType.HEARTBEAT, site_id)
                if event is None or subscriber.dropped:
                    return
                if accept is not None and not accept(event):
                    continue
                last_sent = loop.time()
                yield event
        finally:
            self.unsubscribe(site_id, subscriber)

    async def stream(
        self,
        site_id: str,
        *,
        is_disconnected: Callable[[], Awaitable[bool]],
        still_allowed: Callable[[], Awaitable[bool]] | None = None,
        accept: Callable[[LiveEvent], bool] | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        access_check_seconds: float = ACCESS_CHECK_SECONDS,
    ) -> AsyncIterator[str]:
        """The server-sent events of one clinic, formatted: :meth:`events` on the wire.

        Takes the same arguments as :meth:`events`, and raises the same
        :class:`TooManySubscribersError` before anything is sent.
        """
        events = self.events(
            site_id,
            is_disconnected=is_disconnected,
            still_allowed=still_allowed,
            accept=accept,
            heartbeat_seconds=heartbeat_seconds,
            access_check_seconds=access_check_seconds,
        )
        try:
            async for event in events:
                yield format_event(event, self.next_id())
        finally:
            await events.aclose()


#: The process's broker: every staff and board stream subscribes to it, every committed change is
#: published to it.
broker = SiteEventBroker()
#: The broker patients' ticket pages subscribe to (Issue 68). Every change goes to both brokers; they
#: differ only in whose streams count against which budget.
patient_broker = SiteEventBroker(max_per_site=MAX_PATIENT_STREAMS_PER_SITE)


class _LocalStreams:
    """Both of this instance's brokers, as one place to hand an event (for the Redis fan-out)."""

    def publish(self, event: LiveEvent) -> None:
        """Hand ``event`` to every stream on this instance."""
        broker.publish(event)
        patient_broker.publish(event)


#: Every stream on this instance: staff, boards and patients.
local_streams = _LocalStreams()

#: How many streams are open on this instance, every clinic and every kind: what an operator watches to
#: see that connections do not pile up over a day (Issue 57). On ``/metrics``.
STREAMS_OPEN = Gauge(
    "clinicq_live_streams_open",
    "Open live-event streams (dashboards, waiting-room boards and ticket pages) on this instance.",
)
STREAMS_OPEN.set_function(
    lambda: broker.subscriber_count() + patient_broker.subscriber_count()
)

# --------------------------------------------------------------------------------------
# Fan-out across instances (Issue 57)
# --------------------------------------------------------------------------------------

#: The Redis pub/sub channel every instance publishes its clinics' events on.
FANOUT_CHANNEL: Final = "clinicq:live-events"
#: How long the listener waits before subscribing again after Redis failed.
FANOUT_RETRY_SECONDS: Final = 5.0


class RedisFanout:
    """Hands this instance's events to the others through Redis pub/sub, and theirs to its broker.

    ``publish`` never raises and never blocks for long: a failed publish is logged once per outage, and
    this instance's own screens already have the event. A daemon thread listens on the channel and
    re-subscribes after a failure. Messages carry the publishing instance's ``origin`` so an instance
    skips its own.
    """

    def __init__(
        self,
        client: Any,
        target: SiteEventBroker | _LocalStreams,
        *,
        channel: str = FANOUT_CHANNEL,
        origin: str | None = None,
        retry_seconds: float = FANOUT_RETRY_SECONDS,
    ) -> None:
        """Wrap a ``redis.Redis``-shaped client; events heard on ``channel`` go to ``target``."""
        self._client = client
        self._target = target
        self._channel = channel
        self.origin = origin or uuid.uuid4().hex
        self._retry_seconds = retry_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._degraded = False
        self._lock = threading.Lock()

    def publish(self, event: LiveEvent) -> None:
        """Publish ``event`` for the other instances. Never raises."""
        message = json.dumps(
            {"origin": self.origin, "event": event.payload()},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        try:
            self._client.publish(self._channel, message)
        except (
            Exception
        ) as exc:  # any transport failure: this instance already delivered it
            self._failed(exc)
            return
        self._recovered()

    def deliver(self, raw: str | bytes) -> bool:
        """Give one message from the channel to the broker; ``False`` when it was ours or unreadable."""
        try:
            body = json.loads(raw)
            if body.get("origin") == self.origin:
                return False
            event = LiveEvent.from_payload(body["event"])
        except ValueError, KeyError, TypeError, AttributeError:
            logger.warning(
                "Ignored an unreadable live-event message on %s.", self._channel
            )
            return False
        self._target.publish(event)
        return True

    def start(self) -> None:
        """Start listening, on a daemon thread. Safe to call twice."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._listen, name="live-events-fanout", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop listening and wait up to ``timeout`` seconds for the thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    @property
    def listening(self) -> bool:
        """Whether the listener thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def _listen(self) -> None:
        """Subscribe and relay until stopped; after a failure, wait and subscribe again."""
        while not self._stop.is_set():
            pubsub = None
            try:
                pubsub = self._client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(self._channel)
                self._recovered()
                while not self._stop.is_set():
                    message = pubsub.get_message(timeout=1.0)
                    if message and message.get("type") == "message":
                        self.deliver(message["data"])
            except Exception as exc:
                self._failed(exc)
                self._stop.wait(self._retry_seconds)
            finally:
                if pubsub is not None:
                    try:
                        pubsub.close()
                    except Exception:  # closing a broken connection may fail too
                        logger.debug("Closing the live-event subscription failed.")

    def _failed(self, exc: Exception) -> None:
        with self._lock:
            if self._degraded:
                return
            self._degraded = True
        logger.warning(
            "Live events cannot reach Redis (%s: %s): screens on other instances will hear this "
            "instance's changes only when it comes back.",
            type(exc).__name__,
            exc,
        )

    def _recovered(self) -> None:
        with self._lock:
            if not self._degraded:
                return
            self._degraded = False
        logger.info("Live events reach Redis again.")


_fanout: RedisFanout | None = None


def start_fanout(settings: Settings) -> RedisFanout | None:
    """Start the fan-out the settings ask for (``REDIS_URL`` and ``LIVE_EVENTS_FANOUT``), or none."""
    global _fanout
    if _fanout is not None or not (settings.redis_url and settings.live_events_fanout):
        return _fanout
    try:
        import redis  # lazily, like the snapshot cache: a deployment without Redis needs no driver
    except ImportError:
        logger.warning(
            "REDIS_URL is set but the redis package is missing; no live-event fan-out."
        )
        return None
    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.redis_probe_timeout_seconds,
            health_check_interval=30,
            decode_responses=True,
        )
    except ValueError as exc:
        logger.warning(
            "REDIS_URL is not a usable Redis URL (%s); no live-event fan-out.", exc
        )
        return None
    _fanout = RedisFanout(client, local_streams)
    _fanout.start()
    return _fanout


def stop_fanout() -> None:
    """Stop the fan-out, if one is running."""
    global _fanout
    if _fanout is not None:
        _fanout.stop()
        _fanout = None


def publish_live(event: LiveEvent) -> None:
    """Publish ``event`` to this instance's streams, and to the other instances' through Redis."""
    local_streams.publish(event)
    if _fanout is not None:
        _fanout.publish(event)


@subscribe(QueueChanged)
def _queue_changed(event: QueueChanged) -> None:
    """A committed queue change becomes ``ticket.called`` (with the number) or ``queue.updated``."""
    if event.called_number is not None:
        publish_live(
            LiveEvent(
                LiveEventType.TICKET_CALLED,
                event.site_id,
                queue_id=event.queue_id,
                extra={"number": event.called_number},
            )
        )
        return
    publish_live(
        LiveEvent(LiveEventType.QUEUE_UPDATED, event.site_id, queue_id=event.queue_id)
    )


@subscribe(BoardSettingsChanged)
def _board_settings_changed(event: BoardSettingsChanged) -> None:
    """A committed display-settings change becomes ``board.config_changed``."""
    publish_live(LiveEvent(LiveEventType.BOARD_CONFIG_CHANGED, event.site_id))
