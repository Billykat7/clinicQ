"""The waiting-room board's routes (M8): public, read-only, and fed only by the privacy projection.

Every response here is built from :func:`~src.modules.display.projection.project_board` and nothing
else (non-negotiable 4, Issue 58):

* ``GET /display/{site_id}`` is the board page (Issue 56): a full-screen kiosk page with its own
  stylesheet and script, handed the same payload to draw from;
* ``GET /display/{site_id}/state`` is the board as JSON: :meth:`BoardState.payload`, which has no
  ``name`` or ``comment`` key at all unless the mode and the patient's consent put a value in it;
* a board template is rendered only through :func:`board_template_response`, which checks its whole
  context with :func:`~src.modules.display.projection.ensure_projected` first, so handing a template
  a ticket or a patient is an error, never a page.

**No sign-in.** A board is a public screen, so these routes need no session. They do look for one:
somebody signed in who works at this clinic sees the board under the site's own display mode (a
manager checking the screen), and everyone else sees numbers only
(:class:`~src.modules.display.enums.BoardViewer`). A clinic that is unknown, hidden or not yet
verified answers the same 404 as one that never existed.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.nav_visibility import peek_user_from_refresh_cookie
from src.core.site_scope import permitted_site_ids, site_not_found
from src.database.models import Site
from src.database.session import get_db
from src.modules.display.enums import BoardViewer
from src.modules.display.messages import health_messages
from src.modules.display.projection import (
    BoardState,
    displayed_site,
    ensure_projected,
    project_board,
)
from src.web.routes import templates

router = APIRouter(prefix="/display", include_in_schema=False)

DbSession = Annotated[Session, Depends(get_db)]

#: A board's answers change with every call, and may carry a name under a wider mode: never cached.
NO_STORE: dict[str, str] = {"Cache-Control": "no-store"}

#: How often the page asks for the board again while it has no live stream (Issue 57 adds one).
POLL_SECONDS: Final = 10
#: How long a newly called ticket stays highlighted, counted from the call on the server's clock.
HIGHLIGHT_SECONDS: Final = 20
#: How many queue panels one screen holds. A clinic with more shows them in pages of this many.
PANELS_PER_PAGE: Final = 4
#: How long each page of panels stays up before the next, when there is more than one.
PAGE_SECONDS: Final = 15
#: How long each health notice stays up before the next.
MESSAGE_SECONDS: Final = 12


def board_viewer(request: Request, db: Session, site: Site) -> BoardViewer:
    """Who the response is for: staff of this clinic, or anyone at all.

    Read from the refresh cookie the signed-in shell already uses. Somebody signed in at another
    clinic is, for this board, anyone at all.
    """
    user = peek_user_from_refresh_cookie(db, request)
    if user is not None and site.id in permitted_site_ids(db, user):
        return BoardViewer.STAFF
    return BoardViewer.ANONYMOUS


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


def _state(request: Request, db: Session, site_id: str) -> BoardState:
    """The projected board for ``site_id`` as this request may see it, or the board's 404."""
    site = displayed_site(db, site_id)
    if site is None:
        raise site_not_found()
    return project_board(db, site, viewer=board_viewer(request, db, site))


@router.get("/{site_id}/state")
def board_state(site_id: str, request: Request, db: DbSession) -> Response:
    """The board as JSON: the privacy projection's payload and nothing else (Issue 58)."""
    return JSONResponse(_state(request, db, site_id).payload(), headers=NO_STORE)


@router.get("/{site_id}", response_class=HTMLResponse)
def board_page(site_id: str, request: Request, db: DbSession) -> Response:
    """The waiting-room board: now serving and up next per queue, a clock and the clinic (Issue 56).

    The page carries the projected payload it first draws, so the screen is full the moment it loads,
    and ``board.js`` asks ``/state`` again every :data:`POLL_SECONDS`. A clinic with no public board
    gets a plain "not available" screen with the same 404 an unknown id gets.
    """
    site = displayed_site(db, site_id)
    if site is None:
        return board_template_response(
            request,
            "display/unavailable.html",
            {"page_title": "Board not available"},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    state = project_board(db, site, viewer=board_viewer(request, db, site))
    message_language, messages = health_messages(state.language)
    return board_template_response(
        request,
        "display/board.html",
        {
            "page_title": state.clinic_name,
            "board": state,
            "payload": state.payload(),
            "state_url": request.url_for("board_state", site_id=site.id).path,
            "messages": messages if get_settings().board_health_ticker else (),
            "message_language": message_language,
            "poll_seconds": POLL_SECONDS,
            "highlight_seconds": HIGHLIGHT_SECONDS,
            "panels_per_page": PANELS_PER_PAGE,
            "page_seconds": PAGE_SECONDS,
            "message_seconds": MESSAGE_SECONDS,
        },
    )
