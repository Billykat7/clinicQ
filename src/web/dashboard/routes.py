"""The clinic dashboard's pages: a clinic's home, the front desk, a room and the settings (Issue 48).

Every page here is one clinic's, at ``/dashboard/sites/{site_id}/…``, and opens through
:func:`open_clinic_page`, which applies the rules in the order that keeps each answer honest:

1. **Signed in?** A signed-out visitor is sent to sign in with the page as ``next``, and lands back on
   it afterwards (``login-modal.js`` follows ``next`` when it is a local path).
2. **Your clinic?** A clinic the caller holds no role at renders the not-found page, the same as an id
   that does not exist, so a status code cannot confirm another clinic's id (non-negotiable 3).
3. **Your screen?** The page's gate, evaluated with the roles held **at this clinic**: a caller who
   works here but may not open this screen gets the honest 403 inside the shell.

The pages render no rule of their own and read nothing a site guard has not narrowed. The JSON API
each screen calls re-checks its grant, because this gate governs what is offered and the API's
governs what is done.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from sqlalchemy.orm import Session

from src.commons.enums import PermissionVerb, PriorityReason
from src.commons.time import business_date
from src.core.live_events import (
    EVENT_STREAM_MEDIA_TYPE,
    STREAM_HEADERS,
    TooManySubscribersError,
    broker,
)
from src.core.nav_registry import all_destination_keys, destination
from src.core.nav_visibility import (
    NavVisibility,
    nav_visibility_at_site,
    peek_user_from_refresh_cookie,
)
from src.core.site_scope import SiteAccess, permitted_queue_ids
from src.database.models import User
from src.database.models.queue_reorder import MAX_REORDER_NOTE_LENGTH
from src.database.session import get_db
from src.modules.queues.service import list_queues
from src.web.context import page_context, require_authenticated_html
from src.web.dashboard.board import read_board
from src.web.dashboard.reorder import (
    PRIORITY_RESOURCE,
    OverrideFilters,
    override_day,
    queue_lines,
    reason_choices,
)
from src.web.dashboard.shell import (
    SECTION_PARAM,
    ClinicShell,
    build_shell,
    first_open_destination,
    remember_site,
    staff_sites,
)
from src.web.routes import (
    _forbidden_html,
    _not_found_html,
    _redirect_to_sign_in,
    templates,
)

router = APIRouter(tags=["web"])

DbSession = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True, slots=True)
class ClinicPage:
    """An opened clinic page: the frame, the template context and the guard's access."""

    shell: ClinicShell
    context: dict[str, object]
    access: SiteAccess


