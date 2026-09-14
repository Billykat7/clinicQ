"""The waiting-room board's live stream: ``GET /display/{site_id}/stream`` (Issue 57).

Server-sent events over one long-lived response (FastAPI's ``StreamingResponse`` with
``text/event-stream``), in the envelope the dashboard's stream uses, carrying the privacy projection
with every event (:mod:`src.modules.display.board_state`):

* **Read-only, and only for the clinic's own screens.** No sign-in: the stream answers a paired kiosk
  box of this clinic (its device cookie) or a signed-in staff member there, exactly as the page and
  ``/state`` do (Issue 61), and anyone else with ``401``. A clinic with no public board answers 404
  before any stream opens. A stream ends at its next access check (every
  :data:`~src.modules.display.board_state.BOARD_ACCESS_CHECK_SECONDS`) once its box is revoked or its
  clinic stops having a board, and the screen then goes back to pairing.
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
from src.modules.display.projection import displayed_site
from src.web.context import short_session
from src.web.display import BoardAudience, board_audience

router = APIRouter(prefix="/display", include_in_schema=False)

#: How long a refused client should wait before trying again when the clinic has too many streams.
RETRY_AFTER_SECONDS = "30"


def _open(request: Request, site_id: str) -> BoardAudience | int:
    """Who the stream is for, or the status that refuses it: 401 for nobody who may see it, 404 for no board."""
    with short_session(request) as db:
        audience = board_audience(request, db, site_id)
        if audience is None:
            return status.HTTP_401_UNAUTHORIZED
        if displayed_site(db, site_id) is None:
            return status.HTTP_404_NOT_FOUND
        return audience


@router.get("/{site_id}/stream")
async def board_stream(site_id: str, request: Request) -> Response:
    """The board's live events: ``board.state`` first, then each change with the board, and a beat."""
    opened = await asyncio.to_thread(_open, request, site_id)
    if isinstance(opened, int):
        return Response(status_code=opened)
    audience = opened

    def project(newer_than: float) -> dict[str, Any] | None:
        """The board after ``newer_than`` (blocking: run it in a thread)."""
        return projections.payload(
            lambda: short_session(request),
            site_id,
            audience.viewer,
            queue_ids=audience.queue_ids,
            newer_than=newer_than,
        )

    async def still_allowed() -> bool:
        """Whether this box or person may still see the board, and it still exists (off the event loop)."""
        return await asyncio.to_thread(_open, request, site_id) == audience

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
