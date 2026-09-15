"""The patient's ticket page and its live stream: ``/t/{token}`` and ``/t/{token}/stream`` (Issue 68).

A public page: no account, no sign-in. The unguessable link (:mod:`src.modules.queue.ticket_page`) is
what opens it, so a patient can share it with the person driving them.

* **The page carries its first state.** The data ``GET /api/v1/tickets/{token}`` returns is rendered
  into the page and embedded in it, so on a slow connection the number and the place in line are on the
  screen before any script has loaded. ``ticket.js`` then follows the stream.
* **The stream is the board's envelope** (Issue 57): server-sent events formatted by
  :func:`~src.core.live_events.format_event`. The first event is ``ticket.state``, the whole ticket, so
  a phone that reconnects after a tunnel is right at once; after that a ``ticket.state`` follows every
  change in the ticket's queue, and a heartbeat every 15 seconds. The stream ends once the ticket is
  finished, because nothing about it will change again.
* **Patients have their own stream budget** (:data:`~src.core.live_events.patient_broker`), so a waiting
  room full of phones cannot take a stream from the clinic's board. Beyond it the stream answers ``503``
  with ``Retry-After`` and the page follows by polling the JSON instead, showing the data's age.
* **No request-scoped session.** A stream may stay open for an hour, so every read opens a short one.

**The installable app** (Issue 69) lives in the same scope, ``/t/``:

* ``GET /patient-sw.js`` is the patient service worker, with this release's version and its shell (the
  files the offline page needs, :data:`PATIENT_SHELL`) written in. Web push (Issue 64) and the offline shell
  are one worker, separate from the waiting-room board's (Issue 62).
* ``GET /t/`` is where the home-screen app opens (the manifest's ``start_url``): straight to the signed-in
  patient's open ticket when there is one, otherwise a page that opens the last ticket this phone saw, and
  offers to sign in by phone (Issue 200). An iPhone Home Screen app keeps its own storage, apart from
  Safari's, so a patient who joined in Safari signs in here, inside the app's scope, to find their ticket.
* ``GET /t/offline`` is the page the worker shows in place of any ``/t/`` page the network cannot bring:
  the last known state of the ticket, kept on the phone, with how old it is.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

from fastapi import APIRouter, Request, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from src.commons.enums import TICKET_TERMINAL_STATUSES, LiveEventType
from src.core.config import get_settings
from src.core.live_events import (
    EVENT_STREAM_MEDIA_TYPE,
    HEARTBEAT_SECONDS,
    STREAM_HEADERS,
    LiveEvent,
    TooManySubscribersError,
    format_event,
    patient_broker,
)
from src.modules.patients.sessions import signed_in_patient_id
from src.modules.queue.schemas import TicketPageOut
from src.modules.queue.ticket_page import (
    active_page_for_patient,
    find_by_page_token,
    page_state,
)
from src.web.context import short_session
from src.web.join import sign_in_view
from src.web.routes import templates

router = APIRouter(prefix="/t", include_in_schema=False)
#: The patient service worker (Issue 64), served from the site's root so it may control ``/t/``.
worker_router = APIRouter(include_in_schema=False)
_WORKER = Path(__file__).resolve().parents[1] / "static" / "patient-sw.js"
#: The marks in ``patient-sw.js`` the server writes the release's version and the shell's file list over.
WORKER_VERSION_MARK: Final = "__CLINICQ_PATIENT_VERSION__"
WORKER_SHELL_MARK: Final = "__CLINICQ_PATIENT_SHELL__"
#: The page the worker shows when a ``/t/`` page cannot be reached.
OFFLINE_PATH: Final = "/t/offline"
#: Everything the offline page needs to draw itself with no network, and nothing else. The worker keeps
#: exactly these files, in a cache named for the release, so what it stores cannot grow: a test renders the
#: offline page and fails if it asks for a file that is not listed here.
PATIENT_SHELL: Final[tuple[str, ...]] = (
    OFFLINE_PATH,
    "/static/manifest.json",
    "/static/css/site.css",
    "/static/css/components.css",
    "/static/css/layouts.css",
    "/static/css/ticket.css",
    "/static/fonts/dm-sans-latin.woff2",
    "/static/favicon.svg",
    "/static/icons/app-192.png",
    "/static/icons/apple-touch-icon.png",
    "/static/js/theme.js",
    "/static/vendor/htmx-2.0.0.min.js",
    "/static/js/csrf-htmx.js",
    "/static/js/ui-feedback.js",
    "/static/js/patient-tickets.js",
    "/static/js/pwa.js",
    "/static/js/ticket-offline.js",
)


def worker_version() -> str:
    """The version written into the worker: the release and its commit, so every deploy installs a new worker."""
    settings = get_settings()
    if settings.git_sha and settings.git_sha != "unknown":
        return f"{settings.version}-{settings.git_sha[:12]}"
    return settings.version


@worker_router.get("/patient-sw.js", name="patient_service_worker")
def patient_service_worker() -> Response:
    """The patient service worker, with this release's version and shell written in.

    Never cached by the browser's HTTP cache: the browser compares the worker on every navigation, and a
    different version installs the new release on the next launch.
    """
    body = (
        _WORKER.read_text(encoding="utf-8")
        .replace(WORKER_VERSION_MARK, worker_version())
        .replace(WORKER_SHELL_MARK, json.dumps(list(PATIENT_SHELL)))
    )
    return Response(
        body,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/t/"},
    )


#: The page's HTML is never cached: it carries the state it was rendered with.
NO_STORE: Final = {"Cache-Control": "no-store"}
#: How long a refused client should wait before trying the stream again.
RETRY_AFTER_SECONDS: Final = "30"
#: How often a ticket stream beats. Patched shorter by the browser tests, like the board's.
TICKET_HEARTBEAT_SECONDS: float = HEARTBEAT_SECONDS


def _read(request: Request, token: str) -> TicketPageOut | None:
    """The ticket's page state now, or ``None`` when the link finds nothing (blocking: run in a thread)."""
    with short_session(request) as db:
        ticket = find_by_page_token(db, token)
        if ticket is None:
            return None
        return page_state(
            db, ticket, viewer_patient_id=signed_in_patient_id(request, db)
        )


