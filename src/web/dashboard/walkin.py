"""Walk-in intake at the front desk, its recent list and the printable ticket stub (Issue 51).

Most patients at a public clinic still walk in. Intake has to be faster than writing a name on a paper
list, so the page is built for the keyboard: the name field has focus, the queue last used is chosen
already, and Enter issues the ticket. Everything the page does is a request to the queue API
(``POST /sites/{site}/queues/{queue}/tickets``, Issue 40's join with ``source=walk_in``), so there is no
separate walk-in path: a walk-in takes the next number in the same sequence as a phone join.

The page renders, and reads nothing a site guard has not narrowed:

* the clinic's queues open to joins, with how many are waiting in each (Issue 49's cards);
* the walk-ins **this person** issued today, newest first, and until when the last can be undone
  (:func:`src.modules.queue.walk_ins.recent_walk_ins`), refreshed on its own after every issue and undo;
* a plain **ticket stub** per ticket: a print stylesheet for a 58 mm thermal printer, which is also a
  large on-screen number to show the patient when no printer is attached. It carries no name, phone or
  reason, because a stub is left on chairs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from src.commons.enums import TICKET_ACTIVE_STATUSES, ConsentPurpose
from src.commons.time import now_sast, stored_sast
from src.core.config import get_settings
from src.core.nav_registry import destination
from src.core.site_scope import scoped_select
from src.database.models import Queue, Ticket
from src.database.models.ticket import MAX_REASON_LENGTH
from src.database.session import get_db
from src.modules.patients.consent_text import CONSENT_WORDING
from src.modules.queue.estimate import WaitEstimate
from src.modules.queue.sequence import format_reference_code
from src.modules.queue.service import describe_ticket
from src.modules.queue.ticket_codes import TicketQr, qr_for, spoken
from src.modules.queue.walk_ins import RecentWalkIn, recent_walk_ins
from src.web.context import require_authenticated_html
from src.web.dashboard.board import BoardCard, read_board
from src.web.dashboard.routes import (
    ClinicPage,
    _visible,
    open_clinic_page,
    render_clinic_page,
)
from src.web.routes import _not_found_html, templates

router = APIRouter(tags=["web"])

#: The nav destination the page and its parts are gated on.
WALK_IN_KEY = "walk_in"


@dataclass(frozen=True, slots=True)
class WalkInView:
    """What the intake page renders."""

    queues: tuple[BoardCard, ...]
    recent: tuple[RecentWalkIn, ...]
    as_of: datetime
    #: How long the last walk-in can be undone, in words (``2 minutes``).
    undo_window: str
    #: The words the desk reads out before ticking the notifications box (Issue 21's wording).
    notifications_wording: str
    reason_max_length: int


def _duration(seconds: int) -> str:
    """``120`` as ``2 minutes``, ``90`` as ``90 seconds``: the undo window as the desk reads it."""
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds} seconds"


def _standing(waiting_ahead: int) -> str:
    """Where the patient stands, in the words the stub prints."""
    if waiting_ahead == 0:
        return "You are next"
    if waiting_ahead == 1:
        return "1 person ahead of you"
    return f"{waiting_ahead} people ahead of you"


def read_walk_in(db: Session, page: ClinicPage) -> WalkInView:
    """The queues a walk-in can join here, and the caller's recent walk-ins."""
    now = now_sast()
    board = read_board(db, page.access, moment=now)
    return WalkInView(
        queues=tuple(card for card in board.cards if card.is_active),
        recent=recent_walk_ins(db, page.access, moment=now),
        as_of=now,
        undo_window=_duration(get_settings().queue_walk_in_undo_seconds),
        notifications_wording=CONSENT_WORDING[ConsentPurpose.NOTIFICATIONS],
        reason_max_length=MAX_REASON_LENGTH,
    )


def _open(request: Request, db: Session, site_id: str) -> ClinicPage | Response:
    return open_clinic_page(
        request,
        db,
        site_id,
        active_key=WALK_IN_KEY,
        page_title=destination(WALK_IN_KEY).label,
        allowed=_visible(WALK_IN_KEY),
    )


@router.get("/dashboard/sites/{site_id}/walk-in", response_class=HTMLResponse)
async def walk_in_page(
    site_id: str, request: Request, db: Session = Depends(get_db)
) -> Response:
    """The intake form, the ticket just issued, and the caller's recent walk-ins."""
    opened = _open(request, db, site_id)
    if not isinstance(opened, ClinicPage):
        return opened
    opened.context["walk_in"] = read_walk_in(db, opened)
    return render_clinic_page(request, opened, "dashboard/walkin.html")


@router.get("/dashboard/sites/{site_id}/walk-in/recent", response_class=HTMLResponse)
async def walk_in_recent(
    site_id: str, request: Request, db: Session = Depends(get_db)
) -> Response:
    """Just the recent list, for the page to swap in after an issue or an undo.

    ``401`` rather than a redirect when signed out: the caller is the page's script.
    """
    if not require_authenticated_html(request, db):
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    opened = _open(request, db, site_id)
    if not isinstance(opened, ClinicPage):
        return opened
    opened.context["walk_in"] = read_walk_in(db, opened)
    response = templates.TemplateResponse(
        request, "dashboard/_walkin_recent.html", opened.context
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@dataclass(frozen=True, slots=True)
class TicketStub:
    """What a ticket stub shows: nothing that names the patient."""

    number: str
    reference_code: str
    #: The QR and the spoken code (Issue 70): the same code the patient's ticket page shows.
    reference_qr: TicketQr
    reference_spoken: str
    clinic: str
    queue: str
    room_label: str | None
    standing: str
    wait: WaitEstimate
    issued_at: datetime


@router.get(
    "/dashboard/sites/{site_id}/walk-in/tickets/{ticket_id}/stub",
    response_class=HTMLResponse,
)
async def ticket_stub(
    site_id: str, ticket_id: str, request: Request, db: Session = Depends(get_db)
) -> Response:
    """A plain page for one ticket still in the day: printed on 58 mm paper, or shown on screen.

    Gated like the intake page; another clinic's ticket, or one no longer in the day, is not found.
    """
    opened = _open(request, db, site_id)
    if not isinstance(opened, ClinicPage):
        return opened
    ticket = db.execute(
        scoped_select(Ticket, opened.access).where(
            Ticket.id == ticket_id,
            Ticket.status.in_([s.value for s in TICKET_ACTIVE_STATUSES]),
        )
    ).scalar_one_or_none()
    queue = (
        db.execute(
            scoped_select(Queue, opened.access).where(Queue.id == ticket.queue_id)
        ).scalar_one_or_none()
        if ticket is not None
        else None
    )
    if ticket is None or queue is None:
        return _not_found_html(request, db)
    standing = describe_ticket(db, queue, ticket)
    opened.context["stub"] = TicketStub(
        number=ticket.number,
        reference_code=format_reference_code(ticket.reference_code),
        reference_qr=qr_for(ticket.reference_code),
        reference_spoken=spoken(ticket.reference_code),
        clinic=opened.shell.site.name,
        queue=queue.name,
        room_label=queue.room_label,
        standing=_standing(standing.waiting_ahead),
        wait=standing.wait,
        issued_at=stored_sast(ticket.joined_at),
    )
    opened.context["back_href"] = destination(WALK_IN_KEY).href_at(site_id)
    response = templates.TemplateResponse(
        request, "print/ticket_stub.html", opened.context
    )
    response.headers["Cache-Control"] = "no-store"
    return response
