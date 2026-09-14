"""Shared Jinja context for admin dashboard HTML pages (Issue 9)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from src.commons.enums import GrantScope, PermissionVerb
from src.core.config import get_settings
from src.core.nav_registry import NAV_DESTINATIONS, group
from src.core.nav_visibility import (
    NavVisibility,
    nav_visibility_for_request,
    nav_visibility_for_request_safe,
)

# Where the breadcrumb trail's leading "home" crumb points. The partial draws that crumb itself,
# so a trail — built here or by hand in a route — never carries it.
HOME_HREF = "/dashboard"

_DESTINATIONS_BY_KEY = {d.key: d for d in NAV_DESTINATIONS}


def default_breadcrumbs(
    nav: NavVisibility, active_nav: str, page_title: str, path: str
) -> list[dict[str, Any]]:
    """Derive a breadcrumb trail for a page that did not build its own.

    Read off the nav registry, so a console's trail is whatever the rail already calls it and a
    new destination gets a trail without a template edit: ``Leasing → Invoices & payments`` for a
    grouped console, ``Tenants`` for a standalone one, and the page title alone for a page outside
    the registry (the account and portal pages). The leading home crumb is the partial's job.

    The home page itself gets an empty trail — the partial renders the home crumb as the current
    page — rather than a trail that points at where it already is.
    """
    if path == HOME_HREF:
        return []
    destination = _DESTINATIONS_BY_KEY.get(active_nav)
    if destination is None:
        return [{"label": page_title, "current": True}] if page_title else []
    trail: list[dict[str, Any]] = []
    # A grouped console sits under its group, which links to the first tab the caller can open.
    # A group named after its only real console (Maintenance) would repeat itself — skip it.
    if destination.group:
        group_label = group(destination.group).label
        if group_label != destination.label:
            trail.append(
                {"label": group_label, "href": nav.group_href(destination.group)}
            )
    # A page under a console rather than the console itself (a report tab, say) names itself last
    # and leaves the console as the link above it.
    if page_title and page_title != destination.label:
        trail.append({"label": destination.label, "href": destination.href})
        trail.append({"label": page_title, "current": True})
    else:
        trail.append({"label": destination.label, "current": True})
    return trail


#: The nav destination whose grant gates the *internal* half of the public landing page — the
#: issue count, the milestone roadmap and the team lanes (see ``web/index.html``). ``logs`` is the
#: operator/IT console, so "may read the application logs" and "may read the delivery plan" are one
#: grant rather than two: an admin who wants a new person to see the internals grants them the IT
#: console once, in ``/admin/rbac/permissions``, and both follow.
INTERNAL_NAV_KEY = "logs"


def can_view_internals(nav: NavVisibility) -> bool:
    """Return whether this caller may be shown the landing page's internal delivery sections.

    The roadmap, the milestone-by-milestone plan, the issue count and the six delivery lanes are
    *project* information, not product marketing: they name unshipped work, internal sequencing and
    the people behind each surface. They render for a signed-in operator and for nobody else.

    Gated on :data:`INTERNAL_NAV_KEY` at the destination's own tier — ``logs:READ`` at
    ``business`` — which is the same gate the footer's "Status" link already uses
    (``can_view_status``). The two are deliberately separate context keys even though they resolve
    identically today: one governs a link to a health endpoint and the other governs page content,
    and a future re-point of either gate in ``/admin/rbac/catalog`` should not silently drag the
    other with it.

    An anonymous caller holds no grants at all, so this is false for every signed-out visitor
    without needing a second "are they signed in?" check — including during a database outage,
    where :func:`~src.core.nav_visibility.nav_visibility_for_request_safe` degrades to the
    signed-out shell and the internals stay closed rather than failing open.

    A pure function of an already-resolved :class:`NavVisibility`, like
    :func:`can_explain_denial`, so the property can be asserted directly rather than through
    rendered HTML.
    """
    return nav.visible(INTERNAL_NAV_KEY)


def public_page_context(request: Request, **extra: Any) -> dict[str, Any]:
    """Context for the public front-door pages (home, search, features, legal, apply).

    These pages carry the shared topbar and footer but take no DB session of their own, so
    they need auth state resolved independently. ``nav_visibility_for_request_safe`` opens
    its own short-lived session and degrades to a signed-out shell on any DB error, keeping
    the front door renderable when the database is down.

    ``is_authenticated`` flips the topbar's "Sign in" affordance to "Dashboard" (and the
    apps-menu tile to "Sign out"); ``can_view_status`` gates the footer "Status" link to
    signed-in operators (admin / logs permission) — see :func:`page_context`;
    ``can_view_internals`` (:func:`can_view_internals`) gates the landing page's internal delivery
    sections behind the same operator grant, so a signed-out visitor gets the product and an
    operator additionally gets the plan.
    """
    nav = nav_visibility_for_request_safe(request)
    settings = get_settings()
    ctx: dict[str, Any] = {
        "app_name": settings.app_name,
        "settings": settings,
        "is_authenticated": nav.show_icon_sidebar,
        "can_view_status": nav.show_logs,
        "can_view_internals": can_view_internals(nav),
    }
    ctx.update(extra)
    return ctx


def page_context(
    request: Request,
    db: Session,
    *,
    nav: NavVisibility | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build template context with registry-driven nav/action visibility and settings.

    Templates render from registry-backed handles rather than a per-resource boolean each (Issue
    #104): ``nav`` — the :class:`NavVisibility` whose ``visible()`` / ``group_visible()`` /
    ``group_href()`` drive the rail and grouped tabs — ``can(resource, verb)`` for in-page action
    buttons gated by a cumulative CRUD verb, and ``can_action(resource, action)`` (Issue #155) for
    the rarer button gated by a named action instead (``sign``, ``approve``, ...). Change a grant
    in the RBAC admin and all three follow, with no template edit.

    ``nav`` is passed by a page that has already resolved visibility somewhere narrower than the
    caller's account: a clinic page resolves it with the roles held **at that clinic** (Issue 48),
    so its links and buttons follow the grant the clinic's API enforces.

    ``clinic`` is the dashboard frame (:class:`~src.web.dashboard.shell.ClinicShell`). A clinic page
    passes its own; any other page inside the signed-in shell (the account pages, a 403) gets the
    one for the clinic the caller last worked in, so the rail's clinic links do not vanish when a
    receptionist opens their profile.
    """
    if nav is None:
        nav = nav_visibility_for_request(db, request)
    ctx: dict[str, Any] = {
        "app_name": get_settings().app_name,
        "settings": get_settings(),
        # The registry-driven visibility object and the action-grant helper the templates read.
        "nav": nav,
        "can": nav.can,
        "can_action": nav.can_action,
        "show_side_bar": nav.show_icon_sidebar,
        # Mirrors of the two flags the shared topbar/footer partials read, so they render the
        # same signed-in affordances inside the app shell as on the public front door.
        "is_authenticated": nav.show_icon_sidebar,
        "can_view_status": nav.show_logs,
        "can_view_internals": can_view_internals(nav),
        "active_nav": "",
        "page_title": "",
    }
    ctx.update(extra)
    if nav.show_icon_sidebar and "clinic" not in ctx:
        ctx["clinic"] = remembered_clinic_shell(request, db)
    # Every page inside the signed-in shell carries a trail; a route that builds its own (the
    # full-detail pages, which name the record) has already put it in ``extra``. Public pages
    # never get the key at all, and the partial renders nothing without it.
    if nav.show_icon_sidebar and "breadcrumbs" not in ctx:
        ctx["breadcrumbs"] = default_breadcrumbs(
            nav, ctx["active_nav"], ctx["page_title"], request.url.path
        )
    return ctx


