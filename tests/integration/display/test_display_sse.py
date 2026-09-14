"""The waiting-room board's live stream over HTTP, and what feeds it (Issue 57).

What this file proves, reading events and JSON, never HTML:

* a stream opens with ``board.state``, the whole projected board, and sends each change with the board
  as it is after that change: a full resync on every connection;
* it is gated like the board: a clinic with no public board is 404, and a clinic with too many streams
  gets 503 with ``Retry-After``;
* a consent answer about the board is announced to the patient's queues, so a withdrawal reaches the
  screen on the next event, not the next poll;
* every screen of a clinic shares one projection per change;
* dead connections are cleaned up: over a simulated eight-hour day of boards dropping and reconnecting,
  the broker holds exactly the streams still open, and none at the end;
* with Redis, a change published by one instance reaches the streams of another.

The streams in the first tests are endless in production. Here the broker's event source is replaced by
a finite script of events, so the response ends and can be read whole; the route, the projection and
the formatting are the real ones. The browser half (the board reconnecting, the watchdog) is in
``tests/e2e/display/test_board_live.py``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from starlette import status

from src.commons.enums import (
    ActorKind,
    ConsentPurpose,
    DisplayMode,
    LiveEventType,
    TicketStatus,
)
from src.core import live_events
from src.core.live_events import (
    EVENT_STREAM_MEDIA_TYPE,
    LiveEvent,
    RedisFanout,
    SiteEventBroker,
    TooManySubscribersError,
)
from src.database.models import Queue
from src.modules.display import board_state
from src.modules.display.board_state import BoardProjections
from src.modules.display.enums import BoardViewer
from src.modules.queue.lifecycle import Actor, call_next
from tests.integration.display.conftest import NOMVULA

_DESK = Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example")


def parse_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    """``[(event name, data), …]`` from a server-sent events body."""
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if ": " in line
        )
        events.append((fields["event"], json.loads(fields["data"])))
    return events


@contextmanager
def scripted_stream(
    monkeypatch: pytest.MonkeyPatch, script: Callable[[str], list[LiveEvent]]
) -> Iterator[None]:
    """Replace the broker's endless events with a heartbeat followed by ``script(site_id)``."""

    async def events(site_id: str, **_: object) -> AsyncIterator[LiveEvent]:
        yield LiveEvent(LiveEventType.HEARTBEAT, site_id)
        for event in script(site_id):
            yield event

    with monkeypatch.context() as patch:
        patch.setattr(live_events.broker, "events", events)
        yield


