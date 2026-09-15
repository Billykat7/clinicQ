"""The check-in tablet's pages, on a paired device at the clinic's door (Issue 83).

The tablet is provisioned exactly like a waiting-room board: it opens ``/display``, shows a six-character
code, and a manager pairs it from the dashboard, choosing **check-in tablet** instead of board (Issue 61).
From then on ``/display`` sends it here, and these pages are what it shows all day. The device cookie is
the credential, so the addresses below are useless in anybody else's browser, and the tablet is signed in
as nobody: it can neither see a patient's name nor list who is waiting.

* ``GET /display/check-in`` is the idle screen: a big "I am here" instruction, a hidden field a USB or
  camera scanner types the QR into, a digits-only keypad for a phone number, and, where the clinic allows
  it, the open queues as buttons.
* ``POST /display/check-in/arrivals`` takes what was scanned or typed and answers with the number, the
  queue, the room and how many are ahead — the same facts the waiting-room board shows everyone.
* ``POST /display/check-in/walk-ins`` starts a walk-in at the door, where the clinic allows it.
* ``GET /display/check-in/state`` is the heartbeat the page uses to notice it has gone offline. When it
  fails, the screen says "please see reception" instead of pretending to work.

**Nothing personal stays on the screen.** The answer is shown for :data:`CLEAR_SECONDS` and the page then
returns to idle by itself, so the next patient in the door reads nothing about the last one. Every answer
is ``no-store``, and nothing is written to the tablet's storage.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import DisplayDeviceKind, JoinRefusal
from src.commons.exceptions import BKPropertyError
from src.commons.time import now_sast
from src.core.config import get_settings
from src.database.models import DisplayDevice, Queue, Site
from src.database.session import get_db
from src.modules.appointments import checkin
from src.modules.display import devices
from src.modules.display.enums import PairingState
from src.modules.queue.service import JoinRefusedError
from src.modules.sites.hours import published_schedules
from src.web.display import (
    CHECK_IN_PATH,
    NO_STORE,
    START_PATH,
    device_from_cookie,
    set_device_cookie,
)
from src.web.routes import templates

router = APIRouter(prefix=CHECK_IN_PATH, include_in_schema=False)

DbSession = Annotated[Session, Depends(get_db)]

#: How long an answer stays on the screen before the tablet goes back to idle, in seconds. Long enough
#: to read a number aloud to a companion, short enough that the next patient sees nothing of it.
CLEAR_SECONDS: Final = 20
#: How often the page checks that the clinic is still reachable, in seconds.
PING_SECONDS: Final = 15
#: What the screen says when nothing can be reached. The clinic's own people are the fallback, always.
OFFLINE_SENTENCE: Final = "This screen is offline. Please see reception."


class ArrivalIn(BaseModel):
    """What the tablet read: a scanned QR, a typed reference, or a phone number."""

    code: str = Field(min_length=1, max_length=32)


class WalkInIn(BaseModel):
    """Starting a walk-in at the door, where the clinic allows it."""

    queue_id: str = Field(min_length=1, max_length=36)
    phone: str | None = Field(default=None, max_length=20)
    notifications_consent: bool = False


def _paired_check_in(request: Request, db: Session) -> DisplayDevice | None:
    """The check-in tablet this request comes from, or ``None`` for anything else."""
    device = device_from_cookie(request, db)
    if (
        device is None
        or not devices.is_paired(device)
        or device.kind_enum is not DisplayDeviceKind.CHECK_IN
    ):
        return None
    return device


def _refused(error: BKPropertyError) -> Response:
    """One refusal, in the patient's own words, with the machine-readable code beside it."""
    return JSONResponse(
        {"detail": str(error), "code": error.code},
        status_code=error.status_code,
        headers=NO_STORE,
    )


def _join_refused(error: JoinRefusedError) -> Response:
    """A queue that cannot take the ticket now: the tablet says so and sends the patient to reception."""
    sentence = (
        "This queue is full for today. Please see reception."
        if error.refusal is JoinRefusal.QUEUE_FULL
        else f"{error} Please see reception."
    )
    return JSONResponse(
        {"detail": sentence, "code": f"queue.join.{error.refusal.value}"},
        status_code=status.HTTP_409_CONFLICT,
        headers=NO_STORE,
    )


def _open_queues(db: Session, site_id: str) -> list[Queue]:
    """The clinic's queues a patient may join at the door, in the order the board shows them."""
    return list(
        db.execute(
            select(Queue)
            .where(
                Queue.site_id == site_id,
                Queue.is_active.is_(True),
                Queue.is_deleted.is_(False),
            )
            .order_by(Queue.display_order, Queue.name)
        ).scalars()
    )


def _answer(found: checkin.CheckedIn) -> Response:
    """What the screen shows: a number, its queue and room, and how many are ahead."""
    return JSONResponse(
        {
            "number": found.number,
            "queue_name": found.queue_name,
            "room_label": found.room_label,
            "waiting_ahead": found.waiting_ahead,
            "wait_label": found.wait_label,
            "already": found.already,
            "from_booking": found.from_booking,
            "message": _sentence(found),
            "clear_seconds": CLEAR_SECONDS,
        },
        headers=NO_STORE,
    )