def remembered_clinic_shell(request: Request, db: Session) -> Any:
    """The dashboard frame for the clinic the caller last worked in, or ``None`` (Issue 48).

    ``None`` for an account that works at no clinic, and with auth disabled (there is no user to
    ask). Imported lazily: the frame reads the sites module, which is further down the import graph
    than this context builder every page uses.
    """
    from src.core.nav_visibility import peek_user_from_refresh_cookie
    from src.web.dashboard.shell import build_shell, remembered_site_id, staff_sites

    user = peek_user_from_refresh_cookie(db, request)
    if user is None:
        return None
    sites = staff_sites(db, user)
    site_id = remembered_site_id(request, sites)
    site = next((candidate for candidate in sites if candidate.id == site_id), None)
    return None if site is None else build_shell(db, user, site, sites)


#: The resource whose READ grant gates the 403 page's "why was this refused?" affordance
#: (Issue #175). Named here rather than inlined at the call site so the gate is one constant.
RBAC_RESOURCE = "rbac"


def can_explain_denial(nav: NavVisibility) -> bool:
    """Return whether this caller may be shown *why* a request of theirs was refused (Issue #175).

    Gated on the ``rbac:READ`` grant and on **nothing else** — never on a role name, which is the
    mistake Issues #164/#172 spent a milestone removing. For every other caller the 403 page is
    unchanged, because telling an unauthorized caller precisely which grant they lack is a
    disclosure, not a courtesy: it turns an honest refusal into a map of the permission model.

    The tier compared is ``business``, the same tier the ``/admin/rbac/simulate`` endpoint behind
    the affordance enforces. Gating the link on a *narrower* tier than the endpoint would render a
    link that 403s when followed — the click-then-refuse Issue #168 exists to remove.

    A pure function of an already-resolved :class:`NavVisibility` so the property can be asserted
    directly, rather than through the rendered HTML that
    ``docs/IDE/RULES/testing-strategy.mdc`` forbids testing.
    """
    return nav.can(RBAC_RESOURCE, PermissionVerb.READ, GrantScope.BUSINESS)


def require_authenticated_html(request: Request, db: Session) -> bool:
    """Return True when the request has a valid signed-in shell (sidebar visible)."""
    return nav_visibility_for_request(db, request).show_icon_sidebar