def _page_context(page_title: str) -> dict[str, object]:
    settings = get_settings()
    return {
        "settings": settings,
        "app_name": settings.app_name,
        "page_title": page_title,
    }


@router.get("/", name="ticket_home")
def ticket_home(request: Request) -> Response:
    """Where the installed app opens: the signed-in patient's open ticket, or the page that finds the last one.

    Without a signed-in patient with an open ticket, ``patient-home.js`` opens the last unfinished ticket this
    phone saw, and otherwise says how to join a queue. Nobody signed in is offered the phone sign-in, which
    comes back here once it succeeds.
    """
    with short_session(request) as db:
        patient_id = signed_in_patient_id(request, db)
        path = active_page_for_patient(db, patient_id) if patient_id else None
    if path is not None:
        return RedirectResponse(
            path, status_code=status.HTTP_303_SEE_OTHER, headers=NO_STORE
        )
    response = templates.TemplateResponse(
        request,
        "patient/home.html",
        {
            **_page_context("Your ticket"),
            "signed_in": patient_id is not None,
            "sign_in": sign_in_view(),
        },
    )
    response.headers.update(NO_STORE)
    return response


@router.get("/offline", name="ticket_offline")
def ticket_offline(request: Request) -> Response:
    """The offline page, kept by the service worker and shown when a ``/t/`` page cannot be reached.

    It carries no ticket: ``ticket-offline.js`` draws the last state the phone kept, with its age.
    """
    return templates.TemplateResponse(
        request, "patient/offline.html", _page_context("Offline")
    )


@router.get("/{token}", name="ticket_page")
def ticket_page(token: str, request: Request) -> Response:
    """The ticket page. An unknown link gets a page that says so, with a 404."""
    state = _read(request, token)
    settings = get_settings()
    context = {
        "settings": settings,
        "app_name": settings.app_name,
        "page_title": f"Ticket {state.number}" if state else "Ticket not found",
        "ticket": state,
        "state_json": state.model_dump(mode="json") if state else None,
        "api_url": f"/api/v1/tickets/{token}",
    }
    response = templates.TemplateResponse(
        request,
        "queue/ticket.html" if state else "queue/ticket_missing.html",
        context,
        status_code=status.HTTP_200_OK if state else status.HTTP_404_NOT_FOUND,
    )
    response.headers.update(NO_STORE)
    return response


def _state_event(state: TicketPageOut, site_id: str) -> LiveEvent:
    """A ``ticket.state`` event carrying the page's data."""
    return LiveEvent(
        LiveEventType.TICKET_STATE,
        site_id,
        extra={"ticket": state.model_dump(mode="json")},
    )


@router.get("/{token}/stream", name="ticket_stream")
async def ticket_stream(token: str, request: Request) -> Response:
    """``ticket.state`` now and after every change in the ticket's queue, with a heartbeat, until it ends."""
    located = await asyncio.to_thread(_locate, request, token)
    if located is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    site_id, queue_id = located

    try:
        events = patient_broker.events(
            site_id,
            is_disconnected=request.is_disconnected,
            # A ticket's place moves only when its own queue changes.
            accept=lambda event: event.queue_id in (None, queue_id),
            heartbeat_seconds=TICKET_HEARTBEAT_SECONDS,
        )
        # The first event (a heartbeat) proves the subscription, so a full clinic is refused here.
        await anext(events)
    except TooManySubscribersError:
        return Response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": RETRY_AFTER_SECONDS},
        )

    # Subscribed first, read second: a change committed in between is in the state or in an event.
    first = await asyncio.to_thread(_read, request, token)
    if first is None:
        await events.aclose()
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    async def body() -> AsyncIterator[str]:
        """The ticket, then the ticket after every change, until it is finished or the client goes."""
        try:
            yield format_event(_state_event(first, site_id), patient_broker.next_id())
            if first.status in TICKET_TERMINAL_STATUSES:
                return
            async for event in events:
                if event.type is LiveEventType.HEARTBEAT:
                    yield format_event(event, patient_broker.next_id())
                    continue
                state = await asyncio.to_thread(_read, request, token)
                if state is None:
                    return
                yield format_event(
                    _state_event(state, site_id), patient_broker.next_id()
                )
                if state.status in TICKET_TERMINAL_STATUSES:
                    return
        finally:
            await events.aclose()

    return StreamingResponse(
        body(), media_type=EVENT_STREAM_MEDIA_TYPE, headers=STREAM_HEADERS
    )


def _locate(request: Request, token: str) -> tuple[str, str] | None:
    """The ticket's clinic and queue, or ``None`` (blocking: run in a thread)."""
    with short_session(request) as db:
        ticket = find_by_page_token(db, token)
        if ticket is None:
            return None
        return ticket.site_id, ticket.queue_id
