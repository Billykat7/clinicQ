"""Development-only pages (Issue 5): the component catalogue and a sample of each layout.

* ``/dev/components`` documents every component macro: what it renders, and how to call it.
* ``/dev/layouts/{patient|dashboard|board}`` shows each layout filled with realistic content, the
  same component set and tokens behind all three. Each is its own URL (the list-view rule in
  ``.cursor/rules``); the bare ``/dev/layouts`` and an unknown layout redirect to the patient one.
* ``/dev/fragments/*`` are the htmx fragments those pages swap in: a queue refresh that also
  raises a toast from the server, and stat tiles that lazy-load behind a skeleton.

The router is included by :func:`src.main.create_app` **only in development**, so in staging and
production these paths do not exist at all (404), rather than existing behind a check. The data is
made up on the spot from the enums; nothing here reads the database.
"""

from datetime import timedelta
from enum import StrEnum
from typing import NamedTuple

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.commons.enums import TicketSource, TicketStatus
from src.commons.time import now_sast
from src.web.components import ToastKind, toast_trigger
from src.web.context import public_page_context
from src.web.routes import templates

router = APIRouter(prefix="/dev", include_in_schema=False)

#: The request header htmx sends on every request it makes.
HTMX_REQUEST_HEADER = "HX-Request"


class DevLayout(StrEnum):
    """The three layouts, as the last segment of ``/dev/layouts/{layout}``."""

    PATIENT = "patient"
    DASHBOARD = "dashboard"
    BOARD = "board"


#: Where ``/dev/layouts`` and an unknown layout land.
DEFAULT_LAYOUT = DevLayout.PATIENT


class DemoTicket(NamedTuple):
    """One made-up ticket, in the shape the queue screens of M6 to M8 will read."""

    number: str
    status: TicketStatus
    source: TicketSource
    minutes_waiting: int
    room: str | None = None


def _demo_queue() -> list[DemoTicket]:
    """A morning at a Gauteng clinic's general queue: one being seen, one called, the rest waiting."""
    return [
        DemoTicket(
            "A011", TicketStatus.IN_PROGRESS, TicketSource.WALK_IN, 42, "Room 2"
        ),
        DemoTicket("A012", TicketStatus.CALLED, TicketSource.WEB, 37, "Room 3"),
        DemoTicket("A013", TicketStatus.WAITING, TicketSource.USSD, 31),
        DemoTicket("A014", TicketStatus.WAITING, TicketSource.WHATSAPP, 24),
        DemoTicket("A015", TicketStatus.RECALLED, TicketSource.WEB, 22),
        DemoTicket("A016", TicketStatus.WAITING, TicketSource.WALK_IN, 15),
        DemoTicket("A017", TicketStatus.WAITING, TicketSource.WEB, 9),
        DemoTicket("A009", TicketStatus.NO_SHOW, TicketSource.USSD, 58),
    ]


def _is_htmx(request: Request) -> bool:
    """True when htmx made the request (a fragment was asked for, not a page)."""
    return request.headers.get(HTMX_REQUEST_HEADER) == "true"


@router.get("", response_class=RedirectResponse)
def dev_index() -> RedirectResponse:
    """``/dev`` has nothing of its own: go to the catalogue."""
    return RedirectResponse("/dev/components", status_code=status.HTTP_302_FOUND)


@router.get("/components", response_class=HTMLResponse)
def components_catalogue(request: Request) -> HTMLResponse:
    """The component catalogue: each macro rendered, with the call that renders it."""
    ctx = public_page_context(
        request,
        page_title="Components",
        layouts=list(DevLayout),
        queue=_demo_queue(),
        now=now_sast(),
        statuses=list(TicketStatus),
    )
    return templates.TemplateResponse(request, "dev/components.html", ctx)


@router.get("/layouts", response_class=RedirectResponse)
def layouts_index() -> RedirectResponse:
    """The bare layouts URL redirects to the default layout's sample."""
    return RedirectResponse(
        f"/dev/layouts/{DEFAULT_LAYOUT}", status_code=status.HTTP_302_FOUND
    )


@router.get("/layouts/{layout}", response_class=HTMLResponse)
def layout_sample(request: Request, layout: str) -> Response:
    """One layout, filled with the screen it exists for. An unknown layout redirects."""
    if layout not in DevLayout:
        return RedirectResponse(
            f"/dev/layouts/{DEFAULT_LAYOUT}", status_code=status.HTTP_302_FOUND
        )
    queue = _demo_queue()
    now = now_sast()
    ctx = public_page_context(
        request,
        page_title=f"{DevLayout(layout).title()} layout",
        layouts=list(DevLayout),
        current_layout=DevLayout(layout),
        queue=queue,
        now=now,
        serving=next(t for t in queue if t.status is TicketStatus.CALLED),
        up_next=[t for t in queue if t.status is TicketStatus.WAITING][:4],
        mine=queue[-2],
        ahead=sum(1 for t in queue if t.status is TicketStatus.WAITING) - 1,
        eta=now + timedelta(minutes=20),
    )
    return templates.TemplateResponse(request, f"dev/layout_{layout}.html", ctx)


@router.get("/fragments/queue", response_class=HTMLResponse)
def queue_fragment(request: Request) -> Response:
    """The queue table's rows, for an htmx swap, plus a toast raised by the server.

    Asked for by anything but htmx (a bookmark, a refresh), it redirects to the page it belongs
    to, so nobody lands on a bare fragment.
    """
    if not _is_htmx(request):
        return RedirectResponse(
            f"/dev/layouts/{DevLayout.DASHBOARD}", status_code=status.HTTP_303_SEE_OTHER
        )
    now = now_sast()
    response = templates.TemplateResponse(
        request,
        "dev/fragments/queue_rows.html",
        {"queue": _demo_queue(), "now": now},
    )
    response.headers.update(
        toast_trigger(f"Queue refreshed at {now:%H:%M:%S}", ToastKind.OK)
    )
    return response


@router.get("/fragments/stats", response_class=HTMLResponse)
def stats_fragment(request: Request) -> Response:
    """The dashboard's stat tiles, lazy-loaded over a skeleton by ``hx-trigger="load"``."""
    if not _is_htmx(request):
        return RedirectResponse(
            f"/dev/layouts/{DevLayout.DASHBOARD}", status_code=status.HTTP_303_SEE_OTHER
        )
    queue = _demo_queue()
    waiting = [t for t in queue if t.status is TicketStatus.WAITING]
    return templates.TemplateResponse(
        request,
        "dev/fragments/stats.html",
        {
            "waiting": len(waiting),
            "longest_wait": max(t.minutes_waiting for t in waiting),
            "no_shows": sum(1 for t in queue if t.status is TicketStatus.NO_SHOW),
            "remote_share": round(
                100
                * sum(1 for t in queue if t.source is not TicketSource.WALK_IN)
                / len(queue)
            ),
        },
    )
