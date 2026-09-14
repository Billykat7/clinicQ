"""The live-events envelope and broker (Issues 49 and 57): format, fan-out, heartbeat and clean-up.

Essential rules only (``docs/IDE/RULES/testing-strategy.mdc``): the wire format both streams share,
that a clinic's events reach its own streams and nobody else's, that a silent stream sends a
heartbeat, and that a stream which has gone, lost its access or stopped reading does not linger.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from src.commons.enums import LiveEventType
from src.commons.time import APP_TIMEZONE
from src.core.live_events import (
    LiveEvent,
    SiteEventBroker,
    TooManySubscribersError,
    format_event,
)

_AT = datetime(2026, 9, 14, 9, 12, 3, tzinfo=APP_TIMEZONE)


def _parse(chunk: str) -> tuple[str, dict]:
    """The event name and JSON data of one formatted event."""
    lines = dict(line.split(": ", 1) for line in chunk.strip().splitlines())
    return lines["event"], json.loads(lines["data"])


async def _never() -> bool:
    """A client that never disconnects."""
    return False


def test_an_event_is_one_sse_message_with_the_shared_envelope() -> None:
    """``id``, ``event`` and a JSON ``data`` line, ended by the blank line that sends it."""
    event = LiveEvent(
        LiveEventType.TICKET_CALLED,
        "site-a",
        at=_AT,
        queue_id="q-1",
        extra={"number": "T004"},
    )
    assert format_event(event, 7) == (
        "id: 7\nevent: ticket.called\n"
        'data: {"type":"ticket.called","site_id":"site-a","at":"2026-09-14T09:12:03+02:00",'
        '"queue_id":"q-1","number":"T004"}\n\n'
    )


def test_a_clinics_events_reach_its_streams_and_no_other_clinics() -> None:
    """Clinic A's stream hears A's change; clinic B's change never reaches it."""

    async def scenario() -> list[tuple[str, dict]]:
        broker = SiteEventBroker()
        stream = broker.stream("site-a", is_disconnected=_never, heartbeat_seconds=5)
        received = [_parse(await anext(stream))]  # the opening heartbeat
        broker.publish(LiveEvent(LiveEventType.QUEUE_UPDATED, "site-b", queue_id="b1"))
        broker.publish(LiveEvent(LiveEventType.QUEUE_UPDATED, "site-a", queue_id="a1"))
        received.append(_parse(await asyncio.wait_for(anext(stream), 1)))
        await stream.aclose()
        assert broker.subscriber_count() == 0
        return received

    (opening, first), (changed, data) = asyncio.run(scenario())
    assert opening == "heartbeat" and first["site_id"] == "site-a"
    assert changed == "queue.updated" and data["queue_id"] == "a1"


def test_publishing_from_another_thread_reaches_the_stream() -> None:
    """Queue writes run in worker threads; the event still lands on the stream's own loop."""

    async def scenario() -> str:
        broker = SiteEventBroker()
        stream = broker.stream("site-a", is_disconnected=_never, heartbeat_seconds=5)
        await anext(stream)
        await asyncio.to_thread(
            broker.publish, LiveEvent(LiveEventType.BOARD_CONFIG_CHANGED, "site-a")
        )
        name, _ = _parse(await asyncio.wait_for(anext(stream), 1))
        await stream.aclose()
        return name

    assert asyncio.run(scenario()) == "board.config_changed"


def test_a_silent_stream_sends_a_heartbeat_and_ends_when_the_client_goes() -> None:
    """After the interval, a heartbeat; once the client has disconnected, the stream ends and unsubscribes."""

    async def scenario() -> tuple[str, int]:
        broker = SiteEventBroker()
        gone = False

        async def disconnected() -> bool:
            return gone

        stream = broker.stream(
            "site-a", is_disconnected=disconnected, heartbeat_seconds=0.05
        )
        await anext(stream)
        name, _ = _parse(await asyncio.wait_for(anext(stream), 1))
        gone = True
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 1)
        return name, broker.subscriber_count("site-a")

    assert asyncio.run(scenario()) == ("heartbeat", 0)


def test_a_filtered_stream_skips_other_queues_and_still_beats_on_time() -> None:
    """A room's stream (Issue 50): only its queue's events, and a heartbeat even while others are busy."""

    async def scenario() -> list[tuple[str, dict]]:
        broker = SiteEventBroker()
        stream = broker.stream(
            "site-a",
            is_disconnected=_never,
            accept=lambda event: event.queue_id in {None, "mine"},
            heartbeat_seconds=0.2,
        )
        await anext(stream)
        received: list[tuple[str, dict]] = []

        async def busy_elsewhere() -> None:
            # Another room's events every 50 ms: skipped, and never enough to hold the beat back.
            for _ in range(10):
                broker.publish(
                    LiveEvent(LiveEventType.QUEUE_UPDATED, "site-a", queue_id="theirs")
                )
                await asyncio.sleep(0.05)
            broker.publish(
                LiveEvent(LiveEventType.QUEUE_UPDATED, "site-a", queue_id="mine")
            )

        publisher = asyncio.create_task(busy_elsewhere())
        while len(received) < 3:
            received.append(_parse(await asyncio.wait_for(anext(stream), 1)))
            if received[-1][1].get("queue_id") == "mine":
                break
        await publisher
        await stream.aclose()
        return received

    received = asyncio.run(scenario())
    assert all(data.get("queue_id") != "theirs" for _, data in received)
    assert received[0][0] == "heartbeat"
    assert received[-1][1]["queue_id"] == "mine"


def test_a_stream_whose_caller_lost_access_ends_at_the_next_beat() -> None:
    """An assignment removed mid-shift closes the stream rather than leaving it open all day."""

    async def scenario() -> int:
        broker = SiteEventBroker()

        async def revoked() -> bool:
            return False

        stream = broker.stream(
            "site-a",
            is_disconnected=_never,
            still_allowed=revoked,
            heartbeat_seconds=0.05,
        )
        await anext(stream)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 1)
        return broker.subscriber_count()

    assert asyncio.run(scenario()) == 0


def test_a_clinic_cannot_open_more_streams_than_the_limit() -> None:
    """The limit is refused before anything is sent, and a closed stream frees its place."""

    async def scenario() -> None:
        broker = SiteEventBroker(max_per_site=2)
        streams = [broker.stream("site-a", is_disconnected=_never) for _ in range(3)]
        await anext(streams[0])
        await anext(streams[1])
        with pytest.raises(TooManySubscribersError):
            await anext(streams[2])
        await streams[0].aclose()
        third = broker.stream("site-a", is_disconnected=_never)
        await anext(third)
        assert broker.subscriber_count("site-a") == 2
        for stream in (streams[1], third):
            await stream.aclose()

    asyncio.run(scenario())


def test_a_stream_that_stopped_reading_is_dropped_not_kept() -> None:
    """A full backlog means the client is gone: the subscriber is removed and its stream ends."""

    async def scenario() -> int:
        broker = SiteEventBroker(backlog=2)
        stream = broker.stream("site-a", is_disconnected=_never, heartbeat_seconds=5)
        await anext(stream)
        for n in range(5):
            broker.publish(
                LiveEvent(LiveEventType.QUEUE_UPDATED, "site-a", queue_id=str(n))
            )
        await asyncio.sleep(0.05)  # let the loop run the queued hand-offs
        assert broker.subscriber_count("site-a") == 0
        with pytest.raises(StopAsyncIteration):
            while True:
                await asyncio.wait_for(anext(stream), 1)
        return broker.subscriber_count()

    assert asyncio.run(scenario()) == 0
