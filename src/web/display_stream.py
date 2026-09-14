"""The waiting-room board's live stream: ``GET /display/{site_id}/stream`` (Issue 57).

Server-sent events over one long-lived response (FastAPI's ``StreamingResponse`` with
``text/event-stream``), in the envelope the dashboard's stream uses, carrying the privacy projection
with every event (:mod:`src.modules.display.board_state`):

* **Read-only and unauthenticated.** A board is a public screen. The stream answers whoever asks with
  what that viewer may see, exactly as the page and ``/state`` do: numbers only for an address anyone
  can type, the clinic's own display mode for its signed-in staff (Issue 58). A clinic with no public
  board answers 404 before any stream opens, and a stream whose clinic stops having one ends at its
  next check.
* **Full resync on every connection.** The first event is ``board.state``, the whole board, so a screen
  that reconnects after a dropped connection, a restart or a power cut is right before anything else
  arrives.
* **A heartbeat every 15 seconds**, so a silent connection is noticed within two missed beats.
* **Bounded and cleaned up.** The clinic's streams share :data:`~src.core.live_events.MAX_SUBSCRIBERS_PER_SITE`
  on each instance (``503`` with ``Retry-After`` beyond it). A client that has gone is noticed at the
  next beat and its subscription removed, and one that stops reading is dropped. ``/metrics`` shows
  ``clinicq_live_streams_open``.
* **No request-scoped session.** A stream may stay open all day, so every read opens a short session
  of its own, off the event loop.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import Response, StreamingResponse

from src.commons.enums import LiveEventType
from src.core.live_events import (
    EVENT_STREAM_MEDIA_TYPE,
    STREAM_HEADERS,
    TooManySubscribersError,
    broker,
    format_event,
)
from src.modules.display.board_state import (
    BOARD_ACCESS_CHECK_SECONDS,
    BOARD_HEARTBEAT_SECONDS,
    board_event,
    projections,
    state_event,
)
from src.modules.display.enums import BoardViewer
from src.modules.display.projection import displayed_site
from src.web.context import short_session
from src.web.display import board_viewer

router = APIRouter(prefix="/display", include_in_schema=False)

#: How long a refused client should wait before trying again when the clinic has too many streams.
RETRY_AFTER_SECONDS = "30"


def _open(request: Request, site_id: str) -> BoardViewer | None:
    """Who the stream is for, or ``None`` when the clinic has no public board."""
    with short_session(request) as db:
        site = displayed_site(db, site_id)
        if site is None:
            return None
        return board_viewer(request, db, site)


@router.get("/{site_id}/stream")
async def board_stream(site_id: str, request: Request) -> Response:
    """The board's live events: ``board.state`` first, then each change with the board, and a beat."""
    viewer = await asyncio.to_thread(_open, request, site_id)
    if viewer is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    def project(newer_than: float) -> dict[str, Any] | None:
        """The board after ``newer_than`` (blocking: run it in a thread)."""
        return projections.payload(
            lambda: short_session(request), site_id, viewer, newer_than=newer_than
        )

    async def still_allowed() -> bool:
        """Whether the clinic still has a public board (a fresh read, off the event loop)."""
        return await asyncio.to_thread(_open, request, site_id) is not None

    try:
        events = broker.events(
            site_id,
            is_disconnected=request.is_disconnected,
            still_allowed=still_allowed,
            heartbeat_seconds=BOARD_HEARTBEAT_SECONDS,
            access_check_seconds=BOARD_ACCESS_CHECK_SECONDS,
        )
        # The first event (a heartbeat) proves the subscription, so a full clinic is refused here.
        await anext(events)
    except TooManySubscribersError:
        return Response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": RETRY_AFTER_SECONDS},
        )

    # Subscribed first, projected second: a change committed in between is in the board or in an event.
    first = await asyncio.to_thread(project, time.monotonic())
    if first is None:
        await events.aclose()
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    async def body() -> AsyncIterator[str]:
        """The full board, then every change with its board, until the client goes."""
        try:
            yield format_event(state_event(site_id, first), broker.next_id())
            async for event in events:
                if event.type is LiveEventType.HEARTBEAT:
                    yield format_event(event, broker.next_id())
                    continue
                payload = await asyncio.to_thread(project, event.stamp)
                if payload is None:
                    # The clinic's board went away. End, and let the screen reconnect to the 404.
                    return
                yield format_event(board_event(event, payload), broker.next_id())
        finally:
            await events.aclose()

    return StreamingResponse(
        body(), media_type=EVENT_STREAM_MEDIA_TYPE, headers=STREAM_HEADERS
    )
