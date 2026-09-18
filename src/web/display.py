"""The waiting-room board's routes (M8): read-only, fed only by the privacy projection, shown to paired boxes.

Every board response is built from :func:`~src.modules.display.projection.project_board` and nothing
else (non-negotiable 4, Issue 58):

* ``GET /display/{site_id}`` is the board page (Issue 56): a full-screen kiosk page with its own
  stylesheet and script, handed the same payload to draw from;
* ``GET /display/{site_id}/state`` is the board as JSON: :meth:`BoardState.payload`, which has no
  ``name`` or ``comment`` key at all unless the mode and the patient's consent put a value in it;
* a board template is rendered only through :func:`board_template_response`, which checks its whole
  context with :func:`~src.modules.display.projection.ensure_projected` first, so handing a template
  a ticket or a patient is an error, never a page.

**Who may see a board (Issue 61).** A board is not a public page any more: the address alone shows
nothing, so it can be neither guessed nor shared. Two audiences may see it
(:class:`BoardAudience`), both under the site's own display mode:

* a **paired kiosk box** of this clinic, recognised by its device cookie (:mod:`src.modules.display.devices`),
  which also narrows the board to the queues the manager chose for that screen;
* a **signed-in staff member** of this clinic, previewing its screen.

Anyone else opening a board page is sent to ``/display``, the start page every box is provisioned with,
which shows a pairing code; the JSON and the stream answer ``401``. A clinic that is unknown, hidden or
not yet verified answers the same 404 as one that never existed, and only to an audience that could have
seen it.

**The box's own routes:** ``GET /display`` (the start page: its board if paired, a pairing code if not),
``GET /display/pairing`` (the code page asks whether it has been paired yet) and ``POST /display/heartbeat``
(the board page reports every minute; a revoked box is told to go back to the start page), and
``GET /display/board-sw.js``, the service worker that lets a paired box show its last board, and say how
old it is, when it starts with no network (Issue 62).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.commons.enums import DisplayDeviceKind
from src.commons.time import now_sast, stored_sast
from src.core.config import get_settings
from src.core.nav_visibility import peek_user_from_refresh_cookie
from src.core.site_scope import permitted_site_ids, site_not_found
from src.database.models import DisplayDevice
from src.database.session import get_db
from src.modules.display import devices
from src.modules.display.announcements import (
    CHIME_PATH,
    FALLBACK_LANGUAGE,
    announcement_for,
    number_clips,
)
from src.modules.display.board_state import BOARD_HEARTBEAT_SECONDS
from src.modules.display.enums import BoardViewer, PairingState
from src.modules.display.messages import health_messages
from src.modules.display.projection import (
    displayed_site,
    ensure_projected,
    project_board,
)
from src.web.routes import templates

router = APIRouter(prefix="/display", include_in_schema=False)

DbSession = Annotated[Session, Depends(get_db)]

#: A board's answers change with every call, and may carry a name under a wider mode: never cached.
NO_STORE: dict[str, str] = {"Cache-Control": "no-store"}

#: How often the page asks for the board while its live stream is down (Issue 57).
POLL_SECONDS: Final = 10
#: How long a newly called ticket stays highlighted, counted from the call on the server's clock.
HIGHLIGHT_SECONDS: Final = 20
#: How many queue panels one screen holds. A clinic with more shows them in pages of this many.
PANELS_PER_PAGE: Final = 4
#: How long each page of panels stays up before the next, when there is more than one.
PAGE_SECONDS: Final = 15
#: How long each health notice stays up before the next.
MESSAGE_SECONDS: Final = 12


#: Where a box that may not see a board is sent: the start page, which pairs it.
START_PATH: Final = "/display"
#: The check-in tablet's page (Issue 83). It sits under ``/display`` so a device paired at the one start
#: address keeps the one device cookie, which is scoped to that path.
CHECK_IN_PATH: Final = "/display/check-in"
#: How long a box's device cookie lasts in the browser: the longest a browser keeps a cookie (400 days),
#: renewed by every heartbeat, so a box in daily use never loses it.
DEVICE_COOKIE_MAX_AGE: Final = 400 * 24 * 3600
#: How often an unpaired box's code page asks whether it has been paired.
PAIRING_POLL_SECONDS: Final = 3
#: How often a paired box's board page reports in.
DEVICE_HEARTBEAT_SECONDS: Final = 60
#: The kiosk board's service worker (Issue 62) and the mark its version is written over.
BOARD_WORKER_FILE: Final = (
    Path(__file__).resolve().parents[1] / "static" / "board-sw.js"
)
BOARD_WORKER_VERSION_MARK: Final = "__CLINICQ_BOARD_VERSION__"
#: A board that goes this long without news from the clinic says so, with the time it last had any. Longer
#: than a poll's interval, so a board kept current by polling does not flicker between the two.
STALE_AFTER_SECONDS: Final = 20
#: A last-known board older than this is no longer shown when the box is offline: after four hours the
#: numbers mislead more than they help, and a name the screen showed should not stay on it all day.
STALE_LIMIT_SECONDS: Final = 4 * 3600


@dataclass(frozen=True, slots=True)
class BoardAudience:
    """Who a board response is for: a paired box of this clinic, or its signed-in staff."""

    viewer: BoardViewer
    #: The queues this screen shows; ``None`` for all. Only a box can be narrowed.
    queue_ids: frozenset[str] | None = None


def device_from_cookie(request: Request, db: Session) -> DisplayDevice | None:
    """The box whose device cookie this request carries, in whatever state, or ``None``."""
    return devices.device_for_secret(
        db, request.cookies.get(get_settings().display_device_cookie_name)
    )


def home_for(device: DisplayDevice) -> str:
    """Where a paired device belongs: its clinic's board, or the check-in page (Issue 83)."""
    if device.kind_enum is DisplayDeviceKind.CHECK_IN:
        return CHECK_IN_PATH
    return f"{START_PATH}/{device.site_id}"