def open_clinic_page(
    request: Request,
    db: Session,
    site_id: str,
    *,
    active_key: str,
    page_title: str,
    allowed: Callable[[NavVisibility], bool],
) -> ClinicPage | Response:
    """Open one clinic's page for the caller, or return the response that refuses it.

    Args:
        request: The page request.
        db: The session.
        site_id: The clinic in the path.
        active_key: The nav destination this page belongs to, highlighted in the frame.
        page_title: The tab title.
        allowed: The page's gate, given the caller's visibility **at this clinic**.

    Returns:
        The opened page, or a redirect to sign in, the not-found page or the 403 page.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)
    user = peek_user_from_refresh_cookie(db, request)
    sites = staff_sites(db, user) if user is not None else []
    site = next((candidate for candidate in sites if candidate.id == site_id), None)
    if user is None or site is None:
        return _not_found_html(request, db)
    shell = build_shell(db, user, site, sites, active_key=active_key)
    if not allowed(shell.nav):
        return _forbidden_html(request, db)
    context = page_context(
        request,
        db,
        nav=shell.nav,
        clinic=shell,
        active_nav=active_key,
        page_title=page_title,
        site=site,
        breadcrumbs=clinic_breadcrumbs(shell, active_key, page_title),
    )
    return ClinicPage(
        shell=shell, context=context, access=SiteAccess(site_id=site.id, user=user)
    )


def clinic_breadcrumbs(
    shell: ClinicShell, active_key: str, page_title: str
) -> list[dict[str, object]]:
    """The trail under home: the clinic, the screen's section when the page is below it, the page.

    Built here rather than by :func:`~src.web.context.default_breadcrumbs`, because a clinic
    destination's link names the clinic and the registry's ``href`` only carries ``{site_id}``.
    """
    trail: list[dict[str, object]] = [
        {"label": shell.site.name, "href": shell.home_href}
    ]
    section = destination(active_key) if active_key else None
    if section is not None and section.label != page_title:
        trail.append({"label": section.label, "href": section.href_at(shell.site.id)})
    trail.append({"label": page_title, "current": True})
    return trail


def render_clinic_page(request: Request, page: ClinicPage, template: str) -> Response:
    """Render ``template`` for an opened page and remember its clinic for ``/dashboard``."""
    response = templates.TemplateResponse(request, template, page.context)
    remember_site(response, page.shell.site.id)
    return response


def _visible(key: str) -> Callable[[NavVisibility], bool]:
    """The gate of the registry destination ``key``: the same check that renders its link."""
    return lambda nav: nav.visible(key)


@router.get("/dashboard/sites/{site_id}", response_class=HTMLResponse)
async def clinic_home(
    site_id: str,
    request: Request,
    db: DbSession,
    section: Annotated[str | None, Query(alias=SECTION_PARAM, max_length=40)] = None,
) -> Response:
    """A clinic's home: the screen ``section`` names if the caller may open it here, else the first.

    The site switcher links here, so switching from the front desk at one clinic reopens the front
    desk at the other, and a screen the caller has no grant for at the other clinic falls back to the
    first one they do, rather than to a 403 they did not ask for. A caller who works at the clinic but
    may open none of its screens gets the 403 page.
    """
    opened = open_clinic_page(
        request, db, site_id, active_key="", page_title="Clinic", allowed=lambda _: True
    )
    if not isinstance(opened, ClinicPage):
        return opened
    nav = opened.shell.nav
    if (
        section is not None
        and section in all_destination_keys()
        and nav.visible(section)
    ):
        target = destination(section)
        if target.site_scoped:
            return RedirectResponse(target.href_at(site_id), status_code=302)
    first = first_open_destination(nav)
    if first is None:
        return _forbidden_html(request, db)
    return RedirectResponse(first.href_at(site_id), status_code=302)


def _fill_board(db: Session, page: ClinicPage) -> None:
    """Put the front desk's cards, lines and controls into an opened page's context.

    Offered and shown per the caller's grants at this clinic (Issue 52): a role that may read the
    overrides gets the badges and the trail; one that may also make them gets working controls, and
    anyone else gets the same controls disabled, never a missing button to wonder about.
    """
    board = read_board(db, page.access)
    nav = page.shell.nav
    can_read_priority = nav.can(PRIORITY_RESOURCE, PermissionVerb.READ)
    page.context.update(
        board=board,
        queues=board.cards,
        lines=queue_lines(
            db,
            page.access,
            [card.id for card in board.cards],
            include_priority=can_read_priority,
        ),
        can_read_priority=can_read_priority,
        can_reorder=nav.can(PRIORITY_RESOURCE, PermissionVerb.UPDATE),
        reasons=reason_choices(),
        note_max_length=MAX_REORDER_NOTE_LENGTH,
    )


def _open_board(request: Request, db: Session, site_id: str) -> ClinicPage | Response:
    """Open the front desk at ``site_id`` through the shell's gates."""
    key = "board"
    return open_clinic_page(
        request,
        db,
        site_id,
        active_key=key,
        page_title=destination(key).label,
        allowed=_visible(key),
    )


@router.get("/dashboard/sites/{site_id}/board", response_class=HTMLResponse)
async def clinic_board(site_id: str, request: Request, db: DbSession) -> Response:
    """The front desk: every queue's card, live (Issue 49), with its waiting line (Issue 52)."""
    opened = _open_board(request, db, site_id)
    if not isinstance(opened, ClinicPage):
        return opened
    _fill_board(db, opened)
    return render_clinic_page(request, opened, "dashboard/board.html")


@router.get("/dashboard/sites/{site_id}/board/cards", response_class=HTMLResponse)
async def clinic_board_cards(site_id: str, request: Request, db: DbSession) -> Response:
    """Just the front desk's cards, for the page to swap in when something changed (Issue 49).

    The same gates and the same data as the page, rendered from the same partial, so a refreshed card
    and a freshly loaded one cannot differ. A signed-out caller gets ``401`` rather than a redirect to
    the sign-in page, because the caller is a script that has to notice, not a person to send away.
    """
    if not require_authenticated_html(request, db):
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    opened = _open_board(request, db, site_id)
    if not isinstance(opened, ClinicPage):
        return opened
    _fill_board(db, opened)
    response = templates.TemplateResponse(
        request, "dashboard/_board_cards.html", opened.context
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@contextmanager
def _short_session(request: Request) -> Iterator[Session]:
    """A session that lives only as long as the ``with`` block, from the app's own ``get_db``.

    For a stream, which must not hold a request-scoped session (and so a pooled connection) open for
    a whole shift. Uses the application's ``get_db`` override when one is installed, so a test's
    database is the one checked.
    """
    provider = request.app.dependency_overrides.get(get_db, get_db)
    sessions = provider()
    try:
        yield next(sessions)
    finally:
        sessions.close()


def _board_stream_refusal(request: Request, site_id: str) -> Response | str:
    """The stream's gate: the caller's user id, or the response that refuses them."""
    with _short_session(request) as db:
        if not require_authenticated_html(request, db):
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)
        user = peek_user_from_refresh_cookie(db, request)
        if user is None or site_id not in {site.id for site in staff_sites(db, user)}:
            return Response(status_code=status.HTTP_404_NOT_FOUND)
        if not nav_visibility_at_site(db, user, site_id).visible("board"):
            return Response(status_code=status.HTTP_403_FORBIDDEN)
        return str(user.id)