def test_a_stream_opens_with_the_whole_board_and_sends_each_change_with_the_board(
    board: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``board.state`` first, then ``ticket.called`` carrying the board after the call; heartbeats bare."""
    triage = board.world.triage
    first = board.ticket(triage)
    second = board.ticket(triage)

    def script(site_id: str) -> list[LiveEvent]:
        # Called while the stream is open: the change the next event announces.
        with board.session() as db:
            call_next(db, db.get_one(Queue, triage), actor=_DESK)
            db.commit()
        return [
            LiveEvent(LiveEventType.TICKET_CALLED, site_id, queue_id=triage),
            LiveEvent(LiveEventType.HEARTBEAT, site_id),
        ]

    with scripted_stream(monkeypatch, script):
        response = board.world.anonymous().get(f"/display/{board.world.site_a}/stream")
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith(EVENT_STREAM_MEDIA_TYPE)
    assert response.headers["cache-control"] == "no-cache, no-transform"

    events = parse_events(response.text)
    assert [name for name, _ in events] == ["board.state", "ticket.called", "heartbeat"]
    (_, opened), (_, called), (_, beat) = events
    assert set(opened) == {"type", "site_id", "at", "board"}
    serving = next(q for q in opened["board"]["queues"] if q["id"] == triage)
    assert serving["now_serving"] == [] and [
        t["number"] for t in serving["up_next"]
    ] == [
        first,
        second,
    ]
    after = next(q for q in called["board"]["queues"] if q["id"] == triage)
    assert [t["number"] for t in after["now_serving"]] == [first]
    assert after["now_serving"][0]["status"] == TicketStatus.CALLED.value
    assert called["queue_id"] == triage
    # A heartbeat carries no board, and nothing but the envelope and the board travels.
    assert "board" not in beat
    assert set(called) == {"type", "site_id", "at", "queue_id", "board"}


def test_a_stream_is_refused_for_a_clinic_with_no_board_and_when_the_clinic_is_full(
    board: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """404 before anything opens; 503 with a Retry-After when the clinic is at its stream limit."""
    anyone = board.world.anonymous()
    missing = anyone.get("/display/0199b0c0-0000-7000-8000-00000000dead/stream")
    assert missing.status_code == status.HTTP_404_NOT_FOUND

    async def full(site_id: str, **_: object) -> AsyncIterator[LiveEvent]:
        raise TooManySubscribersError(site_id)
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(live_events.broker, "events", full)
    refused = anyone.get(f"/display/{board.world.site_a}/stream")
    assert refused.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert refused.headers["retry-after"] == "30"


def test_a_board_consent_answer_is_announced_to_the_patients_queues_after_the_commit(
    board: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A withdrawal reaches every board showing the patient: ``queue.updated`` for each of their queues."""
    nomvula = board.patient()
    board.consent(nomvula, ConsentPurpose.DISPLAY_NAME, True)
    board.ticket(board.world.triage, nomvula)
    board.ticket(board.world.pharmacy, nomvula)
    board.display(DisplayMode.NAME_LITE)

    heard: list[LiveEvent] = []
    monkeypatch.setattr(live_events.broker, "publish", heard.append)
    board.consent(nomvula, ConsentPurpose.DISPLAY_NAME, False)
    assert sorted((e.type, e.site_id, e.queue_id) for e in heard) == sorted(
        [
            (LiveEventType.QUEUE_UPDATED, board.world.site_a, board.world.triage),
            (LiveEventType.QUEUE_UPDATED, board.world.site_a, board.world.pharmacy),
        ]
    )
    # Nothing about the patient travels with the announcement.
    assert all(NOMVULA not in json.dumps(e.payload()) for e in heard)

    # A purpose no board reads announces nothing.
    heard.clear()
    board.consent(nomvula, ConsentPurpose.NOTIFICATIONS, True)
    assert heard == []


def test_every_screen_of_a_clinic_shares_one_projection_per_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ten streams answering the same event project once; a later event projects again."""
    calls: list[str] = []

    def project(db: object, site_id: str, viewer: BoardViewer) -> dict[str, Any]:
        calls.append(site_id)
        time.sleep(0.02)  # long enough for the other threads to arrive while it runs
        return {"queues": [], "n": len(calls)}

    monkeypatch.setattr(board_state, "project_payload", project)

    @contextmanager
    def no_db() -> Iterator[None]:
        yield None

    memo = BoardProjections()
    event_stamp = time.monotonic()
    answers: list[dict[str, Any] | None] = []
    threads = [
        threading.Thread(
            target=lambda: answers.append(
                memo.payload(
                    no_db, "site-a", BoardViewer.ANONYMOUS, newer_than=event_stamp
                )
            )
        )
        for _ in range(10)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == ["site-a"]
    assert all(answer == {"queues": [], "n": 1} for answer in answers)

    later = time.monotonic()
    assert memo.payload(no_db, "site-a", BoardViewer.ANONYMOUS, newer_than=later) == {
        "queues": [],
        "n": 2,
    }
    # Another viewer of the same clinic is a different board.
    memo.payload(no_db, "site-a", BoardViewer.STAFF, newer_than=later)
    assert len(calls) == 3


def test_a_simulated_eight_hour_day_of_boards_dropping_and_reconnecting_leaves_no_stream_behind() -> (
    None
):
    """Every minute of a clinic day, boards drop without a goodbye and reconnect; the broker keeps count.

    Four boards at each of three clinics. Every simulated minute one board somewhere loses its connection
    without closing it (the client is simply gone: load-shedding, a pulled cable) and a new connection
    replaces it, and every half hour every board reconnects at once, as after a router restart. Dead
    streams are noticed at their next heartbeat (0.01 s here, 15 s in production). After each minute the
    broker holds exactly the streams that are still open, never more, and none once the day is over.
    """

    async def day() -> tuple[int, int, int]:
        broker = SiteEventBroker()
        sites = ["site-a", "site-b", "site-c"]
        tasks: dict[int, tuple[asyncio.Task[None], dict[str, Any]]] = {}
        counter = 0
        peak = 0

        async def board(site: str, state: dict[str, bool]) -> None:
            async def gone() -> bool:
                return state["gone"]

            async for _ in broker.events(
                site, is_disconnected=gone, heartbeat_seconds=0.01
            ):
                pass

        def connect(site: str) -> None:
            nonlocal counter
            state: dict[str, Any] = {"gone": False, "site": site}
            tasks[counter] = (asyncio.create_task(board(site, state)), state)
            counter += 1

        async def settle() -> None:
            """Let the event loop run until every dropped stream has ended and every new one is open."""
            for _ in range(200):
                await asyncio.sleep(0.01)
                live = sum(1 for _, state in tasks.values() if not state["gone"])
                dead_running = any(
                    state["gone"] and not task.done() for task, state in tasks.values()
                )
                if not dead_running and broker.subscriber_count() == live:
                    return
            raise AssertionError("streams did not settle within 2 seconds")

        for site in sites:
            for _ in range(4):
                connect(site)
        await settle()
        for minute in range(8 * 60):
            if minute % 30 == 29:  # every board reconnects together
                for _, state in list(tasks.values()):
                    if not state["gone"]:
                        state["gone"] = True
                        connect(state["site"])
            else:  # one board drops silently and a new connection replaces it
                dropped = next(
                    state
                    for _, state in tasks.values()
                    if not state["gone"] and state["site"] == sites[minute % len(sites)]
                )
                dropped["gone"] = True
                connect(dropped["site"])
            await settle()
            peak = max(peak, broker.subscriber_count())
            assert broker.subscriber_count() == 12, minute
            for key in [k for k, (task, _) in tasks.items() if task.done()]:
                del tasks[key]
        for _, state in tasks.values():
            state["gone"] = True
        await asyncio.gather(*(task for task, _ in tasks.values()))
        return broker.subscriber_count(), peak, counter

    remaining, peak, opened = asyncio.run(day())
    assert remaining == 0
    assert peak == 12
    assert opened > 8 * 60  # every drop was a real reconnection


@pytest.mark.redis
def test_a_change_on_one_instance_reaches_the_streams_of_another_through_redis(
    redis_server_url: str,
) -> None:
    """Two instances, one Redis: a board on instance B hears a call made on instance A; A skips its echo."""
    import redis

    channel = f"clinicq:live-events:test:{time.monotonic_ns()}"
    here, there = SiteEventBroker(), SiteEventBroker()
    clients = [
        redis.Redis.from_url(redis_server_url, decode_responses=True) for _ in range(2)
    ]
    instance_a = RedisFanout(clients[0], here, channel=channel, retry_seconds=0.1)
    instance_b = RedisFanout(clients[1], there, channel=channel, retry_seconds=0.1)

    async def scenario() -> tuple[LiveEvent, int]:
        async def never() -> bool:
            return False

        on_b = there.events("site-a", is_disconnected=never, heartbeat_seconds=30)
        await anext(on_b)  # subscribed
        on_a = here.events("site-a", is_disconnected=never, heartbeat_seconds=30)
        await anext(on_a)
        instance_a.start()
        instance_b.start()
        await asyncio.sleep(0.5)  # both listening
        called = LiveEvent(
            LiveEventType.TICKET_CALLED,
            "site-a",
            queue_id="q1",
            extra={"number": "T004"},
        )
        here.publish(called)  # what publish_live does on instance A
        instance_a.publish(called)
        heard = await asyncio.wait_for(anext(on_b), 5)
        # Instance A delivered its own event once, locally; the Redis echo is ignored.
        local = await asyncio.wait_for(anext(on_a), 1)
        await asyncio.sleep(0.3)
        echoes = here._subscribers["site-a"].copy().pop().queue.qsize()  # a test's peek
        await on_a.aclose()
        await on_b.aclose()
        assert local.payload() == called.payload()
        return heard, echoes

    try:
        heard, echoes = asyncio.run(scenario())
    finally:
        instance_a.stop()
        instance_b.stop()
        for client in clients:
            client.close()
    assert (
        heard.payload()
        == LiveEvent(
            LiveEventType.TICKET_CALLED,
            "site-a",
            at=heard.at,
            queue_id="q1",
            extra={"number": "T004"},
        ).payload()
    )
    assert echoes == 0


def test_a_fanout_that_cannot_reach_redis_never_raises_and_keeps_local_screens_current() -> (
    None
):
    """Redis down: publishing is logged, not raised, and this instance's own streams still hear the event."""

    class Unreachable:
        def publish(self, *_: object) -> None:
            raise ConnectionError("redis is down")

    local = SiteEventBroker()
    fanout = RedisFanout(Unreachable(), local)

    async def scenario() -> str:
        async def never() -> bool:
            return False

        stream = local.events("site-a", is_disconnected=never, heartbeat_seconds=30)
        await anext(stream)
        event = LiveEvent(LiveEventType.QUEUE_UPDATED, "site-a", queue_id="q1")
        local.publish(event)
        fanout.publish(event)  # does not raise
        fanout.publish(event)  # and logs once per outage
        heard = await asyncio.wait_for(anext(stream), 1)
        await stream.aclose()
        return heard.type.value

    assert asyncio.run(scenario()) == "queue.updated"
    assert (
        fanout.deliver('{"origin": "elsewhere", "event": {"type": "nonsense"}}')
        is False
    )