def _sentence(found: checkin.CheckedIn) -> str:
    """The one line the screen leads with, in the words a patient at the door needs."""
    where = f"{found.queue_name}" + (
        f", {found.room_label}" if found.room_label else ""
    )
    if found.already:
        return f"You are already checked in. Your number is {found.number}, {where}."
    if found.from_booking:
        return f"Checked in. Your number is {found.number}, {where}."
    return f"Thank you. Your number is {found.number}, {where}."


def _renew_cookie(request: Request, response: Response) -> None:
    """Renew the tablet's cookie on every check-in, so a screen in daily use never has to be paired again."""
    secret = request.cookies.get(get_settings().display_device_cookie_name)
    if secret:
        set_device_cookie(response, secret)


@router.get("", response_class=HTMLResponse)
def check_in_page(request: Request, db: DbSession) -> Response:
    """The idle screen: what to do, a field the scanner types into, and a keypad (Issue 83)."""
    device = _paired_check_in(request, db)
    if device is None:
        return RedirectResponse(START_PATH, status_code=status.HTTP_302_FOUND)
    site = db.get(Site, device.site_id)
    if site is None or site.is_deleted:
        return RedirectResponse(START_PATH, status_code=status.HTTP_302_FOUND)
    queues = _open_queues(db, site.id) if site.kiosk_walk_ins_enabled else []
    settings = get_settings()
    response = templates.TemplateResponse(
        request,
        "kiosk/checkin.html",
        {
            # The page frame's own needs (base.html): configuration, never clinic data.
            "settings": settings,
            "app_name": settings.app_name,
            "page_title": "Check in",
            "clinic_name": site.name,
            "walk_ins": [
                {"id": queue.id, "name": queue.name, "room": queue.room_label}
                for queue in queues
            ],
            "arrivals_url": f"{CHECK_IN_PATH}/arrivals",
            "walk_ins_url": f"{CHECK_IN_PATH}/walk-ins",
            "state_url": f"{CHECK_IN_PATH}/state",
            "clear_seconds": CLEAR_SECONDS,
            "ping_seconds": PING_SECONDS,
            "offline_sentence": OFFLINE_SENTENCE,
        },
    )
    response.headers.update(NO_STORE)
    return response


@router.get("/state")
def check_in_state(request: Request, db: DbSession) -> Response:
    """Whether the tablet is still paired and the clinic still reachable; its offline check."""
    device = _paired_check_in(request, db)
    if device is None:
        return JSONResponse(
            {"detail": "This tablet is not paired with a clinic."},
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers=NO_STORE,
        )
    devices.record_heartbeat(db, device, app_version=None, user_agent=None)
    db.commit()
    return JSONResponse({"state": PairingState.PAIRED.value}, headers=NO_STORE)


@router.post("/arrivals")
def arrive(payload: ArrivalIn, request: Request, db: DbSession) -> Response:
    """Check a patient in from what the tablet read, or say why not (Issue 83).

    Refused from another site (``Sec-Fetch-Site: cross-site``): the cookie is same-site only, so nothing
    on the web can check patients in through a tablet's browser.
    """
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    device = _paired_check_in(request, db)
    if device is None or device.site_id is None:
        return JSONResponse(
            {"detail": OFFLINE_SENTENCE},
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers=NO_STORE,
        )
    site = db.get(Site, device.site_id)
    if site is None or site.is_deleted:
        return _refused(checkin.CheckInNotFoundError())
    moment = now_sast()
    schedule = published_schedules(db, [site.id], from_day=moment.date())[site.id]
    try:
        found = checkin.check_in(db, site, schedule, payload.code, moment=moment)
    except JoinRefusedError as refused:
        db.rollback()
        return _join_refused(refused)
    except BKPropertyError as error:
        db.rollback()
        return _refused(error)
    db.commit()
    response = _answer(found)
    _renew_cookie(request, response)
    return response


@router.post("/walk-ins")
def walk_in(payload: WalkInIn, request: Request, db: DbSession) -> Response:
    """Start a walk-in at the door, where the clinic allows it."""
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    device = _paired_check_in(request, db)
    if device is None or device.site_id is None:
        return JSONResponse(
            {"detail": OFFLINE_SENTENCE},
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers=NO_STORE,
        )
    site = db.get(Site, device.site_id)
    queue = db.get(Queue, payload.queue_id)
    if site is None or site.is_deleted or queue is None or queue.site_id != site.id:
        return _refused(checkin.CheckInNotFoundError())
    moment = now_sast()
    schedule = published_schedules(db, [site.id], from_day=moment.date())[site.id]
    try:
        found = checkin.start_walk_in(
            db,
            site,
            queue,
            schedule,
            phone=payload.phone,
            notifications_consent=payload.notifications_consent,
            moment=moment,
        )
    except JoinRefusedError as refused:
        db.rollback()
        return _join_refused(refused)
    except BKPropertyError as error:
        db.rollback()
        return _refused(error)
    db.commit()
    return _answer(found)