def board_audience(request: Request, db: Session, site_id: str) -> BoardAudience | None:
    """Who may see ``site_id``'s board on this request, or ``None`` when nobody here may.

    Decided before the clinic is looked up, so an unauthorised caller cannot learn whether an id exists.
    """
    device = device_from_cookie(request, db)
    if devices.is_paired(device) and device is not None and device.site_id == site_id:
        chosen = frozenset(device.queue_ids) if device.queue_ids else None
        return BoardAudience(BoardViewer.DEVICE, queue_ids=chosen)
    user = peek_user_from_refresh_cookie(db, request)
    if user is not None and site_id in permitted_site_ids(db, user):
        return BoardAudience(BoardViewer.STAFF)
    return None


def set_device_cookie(response: Response, secret: str) -> None:
    """Keep a box's secret in its browser: httpOnly, same-site only, and only for the board's paths."""
    response.set_cookie(
        key=get_settings().display_device_cookie_name,
        value=secret,
        max_age=DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        secure=not get_settings().is_development,
        samesite="strict",
        path=START_PATH,
    )


def board_template_response(
    request: Request,
    name: str,
    context: dict[str, Any],
    *,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    """Render a board template from projected data only.

    ``context`` is checked with :func:`ensure_projected` before the template is looked up, so a raw
    ``Ticket``, ``Patient`` or ``Site`` anywhere in it raises
    :class:`~src.modules.display.projection.UnprojectedBoardDataError` and nothing is rendered.
    """
    ensure_projected(context)
    settings = get_settings()
    # The page frame's own needs (base.html), added after the check: configuration, not board data.
    chrome = {"settings": settings, "app_name": settings.app_name}
    response = templates.TemplateResponse(
        request, name, {**context, **chrome}, status_code=status_code
    )
    response.headers.update(NO_STORE)
    return response


def _unauthorised() -> Response:
    """The answer to a board's JSON or stream asked for by nobody who may see it."""
    return JSONResponse(
        {"detail": "This screen is not paired with a clinic."},
        status_code=status.HTTP_401_UNAUTHORIZED,
        headers=NO_STORE,
    )


class HeartbeatIn(BaseModel):
    """What a board page reports every minute."""

    app_version: str | None = Field(default=None, max_length=40)


@router.get("", response_class=HTMLResponse)
def start_page(request: Request, db: DbSession) -> Response:
    """The one address every kiosk box opens (Issue 61): its board when paired, a pairing code when not.

    A box with no device cookie, or a revoked one, is given a new unpaired device and a code. A box that
    is waiting keeps its device and gets a fresh code, because only the code's digest is stored.
    """
    device = device_from_cookie(request, db)
    if devices.is_paired(device) and device is not None:
        return RedirectResponse(home_for(device), status_code=status.HTTP_302_FOUND)
    user_agent = request.headers.get("user-agent")
    secret: str | None = None
    if device is None or device.revoked_at is not None:
        started = devices.start_device(db, user_agent=user_agent)
        device, code, secret = started.device, started.code, started.secret
    else:
        code = devices.renew_code(db, device)
    expires_at = device.pairing_expires_at
    db.commit()
    response = board_template_response(
        request,
        "display/pair.html",
        {
            "page_title": "Pair this screen",
            "code": devices.format_code(code),
            "expires_at": expires_at,
            "pairing_url": request.url_for("pairing_state").path,
            "poll_seconds": PAIRING_POLL_SECONDS,
            "code_minutes": get_settings().display_pairing_code_minutes,
        },
    )
    if secret is not None:
        set_device_cookie(response, secret)
    return response


#: Google's Cast receiver framework, the one third-party script in the whole application. A
#: Chromecast will not run a receiver that does not load it, and it is served from this host only.
CAST_SDK_URL: Final = (
    "https://www.gstatic.com/cast/sdk/libs/caf_receiver/v3/cast_receiver_framework.js"
)
#: The policy the receiver page carries instead of the strict one every other page gets
#: (:func:`~src.core.security_headers.build_content_security_policy`). It differs in exactly one
#: directive — the script host above — and is otherwise as closed as the default, including
#: ``frame-src 'self'``, which is what confines the board frame to this origin.
CAST_PAGE_CSP: Final = (
    "default-src 'self'; "
    f"script-src 'self' {CAST_SDK_URL.rsplit('/', 1)[0]}/ https://www.gstatic.com; "
    "style-src 'self'; img-src 'self' data: blob: https:; font-src 'self'; "
    # ``ws://localhost:8008`` is not a loophole: on a Chromecast the receiver page *is* running on
    # the device, and that is the device's own local channel between the page and the Cast platform
    # ("cast.receiver.IpcChannel"). Leaving it out does not make the page safer, it makes it not a
    # receiver — caught by loading this page in a browser, where the SDK opened the socket and the
    # policy refused it before the page could ever reach a television.
    "connect-src 'self' ws://localhost:8008 wss://localhost:8008 https://*.gstatic.com; "
    "frame-src 'self'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'self'; object-src 'none';"
)


@router.get("/cast", response_class=HTMLResponse, name="display_cast_receiver")
def cast_receiver(request: Request) -> Response:
    """The page a Chromecast loads when the dashboard casts to it (Issue 237).

    Registered once with Google, at ``cast.google.com/publish``, as the application id a deployment
    then puts in ``CAST_RECEIVER_APP_ID`` (``docs/OPS/SMART_TV_SETUP.md``). A Chromecast loads the
    registered address and nothing else, which is why this page takes no clinic in its address: the
    clinic arrives as a claim code over the Cast connection, and the page spends it at
    :func:`display_claim`.

    Public and stateless on purpose. Anyone may open it and see a splash screen saying "Starting…";
    it shows a board only after a code it did not choose has been accepted, and that code is minted
    by a clinic manager and delivered over the clinic's own network.
    """
    # Through the guarded renderer like every other display template, though this one shows no board
    # data of its own: the guarantee is that there is no *second* way to render a page under
    # ``display/``, and a page that frames the board is exactly where a second way would be missed.
    response = board_template_response(
        request, "display/cast.html", {"cast_sdk_url": CAST_SDK_URL}
    )
    # Set here so the security-headers middleware leaves it alone; see CAST_PAGE_CSP.
    response.headers["Content-Security-Policy"] = CAST_PAGE_CSP
    return response


class ClaimIn(BaseModel):
    """The one-time code a screen the server reached hands back to take up its row (Issue 237)."""

    code: str = Field(min_length=6, max_length=12)


#: Said to a screen presenting a code that is unknown, expired or already spent. The same sentence
#: for all three, like :data:`~src.modules.display.router.CODE_NOT_FOUND`: a screen is not owed an
#: explanation that would help something else guess.
CLAIM_REFUSED: Final = (
    "This screen is not expected. Ask the clinic to send the board again."
)


@router.post("/claim", name="display_claim")
def display_claim(payload: ClaimIn, request: Request, db: DbSession) -> Response:
    """Let a screen the dashboard reached over the network take up the row held for it (Issue 237).

    The mirror of :func:`start_page`. There, a box shows a code and waits for a person to carry it to
    the dashboard; here the dashboard already picked this screen out of what answered on the clinic's
    network (:mod:`src.modules.display.discovery`) and sent it the code directly
    (:mod:`src.modules.display.casting`), so the screen proves it is the one that was contacted by
    handing the code straight back.

    On success the screen is given its device secret in the same httpOnly cookie every kiosk box
    keeps, and told where its board is. The code is spent in that moment, so a replay finds nothing.

    Not under ``/api/``, so the CSRF double-submit does not apply — and must not: the caller is a
    television's Cast receiver with no cookie of ours yet and nothing to double-submit. What stands
    in its place is the code itself: single-use, ten minutes old at most, and known only to a screen
    the clinic's own server reached on the clinic's own network.
    """
    try:
        claimed = devices.claim_device(
            db, code=payload.code, user_agent=request.headers.get("user-agent")
        )
    except devices.ClaimNotFoundError:
        return JSONResponse(
            {"detail": CLAIM_REFUSED},
            status_code=status.HTTP_404_NOT_FOUND,
            headers=NO_STORE,
        )
    db.commit()
    response = JSONResponse(
        {"board_url": home_for(claimed.device)}, headers=dict(NO_STORE)
    )
    set_device_cookie(response, claimed.secret)
    return response


@router.get("/claim", name="display_claim_link")
def display_claim_link(request: Request, db: DbSession, code: str = "") -> Response:
    """The same claim, opened as a link, for a screen with a browser but no Chromecast (Issue 237).

    A Smart TV that has a web browser but will not be cast to — and a box being set up by hand — can
    be sent straight to its board by opening one short address instead of showing a code for someone
    to carry. The code is in the address because a television has no other way to carry one: it is
    single-use and expires with the row it claims, and it is spent the instant this runs, so the link
    in a history or a log opens nothing afterwards.

    A refused code lands on the ordinary pairing screen rather than an error page: whatever went
    wrong, showing a fresh code is the way out of it.
    """
    try:
        claimed = devices.claim_device(
            db, code=code, user_agent=request.headers.get("user-agent")
        )
    except devices.ClaimNotFoundError:
        return RedirectResponse(START_PATH, status_code=status.HTTP_302_FOUND)
    db.commit()
    response = RedirectResponse(
        home_for(claimed.device), status_code=status.HTTP_302_FOUND
    )
    response.headers.update(NO_STORE)
    set_device_cookie(response, claimed.secret)
    return response


@router.get("/board-sw.js", include_in_schema=False)
def board_service_worker() -> Response:
    """The kiosk board's service worker (Issue 62), served from here so it may look after ``/display`` and below.

    It is ``src/static/board-sw.js`` with the release's version written in, so every release installs a
    new worker, which fetches the new page and assets and drops the old ones. Never cached by the browser's
    HTTP cache: a worker checks for its own update on every navigation.
    """
    body = BOARD_WORKER_FILE.read_text(encoding="utf-8").replace(
        BOARD_WORKER_VERSION_MARK, get_settings().version
    )
    return Response(
        body,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": START_PATH},
    )


@router.get("/pairing")
def pairing_state(request: Request, db: DbSession) -> Response:
    """Whether the box asking has been paired: ``paired`` with its board's address, ``waiting``, or ``expired``."""
    device = device_from_cookie(request, db)
    if devices.is_paired(device) and device is not None:
        body = {"state": PairingState.PAIRED.value, "board_url": home_for(device)}
    elif (
        device is None
        or device.revoked_at is not None
        or device.pairing_expires_at is None
        or stored_sast(device.pairing_expires_at) <= now_sast()
    ):
        body = {"state": PairingState.EXPIRED.value}
    else:
        body = {"state": PairingState.WAITING.value}
    return JSONResponse(body, headers=NO_STORE)


@router.post("/heartbeat")
def heartbeat(payload: HeartbeatIn, request: Request, db: DbSession) -> Response:
    """A paired box reports in (every minute): 204 and a renewed cookie, or 401 for a box that may not show a board.

    Refused when sent from another site (``Sec-Fetch-Site: cross-site``), and the cookie is same-site
    only, so a page elsewhere cannot keep a box looking alive.
    """
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    secret = request.cookies.get(get_settings().display_device_cookie_name)
    device = devices.device_for_secret(db, secret)
    if not devices.is_paired(device) or device is None or secret is None:
        return _unauthorised()
    devices.record_heartbeat(
        db,
        device,
        app_version=payload.app_version,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    response = JSONResponse(
        {"state": PairingState.PAIRED.value, "version": get_settings().version},
        headers=NO_STORE,
    )
    set_device_cookie(response, secret)
    return response


@router.get("/{site_id}/state")
def board_state(site_id: str, request: Request, db: DbSession) -> Response:
    """The board as JSON: the privacy projection's payload and nothing else (Issue 58)."""
    audience = board_audience(request, db, site_id)
    if audience is None:
        return _unauthorised()
    site = displayed_site(db, site_id)
    if site is None:
        raise site_not_found()
    state = project_board(
        db, site, viewer=audience.viewer, queue_ids=audience.queue_ids
    )
    return JSONResponse(state.payload(), headers=NO_STORE)


@router.get("/{site_id}", response_class=HTMLResponse)
def board_page(site_id: str, request: Request, db: DbSession) -> Response:
    """The waiting-room board: now serving and up next per queue, a clock and the clinic (Issue 56).

    The page carries the projected payload it first draws, so the screen is full the moment it loads;
    ``board-live.js`` then follows the live stream (Issue 57), falling back to ``/state`` every
    :data:`POLL_SECONDS` while the stream is down. Nobody who may see this board is sent to the start
    page (Issue 61); a clinic with no public board gets a plain "not available" screen with the same 404
    an unknown id gets.
    """
    audience = board_audience(request, db, site_id)
    if audience is None:
        return RedirectResponse(START_PATH, status_code=status.HTTP_302_FOUND)
    site = displayed_site(db, site_id)
    if site is None:
        return board_template_response(
            request,
            "display/unavailable.html",
            {"page_title": "Board not available"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    state = project_board(
        db, site, viewer=audience.viewer, queue_ids=audience.queue_ids
    )
    message_language, messages = health_messages(state.language)
    # What the board says aloud (Issue 60): sentences with a number and a room to fill, never a name.
    phrase = announcement_for(state.language)
    fallback = announcement_for(FALLBACK_LANGUAGE)
    return board_template_response(
        request,
        "display/board.html",
        {
            "page_title": state.clinic_name,
            "board": state,
            "payload": state.payload(),
            "state_url": request.url_for("board_state", site_id=site.id).path,
            "stream_url": request.url_for("board_stream", site_id=site.id).path,
            "heartbeat_seconds": BOARD_HEARTBEAT_SECONDS,
            "messages": messages if get_settings().board_health_ticker else (),
            "message_language": message_language,
            "poll_seconds": POLL_SECONDS,
            "highlight_seconds": HIGHLIGHT_SECONDS,
            "panels_per_page": PANELS_PER_PAGE,
            "page_seconds": PAGE_SECONDS,
            "message_seconds": MESSAGE_SECONDS,
            # A paired box reports in every minute and follows a new release; a staff preview does not.
            "device_heartbeat_url": request.url_for("heartbeat").path
            if audience.viewer is BoardViewer.DEVICE
            else "",
            "device_heartbeat_seconds": DEVICE_HEARTBEAT_SECONDS,
            # Only a paired box keeps its board for offline use (Issue 62); a staff preview keeps nothing.
            "offline_worker_url": request.url_for("board_service_worker").path
            if audience.viewer is BoardViewer.DEVICE
            else "",
            "stale_after_seconds": STALE_AFTER_SECONDS,
            "stale_limit_seconds": STALE_LIMIT_SECONDS,
            "start_url": START_PATH,
            "app_version": get_settings().version,
            "announce_call": phrase.call,
            "announce_voice": phrase.voice_tag,
            "announce_language": phrase.language,
            "announce_fallback_call": fallback.call,
            "announce_fallback_voice": fallback.voice_tag,
            "announce_clips": number_clips(phrase.language),
            "announce_chime": CHIME_PATH,
        },
    )
