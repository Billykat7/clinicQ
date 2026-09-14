"""The waiting-room board's routes (M8): public, read-only, and fed only by the privacy projection.

Every response here is built from :func:`~src.modules.display.projection.project_board` and nothing
else (non-negotiable 4, Issue 58):

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

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from src.core.nav_visibility import peek_user_from_refresh_cookie
from src.core.site_scope import permitted_site_ids, site_not_found
from src.database.models import Site
from src.database.session import get_db
from src.modules.display.enums import BoardViewer
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
    request: Request, name: str, context: dict[str, Any]
) -> Response:
    """Render a board template from projected data only.

    ``context`` is checked with :func:`ensure_projected` before the template is looked up, so a raw
    ``Ticket``, ``Patient`` or ``Site`` anywhere in it raises
    :class:`~src.modules.display.projection.UnprojectedBoardDataError` and nothing is rendered.
    """
    ensure_projected(context)
    response = templates.TemplateResponse(request, name, context)
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
