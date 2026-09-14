"""The waiting-room board as a live stream: the privacy projection, sent on every change (Issue 57).

A board's stream uses the dashboard's envelope (:mod:`src.core.live_events`), with one difference that
makes it self-sufficient: **every event carries the board**. The ``board`` field is
:meth:`~src.modules.display.projection.BoardState.payload`, the privacy projection of Issue 58 and
nothing else, so a kiosk never asks for anything a stream did not bring:

.. code-block:: text

    id: 7
    event: board.state
    data: {"type": "board.state", "site_id": "…", "at": "…", "board": {"site_id": "…", "queues": […]}}

    id: 8
    event: ticket.called
    data: {"type": "ticket.called", "site_id": "…", "at": "…", "queue_id": "…", "board": {…}}

* ``board.state`` is always the first event on a connection, so every reconnection is a full resync.
* ``ticket.called``, ``queue.updated`` and ``board.config_changed`` carry the board as it is after the
  change. Consent changes arrive as ``queue.updated`` for the patient's queues
  (:func:`~src.modules.patients.consent.record_consent`), so a withdrawal reaches the screen at once.
* ``heartbeat`` carries nothing, every :data:`BOARD_HEARTBEAT_SECONDS`.

**One projection per change, however many screens.** A clinic may have several boards, and a change can
arrive in a burst (ten walk-ins in a minute). :class:`BoardProjections` keeps the last board projected
for each clinic and viewer. A stream that hears an event reuses a board projected *after* that event
reached this process, and projects afresh only when none exists, so ten screens and a burst of events
cost one projection per settled change.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.orm import Session

from src.commons.enums import LiveEventType
from src.core.live_events import LiveEvent
from src.modules.display.enums import BoardViewer
from src.modules.display.projection import (
    displayed_site,
    ensure_projected,
    project_board,
)

#: A board's stream beats every 15 seconds (the issue's figure): a screen that hears nothing for two beats
#: (30 seconds) knows its connection is dead and says so.
BOARD_HEARTBEAT_SECONDS: Final = 15.0
#: How often a board's stream checks that its clinic still has a public board.
BOARD_ACCESS_CHECK_SECONDS: Final = 60.0
#: The payload key a board event carries the projection under.
BOARD_FIELD: Final = "board"


@dataclass(frozen=True, slots=True)
class _Projected:
    """A board payload and when it was projected, on this process's monotonic clock."""

    at: float
    payload: dict[str, Any]


class BoardProjections:
    """The last board projected per clinic and viewer, shared by every stream on this process."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """An empty memo; ``clock`` is the monotonic clock events are stamped with."""
        self._clock = clock
        self._latest: dict[tuple[str, BoardViewer], _Projected] = {}
        self._locks: dict[tuple[str, BoardViewer], threading.Lock] = {}
        self._guard = threading.Lock()

    def payload(
        self,
        db_factory: Callable[[], Any],
        site_id: str,
        viewer: BoardViewer,
        *,
        newer_than: float,
    ) -> dict[str, Any] | None:
        """The board for ``site_id`` as ``viewer`` sees it, projected after ``newer_than``.

        Blocking (it may read the database): call it off the event loop. ``None`` when the clinic no
        longer has a public board.

        Args:
            db_factory: A context manager factory yielding a short session.
            site_id: The clinic.
            viewer: Who the stream is for.
            newer_than: The monotonic stamp of the event being answered; a board projected after it
                already includes the change.
        """
        key = (site_id, viewer)
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:  # one projection at a time per clinic and viewer; the others wait and reuse it
            latest = self._latest.get(key)
            if latest is not None and latest.at > newer_than:
                return latest.payload
            started = self._clock()
            with db_factory() as db:
                payload = project_payload(db, site_id, viewer)
            if payload is None:
                self._latest.pop(key, None)
                return None
            self._latest[key] = _Projected(at=started, payload=payload)
            return payload


def project_payload(
    db: Session, site_id: str, viewer: BoardViewer
) -> dict[str, Any] | None:
    """The projected board payload for ``site_id``, or ``None`` when it has no public board."""
    site = displayed_site(db, site_id)
    if site is None:
        return None
    return project_board(db, site, viewer=viewer).payload()


def board_event(event: LiveEvent, payload: dict[str, Any]) -> LiveEvent:
    """``event`` as a board sends it: its type, clinic, time and queue, and the projected board.

    Anything else the event carried (a called number, say) is left behind: a board's stream carries the
    projection and the envelope, nothing more. The whole event is checked with
    :func:`~src.modules.display.projection.ensure_projected` before it can be sent.
    """
    sent = LiveEvent(
        type=event.type,
        site_id=event.site_id,
        at=event.at,
        queue_id=event.queue_id,
        extra={BOARD_FIELD: payload},
    )
    ensure_projected(sent.payload(), path="stream")
    return sent


def state_event(site_id: str, payload: dict[str, Any]) -> LiveEvent:
    """The full board, the first event on every connection (a resync)."""
    return board_event(LiveEvent(LiveEventType.BOARD_STATE, site_id), payload)


#: The process's memo, shared by every board stream.
projections = BoardProjections()