@router.get("/dashboard/sites/{site_id}/board/stream")
async def clinic_board_stream(site_id: str, request: Request) -> Response:
    """The clinic's live events as server-sent events (Issue 49, the format Issue 57 shares).

    Gated like the board: signed out ``401``, another clinic ``404``, no front desk here ``403``, and
    the check runs again at every heartbeat, so a removed assignment or a switched-off account ends
    the stream within :data:`~src.core.live_events.HEARTBEAT_SECONDS`. The events name what changed and
    carry no patient data; the page reads the cards again through its own gate.

    No request-scoped session: a stream can stay open all day, and each check opens a short session
    of its own, off the event loop, and closes it before anything more is sent.
    """
    gate = await asyncio.to_thread(_board_stream_refusal, request, site_id)
    if isinstance(gate, Response):
        return gate
    user_id = gate

    def check_access() -> bool:
        """Whether the caller still works here and still has the front desk (a fresh read)."""
        with _short_session(request) as db:
            current = db.get(User, user_id)
            return bool(
                current is not None
                and current.is_active
                and not current.is_deleted
                and site_id in {site.id for site in staff_sites(db, current)}
                and nav_visibility_at_site(db, current, site_id).visible("board")
            )

    async def still_allowed() -> bool:
        """:func:`check_access`, in a worker thread so the event loop never waits on the database."""
        return await asyncio.to_thread(check_access)

    try:
        events = broker.stream(
            site_id,
            is_disconnected=request.is_disconnected,
            still_allowed=still_allowed,
        )
        first = await anext(events)
    except TooManySubscribersError:
        return Response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "30"},
        )

    async def body() -> AsyncIterator[str]:
        """The first event already taken (which proved the subscription), then the rest."""
        yield first
        async for chunk in events:
            yield chunk

    return StreamingResponse(
        body(), media_type=EVENT_STREAM_MEDIA_TYPE, headers=STREAM_HEADERS
    )


@router.get("/dashboard/sites/{site_id}/room", response_class=HTMLResponse)
async def clinic_room(site_id: str, request: Request, db: DbSession) -> Response:
    """A clinician's room: only the queues they are assigned to at this clinic (Issue 28).

    The frame for Issue 53's room view. Another room's queues are not read at all, not read and
    hidden.
    """
    key = "room"
    opened = open_clinic_page(
        request,
        db,
        site_id,
        active_key=key,
        page_title=destination(key).label,
        allowed=_visible(key),
    )
    if not isinstance(opened, ClinicPage):
        return opened
    own = permitted_queue_ids(db, opened.access.user)
    opened.context["queues"] = read_board(db, opened.access, only=own).cards
    return render_clinic_page(request, opened, "dashboard/room.html")


@router.get("/dashboard/sites/{site_id}/overrides", response_class=HTMLResponse)
async def clinic_overrides(
    site_id: str,
    request: Request,
    db: DbSession,
    day: Annotated[date | None, Query()] = None,
    queue: Annotated[str | None, Query(max_length=36)] = None,
    reason: Annotated[PriorityReason | None, Query()] = None,
    staff: Annotated[str | None, Query(max_length=255)] = None,
) -> Response:
    """The manager's view of a day's priority overrides, with counts per staff member (Issue 52).

    A server-rendered list (``docs/IDE/RULES/list-view-ui-pattern.mdc``, second wiring style): the
    filters are a real ``GET`` form, so a filtered day is a link, and the sort and the row's quick
    view work on the rows already in the page. The counts cover the whole day whatever the filters
    say, are listed by name, and say what they are not.
    """
    key = "overrides"
    opened = open_clinic_page(
        request,
        db,
        site_id,
        active_key=key,
        page_title=destination(key).label,
        allowed=_visible(key),
    )
    if not isinstance(opened, ClinicPage):
        return opened
    today = business_date()
    chosen = min(day or today, today)
    overrides = override_day(db, opened.access, chosen)
    filters = OverrideFilters(queue=queue or None, reason=reason, staff=staff or None)
    opened.context.update(
        overrides=overrides,
        entries=filters.apply(overrides.entries),
        filters=filters,
        reasons=reason_choices(),
        queue_names={
            queue.id: queue.name for queue in list_queues(db, opened.access).items
        },
        today=today,
    )
    return render_clinic_page(request, opened, "dashboard/overrides.html")
