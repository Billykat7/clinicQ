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

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from src.commons.enums import PermissionVerb, PriorityReason, TicketStatus
from src.commons.time import business_date
from src.core.config import get_settings
from src.core.nav_registry import all_destination_keys, destination
from src.core.nav_visibility import NavVisibility, peek_user_from_refresh_cookie
from src.core.site_scope import SiteAccess, permitted_queue_ids
from src.database.models.queue_reorder import MAX_REORDER_NOTE_LENGTH
from src.database.session import get_db
from src.modules.queue.tickets import site_day_select
from src.modules.queues.service import list_queues
from src.web.context import page_context, require_authenticated_html
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
    SITE_DISPLAY_RESOURCE,
    SITE_PROFILE_RESOURCE,
    _forbidden_html,
    _not_found_html,
    _redirect_to_sign_in,
    templates,
)

router = APIRouter(tags=["web"])

DbSession = Annotated[Session, Depends(get_db)]

#: Statuses that mean "with a member of staff now": called to the room, called again, or being seen.
_WITH_STAFF: frozenset[TicketStatus] = frozenset(
    {TicketStatus.CALLED, TicketStatus.RECALLED, TicketStatus.IN_PROGRESS}
)


@dataclass(frozen=True, slots=True)
class ClinicPage:
    """An opened clinic page: the frame, the template context and the guard's access."""

    shell: ClinicShell
    context: dict[str, object]
    access: SiteAccess


@dataclass(frozen=True, slots=True)
class QueueSummary:
    """One queue as the dashboard's cards show it: who is waiting, and who is with staff."""

    id: str
    name: str
    room_label: str | None
    is_active: bool
    waiting: int
    with_staff: int


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


def queue_summaries(
    db: Session, access: SiteAccess, *, only: frozenset[str] | None = None
) -> list[QueueSummary]:
    """This clinic's queues in the clinic's order, each with today's waiting and in-room counts.

    ``only`` narrows to those queue ids (a nurse's own queues). Read through the site guard: the
    queues through :func:`~src.modules.queues.service.list_queues`, the tickets through
    :func:`~src.modules.queue.tickets.site_day_select`.
    """
    queues = [
        queue
        for queue in list_queues(db, access).items
        if only is None or queue.id in only
    ]
    waiting: Counter[str] = Counter()
    with_staff: Counter[str] = Counter()
    for ticket in db.execute(site_day_select(access, business_date())).scalars():
        if ticket.status_enum is TicketStatus.WAITING:
            waiting[ticket.queue_id] += 1
        elif ticket.status_enum in _WITH_STAFF:
            with_staff[ticket.queue_id] += 1
    return [
        QueueSummary(
            id=queue.id,
            name=queue.name,
            room_label=queue.room_label,
            is_active=queue.is_active,
            waiting=waiting[queue.id],
            with_staff=with_staff[queue.id],
        )
        for queue in queues
    ]


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


@router.get("/dashboard/sites/{site_id}/board", response_class=HTMLResponse)
async def clinic_board(site_id: str, request: Request, db: DbSession) -> Response:
    """The front desk: every queue at the clinic with who is waiting and who is with staff.

    The frame for Issue 49's live board, which replaces these server-rendered counts.
    """
    key = "board"
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
    queues = queue_summaries(db, opened.access)
    nav = opened.shell.nav
    # Offered and shown per the caller's grants at this clinic (Issue 52): a role that may read the
    # overrides gets the badges and the trail; one that may also make them gets working controls,
    # and anyone else gets the same controls disabled, never a missing button to wonder about.
    can_read_priority = nav.can(PRIORITY_RESOURCE, PermissionVerb.READ)
    opened.context.update(
        queues=queues,
        lines=queue_lines(
            db,
            opened.access,
            [queue.id for queue in queues],
            include_priority=can_read_priority,
        ),
        can_read_priority=can_read_priority,
        can_reorder=nav.can(PRIORITY_RESOURCE, PermissionVerb.UPDATE),
        reasons=reason_choices(),
        note_max_length=MAX_REORDER_NOTE_LENGTH,
    )
    return render_clinic_page(request, opened, "dashboard/board.html")


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
    opened.context["queues"] = queue_summaries(db, opened.access, only=own)
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


@router.get("/dashboard/sites/{site_id}/settings", response_class=HTMLResponse)
async def clinic_settings(site_id: str, request: Request, db: DbSession) -> Response:
    """The clinic's settings: the waiting-room screen's, the first settings page that exists."""
    key = "clinic_settings"
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
    return RedirectResponse(
        f"/dashboard/sites/{site_id}/settings/display", status_code=302
    )


@router.get("/dashboard/sites/{site_id}/settings/display", response_class=HTMLResponse)
async def site_display_settings_page(
    site_id: str, request: Request, db: DbSession
) -> Response:
    """The waiting-room screen's settings for one clinic (Issue 27, non-negotiable 4).

    Two gates, in the order that keeps a 404 honest: the caller has to hold a role **at this
    clinic** (another clinic's id renders the not-found page, never a 403 that would confirm the id
    exists), and then the ``sites.display`` grant **at this clinic** decides whether they may see the
    page at all. The JSON API behind it re-checks the same grant and adds the ``update`` check for
    saving, because this gate governs what is *offered* and the API's governs what is *done*.

    The page itself renders no rule: the modes, their descriptions, the bounds and the warnings all
    come from ``/api/v1/sites/display-options``.
    """
    opened = open_clinic_page(
        request,
        db,
        site_id,
        active_key="clinic_settings",
        page_title="Waiting-room screen",
        allowed=lambda nav: nav.can(SITE_DISPLAY_RESOURCE, PermissionVerb.READ.value),
    )
    if not isinstance(opened, ClinicPage):
        return opened
    return render_clinic_page(request, opened, "dashboard/settings_display.html")


@router.get("/dashboard/sites/{site_id}/settings/payment", response_class=HTMLResponse)
async def site_payment_profile_page(
    site_id: str, request: Request, db: DbSession
) -> Response:
    """A private clinic's payment methods and medical aids, as it reports them (Issue 37).

    Behind ``PAYMENT_FILTER_ENABLED``: while the feature is off the page is not served at all (the
    API still accepts a profile, so a clinic can fill it in beforehand). The same two gates as the
    display settings page: a role **at this clinic**, then the ``sites.profile`` grant there. The
    page renders no rule: whether the clinic may hold a profile, the scheme list and the notice all
    come from ``/api/v1/sites/{site_id}/payment-profile``, and the server refuses a public clinic's
    save.
    """
    if not get_settings().payment_filter_enabled:
        return _not_found_html(request, db)
    opened = open_clinic_page(
        request,
        db,
        site_id,
        active_key="clinic_settings",
        page_title="Payment and medical aid",
        allowed=lambda nav: nav.can(SITE_PROFILE_RESOURCE, PermissionVerb.READ.value),
    )
    if not isinstance(opened, ClinicPage):
        return opened
    return render_clinic_page(request, opened, "dashboard/settings_payment.html")
