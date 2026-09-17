"""Jinja-rendered pages: the public front door and the admin shell (Issue 9).

Every route here renders HTML; the JSON API lives under ``src/api``. Three groups:

* the **public front door** — ``/``, ``/features``, ``/privacy``, ``/terms``. Built with
  :func:`~src.web.context.public_page_context`, which resolves auth state through a short-lived
  session that degrades to a signed-out shell on any database error, so the front door still
  renders when the database is down;
* the **signed-in shell** — ``/dashboard``, ``/account/*``, and the consoles each feature flag
  brings with it;
* the **access-control console** — ``/admin/rbac/*``, the one admin surface the kernel owns
  outright.

Your own pages go here alongside them, or in a ``src/web/`` module of their own once there are
enough of them. Two rules keep a page honest, and every route below follows both:

1. :func:`~src.web.context.require_authenticated_html` first — a signed-out visitor is redirected
   to sign in, never shown an empty shell;
2. then a **grant** check — ``ctx["nav"].visible(key)`` for a console with a nav destination,
   ``ctx["nav"].can(resource, verb)`` for anything finer. Rendering is only half of it: the JSON
   API each page calls re-checks the same grant, because the page gate governs what is *offered*
   and the API gate governs what is *done*.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from src.commons.enums import (
    NOTIFICATION_LANGUAGES,
    DisplayDeviceStatus,
    GrantScope,
    NotificationChannel,
    NotificationTemplate,
    PermissionVerb,
    S3LogListingLevel,
    SaProvince,
    SiteSector,
    SiteStatus,
)
from src.commons.time import APP_TIMEZONE
from src.core.config import get_settings
from src.core.nav_visibility import (
    nav_visibility_for_request,
    peek_user_from_refresh_cookie,
)
from src.core.rbac import (
    effective_verb_over_keys,
    load_effective_grant_keys_for_role,
    load_resource_parent_map,
)
from src.core.rbac_language import scope_tier_label, scope_tier_meaning
from src.core.s3_logs_query import warm_logs_listing
from src.core.scope import ASSIGNMENT_SCOPE_TYPE, scope_tiers_for_roles
from src.core.security import decode_patient_session_token
from src.database.session import get_db
from src.web.components import register_components
from src.web.context import (
    can_explain_denial,
    page_context,
    public_page_context,
    require_authenticated_html,
)
from src.web.dashboard.shell import clinic_home_href, remembered_site_id, staff_sites

logger = logging.getLogger(__name__)

#: The resource whose grants gate the waiting-room screen settings page (Issue 27). Named here so
#: the page gate and the API gate cannot drift to different resources.
SITE_DISPLAY_RESOURCE = "sites.display"
#: The clinic profile resource, which a clinic's payment profile belongs to (Issue 37).
SITE_PROFILE_RESOURCE = "sites.profile"

#: And the one gating the operator's cross-clinic consoles, at ``business`` tier.
SITES_RESOURCE = "sites"
#: The clinic-verification console's own resource (Issues 29, 221): the listing decision is not the
#: authority to delete a clinic, so the console gates on what the API gates on, not on ``sites``.
SITE_ONBOARDING_RESOURCE = "sites.onboarding"

PACKAGE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"

# The date the public legal pages (`/privacy`, `/terms`) were last reviewed. A single source so
# both pages show the same visible "last updated" line. Update it whenever the wording changes;
# it is editorial metadata, not a magic string.
LEGAL_PAGES_LAST_UPDATED = "9 September 2026"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# The component vocabulary (badge tones, ticket-status badges) every template may use (Issue 5).
register_components(templates.env)

router = APIRouter(tags=["web"])


def _redirect_to_sign_in(request: Request) -> RedirectResponse:
    """Send a signed-out visitor to ``/signin``, remembering where they were going.

    Issue 231: this used to be ``/?next=…&openSignin=1`` — the home page, with a modal told to
    open itself. The sign-in page has an address of its own now, so the redirect points at it and
    the person can reload, bookmark or come back to it.
    """
    path = request.url.path
    query = request.url.query
    next_path = f"{path}?{query}" if query else path
    return RedirectResponse(
        url=f"/signin?next={quote(next_path, safe='')}",
        status_code=302,
    )


#: Where ``?next=`` may send someone after they sign in: a path on this site, and nothing else.
#: ``//host`` is a URL a browser reads as another origin, so a second character of ``/`` is refused.
def _safe_next(raw: str | None) -> str:
    """Return ``raw`` when it is a same-origin path, else the dashboard."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/dashboard"


#: Why someone is looking at the sign-in page, when a redirect or a finished flow sent them there.
#: The key is a query parameter and the sentence is written here, so a link cannot put words on
#: the page.
_SIGN_IN_NOTICES = {
    "expired": "Your session ended. Sign in again to carry on.",
    "reset": "Password updated. Sign in with your new password.",
    "signedout": "You are signed out.",
}


@router.get("/signin", response_class=HTMLResponse)
async def sign_in_page(
    request: Request,
    next: str | None = None,
    email: str | None = None,
    reset: str | None = None,
    expired: str | None = None,
    signedout: str | None = None,
) -> HTMLResponse:
    """The staff sign-in page (Issue 231).

    What the page offers is decided **here**, from settings, and rendered into the template: the
    password field, the "email me a code" button and the sign-up link exist or they do not. The
    modal this replaces fetched ``/auth/config`` after paint and hid controls once the answer came
    back, so a person could see a password field appear and disappear on a slow connection.

    ``next`` is checked against :func:`_safe_next` before it reaches the page, so a crafted link
    cannot turn a successful sign-in into a redirect off this site.
    """
    settings = get_settings()
    # ``next`` is the query parameter's name, so the builtin is out of reach in here: a plain loop.
    notice = None
    for key, present in (
        ("reset", reset),
        ("expired", expired),
        ("signedout", signedout),
    ):
        if present:
            notice = _SIGN_IN_NOTICES[key]
            break
    return templates.TemplateResponse(
        request,
        "web/auth_signin.html",
        public_page_context(
            request,
            next_path=_safe_next(next),
            prefill_email=(email or "").strip(),
            notice=notice,
            otp_login_enabled=settings.auth_otp_login_enabled,
            password_login_enabled=settings.auth_password_login_enabled,
            signup_enabled=settings.signup_enabled,
        ),
    )


@router.get("/signup", response_class=HTMLResponse)
async def sign_up_page(request: Request) -> HTMLResponse:
    """Create a staff account (Issue 231), when this deployment allows it.

    A 404 rather than a form when ``SIGNUP_ENABLED`` is off: the only thing the form could do is
    collect an email and be refused by the API with a 403, and a page that cannot work should not
    be reachable.
    """
    if not get_settings().signup_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return templates.TemplateResponse(
        request, "web/auth_signup.html", public_page_context(request)
    )


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(
    request: Request, email: str | None = None
) -> HTMLResponse:
    """Ask for a password-reset link (Issue 231).

    Only reachable when passwords are a way in at all; ``/auth/password/forgot`` answers 403
    otherwise, and the sign-in page does not link here in that case.
    """
    if not get_settings().auth_password_login_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return templates.TemplateResponse(
        request,
        "web/auth_forgot.html",
        public_page_context(request, prefill_email=(email or "").strip()),
    )


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(
    request: Request, token: str | None = None
) -> HTMLResponse:
    """Where the emailed reset link lands (Issue 231).

    It used to land on ``/?resetToken=…`` — the home page, which opened the modal on its reset
    screen and then rewrote the address bar to take the token back out. The step has a page now,
    and the token becomes a hidden field on the form rather than something the landing page carries.

    The page does not check the token, or say whether it looks valid: the API decides that, once,
    when it is used. Without one it explains what is missing instead of showing a form that cannot
    work.
    """
    if not get_settings().auth_password_login_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return templates.TemplateResponse(
        request,
        "web/auth_reset.html",
        public_page_context(request, token=(token or "").strip()),
    )


def _forbidden_html(request: Request, db: Session) -> HTMLResponse:
    """Render the signed-in 403 page for an authenticated-but-unauthorized user.

    A signed-out visitor is redirected to sign in; a signed-in user who lacks the
    permission for a page never sees its nav link, and gets an honest ``403`` inside
    the app shell if they reach the URL anyway (Issue 53), rather than a silent
    bounce that hides why the page did not open.

    **Issue #175 (M29)** adds one thing, for one audience: a caller who holds ``rbac:READ`` gets a
    "why was this refused?" affordance that simulates the refused request. For everyone else this
    page is unchanged — :func:`~src.web.context.can_explain_denial` is false, the template renders
    nothing extra, and the refusal stays exactly as terse as it has always been. Telling an
    unauthorized caller which grant they lack is a disclosure, not a courtesy.
    """
    ctx = page_context(request, db, page_title="Access denied")
    ctx["can_explain_denial"] = can_explain_denial(ctx["nav"])
    if ctx["can_explain_denial"]:
        # The refused path and the caller it was refused for, so the affordance simulates *this*
        # request rather than asking the operator to retype it. Resolved only inside the gate: a
        # caller who cannot be shown the answer never has the inputs put on their page either.
        denied_user = peek_user_from_refresh_cookie(db, request)
        ctx["denied_path"] = request.url.path
        ctx["denied_principal_email"] = (
            str(denied_user.email) if denied_user is not None else ""
        )
    return templates.TemplateResponse(
        request,
        "403.html",
        ctx,
        status_code=status.HTTP_403_FORBIDDEN,
    )


def _page_gate_denied(
    db: Session,
    request: Request,
    resource: str,
    verb: PermissionVerb = PermissionVerb.READ,
    scope: GrantScope = GrantScope.OWN,
) -> bool:
    """Return True when the caller's grant does not admit them to ``resource``'s page (Issue #167).

    The gate for a page whose route resolves its data *before* building a template context, so
    there is no ``ctx["nav"]`` to check yet (the report pages, the portals). Same decision as
    ``ctx["nav"].can_surface(...)`` — one ``NavVisibility`` build, the surface's DB gate override
    if an admin has re-pointed it, then the verb and the :class:`~src.commons.enums.GrantScope`
    tier — just reached directly.

    ``scope`` defaults to ``OWN``, the narrowest tier, because every page this helper gates is a
    **first-person** surface: your portal, your jobs, your dashboard, a report already narrowed to
    what you own. The tier must not be what refuses them; the *grant* is (see the surface-framework
    doc's "Ownership-only surfaces" section for the decision and its reasoning).
    """
    return not nav_visibility_for_request(db, request).can_surface(
        resource, verb, scope
    )


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request) -> HTMLResponse:
    """Public home page — the front door for visitors, signed in or out.

    Resilient by design: the context is built by :func:`public_page_context`, which resolves
    auth state through a short-lived session that degrades to a signed-out shell on any DB
    error, so the page still renders when the database is unavailable (same for ``/features``
    and ``/search``). When a valid session is present the shared topbar shows "Dashboard" and
    the apps-menu "Sign out" instead of "Sign in". ``settings``/``app_name`` still come from
    config for the shared header; signing in is ``/signin`` now (Issue 231), so this page no longer
    carries a dialog or a query parameter that opens one.

    The page has **two audiences**. The product half is public; the delivery plan, the team lanes
    and the tracked-issue count are internal and render only for a caller holding the operator
    grant behind :func:`~src.web.context.can_view_internals`. That gate lives in the context, not
    here, because the same flag also gates the nav and footer links into those sections and the
    kernel section of ``/features``. A DB outage degrades to the signed-out shell, so the internals
    fail closed rather than open.
    """
    return templates.TemplateResponse(
        request,
        "web/index.html",
        public_page_context(request),
    )


@router.get("/features", response_class=HTMLResponse)
async def features_page(request: Request) -> HTMLResponse:
    """The long form of the landing page's scope section: what the product does, by audience.

    DB-independent like the rest of the front door. The product scope is public; the list of what
    the platform kernel ships *today* is internal build state and carries the same
    :func:`~src.web.context.can_view_internals` gate the landing page's delivery plan does.
    """
    return templates.TemplateResponse(
        request, "web/features.html", public_page_context(request)
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request) -> HTMLResponse:
    """Public POPIA privacy notice (Issue #64).

    Part of the DB-independent front door: no DB session and no user context, so the notice
    still renders when the database is unavailable — exactly like ``/`` and ``/features``. The
    data-retention table it carries mirrors what the models actually store, and the page shows
    a visible last-reviewed date so a stale notice is obvious.

    Written against the Protection of Personal Information Act 4 of 2013 and structured the way
    section 18 asks — who processes, what, why, on which section 11 justification, who else sees
    it, for how long, and how a data subject exercises the Chapter 3 rights. The responsible
    party / operator split matters and is stated on the page: the **clinic** is the responsible
    party for a patient's visit, and this platform is its operator.
    """
    return templates.TemplateResponse(
        request,
        "web/privacy.html",
        public_page_context(request, last_updated=LEGAL_PAGES_LAST_UPDATED),
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request) -> HTMLResponse:
    """Public service terms for patients and clinics (Issue #64).

    DB-independent like the privacy notice: static config-only context so it survives a
    database outage, with the same visible last-reviewed date.

    South African law throughout — the ECT Act's section 43 supplier disclosure, the Consumer
    Protection Act's limits on excluding liability, POPIA by reference to ``/privacy``. The first
    clause on the page is the one that matters most: this is **not** an emergency service, and a
    queue product must never be mistaken for a way to get urgent help.
    """
    return templates.TemplateResponse(
        request,
        "web/terms.html",
        public_page_context(request, last_updated=LEGAL_PAGES_LAST_UPDATED),
    )


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Legacy admin hub."""
    return templates.TemplateResponse(
        request,
        "home.html",
        page_context(request, db, active_nav="explore", page_title="Home"),
    )


@router.get("/landing")
async def landing(request: Request) -> RedirectResponse:
    """Old landing URL — the home page now lives at ``/``."""
    return RedirectResponse(url="/", status_code=301)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """The shared landing page every signed-in caller reaches.

    Gated on ``dashboard`` READ, which the base ``user`` role holds — so every ordinary signed-in
    caller sees it, and a role granted nothing at all is refused here rather than served an empty
    shell.

    The kernel renders the destination cards the caller's own grants make reachable, and nothing
    else: there are no figures to show until there is a domain to count. Give it real content by
    passing your own aggregates into the context and rendering them in ``dashboard.html`` —
    resolve every figure through a scoped service (:func:`~src.core.scope.resolve_scope`) so a
    section can never show another user's rows.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    if _page_gate_denied(db, request, "dashboard"):
        return _forbidden_html(request, db)
    user = peek_user_from_refresh_cookie(db, request)
    # Clinic staff go straight to their clinic (Issue 48): the clinic they last worked in if they
    # still work there, else their first. The kernel landing below stays for an account that works
    # at no clinic, such as the platform operator.
    if user is not None and (sites := staff_sites(db, user)):
        site_id = remembered_site_id(request, sites)
        if site_id is not None:
            return RedirectResponse(clinic_home_href(site_id), status_code=302)  # type: ignore[return-value]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        page_context(
            request,
            db,
            active_nav="dashboard",
            page_title="Dashboard",
            display_name=(user.first_name or user.email) if user is not None else None,
        ),
    )


@router.get("/account", response_class=HTMLResponse)
async def account_root(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/account/profile", status_code=302)


@router.get("/account/profile", response_class=HTMLResponse)
async def account_profile(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    return templates.TemplateResponse(
        request,
        "account/profile.html",
        page_context(
            request,
            db,
            active_nav="account",
            page_title="Account",
            account_section="profile",
        ),
    )


@router.get("/account/security", response_class=HTMLResponse)
async def account_security(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    return templates.TemplateResponse(
        request,
        "account/security.html",
        page_context(
            request,
            db,
            active_nav="account",
            page_title="Account",
            account_section="security",
        ),
    )


@router.get("/account/notifications", response_class=HTMLResponse)
async def account_notifications(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """The signed-in user's notification preferences page (Issue #72).

    The page fetches and saves preferences through the JSON API
    (``/api/v1/notifications/preferences``); this route only renders the shell.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    return templates.TemplateResponse(
        request,
        "account/notifications.html",
        page_context(
            request,
            db,
            active_nav="account",
            page_title="Account",
            account_section="notifications",
        ),
    )


#: The verification console's tabs. Each is its own URL and its own ``SiteStatus``; the bare group
#: URL redirects to the first, and an unrecognised section redirects there too rather than 404ing
#: (the list-view convention, ``docs/IDE/RULES/list-view-ui-pattern.mdc``).
_VERIFICATION_SECTIONS: dict[str, SiteStatus] = {
    "pending": SiteStatus.PENDING_VERIFICATION,
    "verified": SiteStatus.VERIFIED,
    "draft": SiteStatus.DRAFT,
    "suspended": SiteStatus.SUSPENDED,
}


@router.get("/register-clinic", response_class=HTMLResponse)
async def register_clinic_page(request: Request) -> HTMLResponse:
    """The public clinic-registration form (Issue 29).

    On the front door, and public by necessity: whoever fills it in has no account. It is safe to
    leave open because a submission grants nothing — the clinic it creates is
    ``pending_verification``, invisible to every patient-facing surface, with no role and no
    session attached. Built on :func:`~src.web.context.public_page_context`, which takes no
    database session, so the page a clinic reaches us through still renders during an outage.
    """
    return templates.TemplateResponse(
        request,
        "web/register_clinic.html",
        public_page_context(
            request, provinces=[province.value for province in SaProvince]
        ),
    )


@router.get("/admin/verification", response_class=HTMLResponse)
async def admin_verification(request: Request) -> RedirectResponse:
    """Redirect the bare console URL to its default tab."""
    return RedirectResponse(
        url="/admin/verification/pending", status_code=status.HTTP_302_FOUND
    )


@router.get("/admin/verification/{section}", response_class=HTMLResponse)
async def admin_verification_section(
    section: str, request: Request, q: str = "", db: Session = Depends(get_db)
) -> HTMLResponse:
    """The platform admin's clinic-verification queue, one tab per listing status.

    Gated on the same ``business``-tier grant on ``sites.onboarding`` the API enforces — this is the
    operator's cross-clinic console, so it is deliberately **not** behind the site guard, which would
    404 somebody assigned to no clinic. The API re-checks the grant on every decision; this decides
    what is offered.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    if section not in _VERIFICATION_SECTIONS:
        return RedirectResponse(  # type: ignore[return-value]
            url="/admin/verification/pending", status_code=status.HTTP_302_FOUND
        )
    ctx = page_context(
        request, db, active_nav="verification", page_title="Clinic verification"
    )
    if not ctx["nav"].can_surface(
        SITE_ONBOARDING_RESOURCE, PermissionVerb.READ, GrantScope.BUSINESS
    ):
        return _forbidden_html(request, db)

    from src.modules.sites.onboarding import pending_queue
    from src.modules.sites.router import _queue_item
    from src.modules.sites.schemas import VerificationQueueOut

    wanted = _VERIFICATION_SECTIONS[section]
    rows = list(db.execute(pending_queue(db, status=wanted, query=q or None)).scalars())
    ctx["section"] = section
    ctx["query"] = q
    ctx["queue"] = VerificationQueueOut(
        total=len(rows), status=wanted, items=[_queue_item(row) for row in rows]
    )
    return templates.TemplateResponse(request, "admin/verification.html", ctx)


#: The clinics console's tabs (Issue 222): each its own URL, and ``all`` is the default. ``None`` is
#: "every status", which is what makes ``all`` the tab an operator lands on and works from.
_CLINIC_SECTIONS: dict[str, SiteStatus | None] = {
    "all": None,
    "draft": SiteStatus.DRAFT,
    "pending": SiteStatus.PENDING_VERIFICATION,
    "verified": SiteStatus.VERIFIED,
    "suspended": SiteStatus.SUSPENDED,
}
#: How many clinics one page of the console shows. The directory is a few hundred rows at most, and
#: the page is server-rendered in one request, so this is a guard against a runaway query rather than
#: a paging design: the filter bar is how an operator narrows it.
_CLINIC_PAGE = 200


@router.get("/admin/clinics", response_class=HTMLResponse)
async def admin_clinics(request: Request) -> RedirectResponse:
    """Redirect the bare console URL to its default tab."""
    return RedirectResponse(url="/admin/clinics/all", status_code=status.HTTP_302_FOUND)


@router.get("/admin/clinics/{section}", response_class=HTMLResponse)
async def admin_clinics_section(
    section: str,
    request: Request,
    q: str = "",
    # Not typed ``SiteSector``: a hand-edited or stale ``?sector=`` should show the unfiltered
    # console, not answer 422 at somebody who is looking for a clinic. ``None`` is "any", and
    # anything that is not a member is read as ``None`` below.
    sector: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """The operator's clinics console: every clinic, and where each one is (Issue 222).

    Every verb a clinic needs has existed in the sites API since Issue 23 and none of them had a
    screen, so creating one on behalf of a clinic that phoned in, or fixing a typed address, meant
    hand-writing JSON. This is that screen.

    Gated on ``sites`` at the ``business`` tier, the same grant ``GET /api/v1/sites`` enforces —
    the operator's cross-clinic console, deliberately **not** behind the site guard, which would
    404 somebody assigned to no clinic. Every mutation is a real request to the API, which
    re-checks the grant; this page decides what is *offered*.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    if section not in _CLINIC_SECTIONS:
        return RedirectResponse(  # type: ignore[return-value]
            url="/admin/clinics/all", status_code=status.HTTP_302_FOUND
        )
    ctx = page_context(request, db, active_nav="clinics", page_title="Clinics")
    if not ctx["nav"].can_surface(
        SITES_RESOURCE, PermissionVerb.READ, GrantScope.BUSINESS
    ):
        return _forbidden_html(request, db)

    from src.modules.sites import service as sites_service

    chosen_sector = next(
        (member for member in SiteSector if member.value == sector), None
    )
    listing = sites_service.list_sites(
        db,
        site_ids=None,  # the business tier: the whole directory
        sector=chosen_sector,
        status=_CLINIC_SECTIONS[section],
        query=q.strip() or None,
        limit=_CLINIC_PAGE,
    )
    ctx["section"] = section
    ctx["query"] = q
    ctx["sector"] = sector or ""
    ctx["listing"] = listing
    ctx["sectors"] = [member.value for member in SiteSector]
    ctx["provinces"] = [province.value for province in SaProvince]
    # Which buttons are drawn is decided in the template by ``can('sites', verb, 'business')`` —
    # the same grant the API re-checks on every call, so there is no second copy of the rule here.
    return templates.TemplateResponse(request, "admin/clinics.html", ctx)


#: The operator's screen console tabs (Issue 61): each its own URL and its own set of statuses.
_DISPLAY_DEVICE_SECTIONS: dict[str, frozenset[DisplayDeviceStatus]] = {
    "all": frozenset({DisplayDeviceStatus.ONLINE, DisplayDeviceStatus.SILENT}),
    "silent": frozenset({DisplayDeviceStatus.SILENT}),
    "removed": frozenset({DisplayDeviceStatus.REVOKED}),
}


@router.get("/admin/display-devices", response_class=HTMLResponse)
async def admin_display_devices(request: Request) -> RedirectResponse:
    """Redirect the bare console URL to its default tab."""
    return RedirectResponse(
        url="/admin/display-devices/all", status_code=status.HTTP_302_FOUND
    )


@router.get("/admin/display-devices/{section}", response_class=HTMLResponse)
async def admin_display_devices_section(
    section: str, request: Request, q: str = "", db: Session = Depends(get_db)
) -> HTMLResponse:
    """The operator's view of every waiting-room screen on the platform, and which have gone quiet (Issue 61).

    Gated on the ``business``-tier grant on ``sites``, like the verification console, because it reads
    across clinics. A server-rendered list (``docs/IDE/RULES/list-view-ui-pattern.mdc``, second wiring
    style): the tabs are URLs, the filter is a GET form, the columns sort in place and a row opens in
    the slideover. It changes nothing: a clinic's manager pairs and removes its screens.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    if section not in _DISPLAY_DEVICE_SECTIONS:
        return RedirectResponse(  # type: ignore[return-value]
            url="/admin/display-devices/all", status_code=status.HTTP_302_FOUND
        )
    ctx = page_context(
        request, db, active_nav="verification", page_title="Waiting-room screens"
    )
    if not ctx["nav"].can_surface(
        SITES_RESOURCE, PermissionVerb.READ, GrantScope.BUSINESS
    ):
        return _forbidden_html(request, db)

    from src.modules.display import devices as display_devices
    from src.modules.display.router import device_out

    wanted = _DISPLAY_DEVICE_SECTIONS[section]
    needle = q.strip().lower()
    rows = [
        (device_out(device), site)
        for device, site in display_devices.platform_devices(db)
        if display_devices.status_of(device) in wanted
        and (not needle or needle in f"{site.name} {device.label or ''}".lower())
    ]
    ctx.update(
        section=section,
        query=q,
        rows=rows,
        silent_minutes=get_settings().display_device_silent_minutes,
    )
    return templates.TemplateResponse(request, "admin/display_devices.html", ctx)


#: The template editor's channel tabs (Issue 66): the URL's word for each channel.
_TEMPLATE_CHANNEL_TABS: dict[str, NotificationChannel] = {
    "sms": NotificationChannel.SMS,
    "web-push": NotificationChannel.WEB_PUSH,
    "whatsapp": NotificationChannel.WHATSAPP,
}


@router.get("/admin/notification-templates", response_class=HTMLResponse)
async def admin_notification_templates(request: Request) -> RedirectResponse:
    """Redirect the bare console URL to its default tab."""
    return RedirectResponse(
        url="/admin/notification-templates/sms", status_code=status.HTTP_302_FOUND
    )


@router.get("/admin/notification-templates/{tab}", response_class=HTMLResponse)
def admin_notification_templates_tab(
    tab: str,
    request: Request,
    template: str = "",
    language: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Every patient message template in one channel, as it is sent now (Issue 66).

    For operators holding ``logs``. Server-rendered (``docs/IDE/RULES/list-view-ui-pattern.mdc``, second
    wiring style): each channel is its own URL, the filter is a GET form, columns sort in place and a row
    opens its words in the slideover, whose full view is the editor.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    channel = _TEMPLATE_CHANNEL_TABS.get(tab)
    if channel is None:
        return RedirectResponse(  # type: ignore[return-value]
            url="/admin/notification-templates/sms", status_code=status.HTTP_302_FOUND
        )
    ctx = page_context(
        request, db, active_nav="logs", page_title="Patient message templates"
    )
    if not ctx["nav"].visible("logs"):
        return _forbidden_html(request, db)

    from src.modules.notifications import template_registry
    from src.modules.notifications.template_router import _current_out

    rows = [
        _current_out(db, key, channel, lang)
        for key in sorted(template_registry.PATIENT_TEMPLATES)
        for lang in template_registry.languages()
        if (not template or key.value == template)
        and (not language or lang.value == language)
    ]
    ctx.update(
        tab=tab,
        tabs=_TEMPLATE_CHANNEL_TABS,
        rows=rows,
        template_filter=template,
        language_filter=language,
        templates_available=sorted(
            t.value for t in template_registry.PATIENT_TEMPLATES
        ),
        languages_available=[lang.value for lang in template_registry.languages()],
        all_languages=[lang.value for lang in NOTIFICATION_LANGUAGES],
    )
    return templates.TemplateResponse(request, "admin/notification_templates.html", ctx)


@router.get(
    "/admin/notification-templates/{tab}/{template}/{language}",
    response_class=HTMLResponse,
)
def admin_template_editor(
    tab: str,
    template: str,
    language: str,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Edit one template in one channel and language, with a live preview (Issue 66).

    The page carries what is sent now and the version history; ``admin-template-editor.js`` previews through
    ``POST /api/v1/notifications/templates/preview`` as the words change and publishes through the versions
    route, which refuses words that break the variable contract.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = page_context(
        request, db, active_nav="logs", page_title="Edit a patient message"
    )
    if not ctx["nav"].visible("logs"):
        return _forbidden_html(request, db)

    from src.modules.notifications import template_registry
    from src.modules.notifications.template_router import get_template

    channel = _TEMPLATE_CHANNEL_TABS.get(tab)
    try:
        key = NotificationTemplate(template)
    except ValueError:
        key = None
    if channel is None or key is None or key not in template_registry.PATIENT_TEMPLATES:
        return RedirectResponse(  # type: ignore[return-value]
            url="/admin/notification-templates/sms", status_code=status.HTTP_302_FOUND
        )
    try:
        detail = get_template(key, channel, language, None, db)
    except HTTPException:
        return RedirectResponse(  # type: ignore[return-value]
            url=f"/admin/notification-templates/{tab}",
            status_code=status.HTTP_302_FOUND,
        )
    ctx.update(
        tab=tab,
        channel=channel,
        template_key=key.value,
        language=language,
        detail=detail,
        breadcrumbs=[
            {
                "label": "Patient message templates",
                "href": f"/admin/notification-templates/{tab}",
            },
            {"label": f"{key.value} ({language})", "current": True},
        ],
    )
    return templates.TemplateResponse(request, "admin/template_editor.html", ctx)


@router.get("/me/consent", response_class=HTMLResponse)
async def patient_consent_page(request: Request) -> HTMLResponse:
    """The patient's own consent page (Issue 21): what we may do, in plain words.

    The page renders from the patient's session cookie alone — no database read — and the questions
    and answers come from ``/api/v1/patients/me/consents``, so the words a patient sees are the
    words recorded with their answer. Without a session it renders the signed-out version rather
    than redirecting: a patient has no password to be sent to, and the sign-in page is M9's.
    """
    token = request.cookies.get(get_settings().patient_session_cookie_name)
    return templates.TemplateResponse(
        request,
        "patient/consent.html",
        public_page_context(
            request,
            page_title="Your choices",
            signed_in=decode_patient_session_token(token or "") is not None,
        ),
    )


@router.get("/invite", response_class=HTMLResponse)
async def staff_invitation_page(request: Request) -> HTMLResponse:
    """The page a staff invitation link opens (Issue 22): what it is for, and a password field.

    Deliberately renders for anyone, with no database read and without inspecting the token: the
    page is a shell, and ``/api/v1/staff/invitations/preview`` is what decides whether the link is
    still good. A link that is used, revoked, expired or unknown gets one message from that
    endpoint, so the page cannot be used to tell those four apart either.
    """
    return templates.TemplateResponse(
        request,
        "account/invitation.html",
        public_page_context(request, page_title="Accept your invitation"),
    )


@router.get("/notifications/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_page(request: Request, token: str = "") -> HTMLResponse:
    """Login-free unsubscribe confirmation page (Issue #72).

    Renders from the signed token alone, with no database read, so it can never be used to probe
    which addresses exist: an invalid/expired token shows a generic "link is no longer valid" page,
    and a valid one names the category and offers a one-click confirm (which POSTs back here). No
    user context — a recipient acting from their inbox is not signed in.
    """
    from src.modules.notifications import preferences as notification_preferences

    result = notification_preferences.preview_unsubscribe(token)
    return templates.TemplateResponse(
        request,
        "notifications/unsubscribe.html",
        public_page_context(
            request,
            page_title="Unsubscribe",
            token=token,
            valid=result.valid,
            essential=result.essential,
            category=result.category.value if result.category else None,
            done=False,
        ),
    )


@router.post("/notifications/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_submit(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Form(""),
) -> HTMLResponse:
    """Apply a login-free / one-click unsubscribe (Issue #72).

    Token-authenticated (no session): honours RFC 8058 one-click ``POST`` from a mail client as
    well as the confirm button on the GET page. Enumeration-safe — every valid token reports the
    same success whether or not the address has an account, and an essential category explains it
    cannot be disabled. Idempotent.
    """
    from src.modules.notifications import preferences as notification_preferences

    result = notification_preferences.apply_unsubscribe(db, token)
    if result.valid:
        db.commit()
    return templates.TemplateResponse(
        request,
        "notifications/unsubscribe.html",
        public_page_context(
            request,
            page_title="Unsubscribe",
            token=token,
            valid=result.valid,
            essential=result.essential,
            category=result.category.value if result.category else None,
            done=True,
        ),
    )


_RBAC_SECTIONS = ("roles", "permissions", "users", "audit", "catalog")


# Catalog sub-tabs, each its own bookmarkable URL under /admin/rbac/catalog (Issue #141 follow-up).
# ``nav-gates`` (Issue #146) is the Option C "re-gate an existing surface" control.


_CATALOG_SUBSECTIONS = ("resources", "actions", "permissions", "nav-gates")


@router.get("/admin/rbac", response_class=HTMLResponse)
async def admin_rbac(request: Request) -> RedirectResponse:
    """The bare access-control URL sends to the first section, so every tab has its own path."""
    return RedirectResponse(url="/admin/rbac/roles", status_code=302)


@router.get("/admin/rbac/{section}", response_class=HTMLResponse)
async def admin_rbac_section(
    request: Request,
    section: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Access control, one URL per tab: /admin/rbac/{roles|permissions|users}.

    Each section is its own bookmarkable page (like /admin/messages and /admin/notifications);
    the template renders the section as the active tab and the page JS loads only its data.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    selected = section.strip().lower()
    if selected not in _RBAC_SECTIONS:
        return RedirectResponse(url="/admin/rbac/roles", status_code=302)  # type: ignore[return-value]
    if selected == "catalog":
        # Catalog is split into sub-tabs, each its own URL; the bare section lands on the first,
        # carrying any list query (offset/limit) through so a bookmarked view survives the redirect.
        target = "/admin/rbac/catalog/resources"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=302)  # type: ignore[return-value]
    ctx = page_context(
        request,
        db,
        active_nav="rbac",
        page_title="Access control",
        rbac_initial_tab=selected,
    )
    if not ctx["nav"].visible("rbac"):
        return _forbidden_html(request, db)
    # Users is the one tab that enforces a separate resource (``users``, not ``rbac`` — see
    # rbac_admin.html's own comment on the Users tab link for why); a caller without it falls back
    # to Roles rather than rendering an empty/403-storming tab (Issue #155, same shape as the
    # invoice statement's ``record`` tab fallback above).
    if selected == "users" and not ctx["can"]("users", "read"):
        return RedirectResponse(url="/admin/rbac/roles", status_code=302)  # type: ignore[return-value]
    return templates.TemplateResponse(request, "rbac_admin.html", ctx)


@router.get("/admin/rbac/catalog/{subsection}", response_class=HTMLResponse)
async def admin_rbac_catalog_subsection(
    request: Request,
    subsection: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Catalog sub-tabs, one URL per section: /admin/rbac/catalog/{resources|actions|permissions}.

    Mirrors the top-level RBAC tabs (Issue #141 follow-up): each sub-section is its own
    bookmarkable page rendered inside the Catalog tab, with the page JS loading only that
    section's rows. Gated exactly like the rest of the console (``nav.visible("rbac")``).
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    selected = subsection.strip().lower()
    if selected not in _CATALOG_SUBSECTIONS:
        return RedirectResponse(  # type: ignore[return-value]
            url="/admin/rbac/catalog/resources", status_code=302
        )
    ctx = page_context(
        request,
        db,
        active_nav="rbac",
        page_title="Access control",
        rbac_initial_tab="catalog",
        catalog_initial_sub=selected,
    )
    if not ctx["nav"].visible("rbac"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(request, "rbac_admin.html", ctx)


def _detail_dt(value: datetime | None) -> str | None:
    """Format a timestamp for a full-detail page in the app timezone, or ``None`` when absent."""
    if value is None:
        return None
    return value.astimezone(APP_TIMEZONE).strftime("%d %b %Y, %H:%M")


def _not_found_html(request: Request, db: Session) -> HTMLResponse:
    """Render the signed-in "not found" page (reuses the 403 shell with a 404 status)."""
    return templates.TemplateResponse(
        request,
        "403.html",
        page_context(request, db, page_title="Not found"),
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _role_instance_assignments(db: Session, role_name: str) -> list[dict[str, str]]:
    """Return the scoped assignments held for ``role_name``, newest first.

    One row per (holder, instance) — the ``user_roles`` rows that make an ``assigned``-tier grant
    mean anything. The holder is named by email rather than id so the panel reads as sentences;
    the instance is named by its id, because the kernel does not know what kind of thing it is.
    Resolve it to a name of your own by joining the table
    :data:`~src.core.scope.ASSIGNMENT_SCOPE_TYPE` refers to.
    """
    from src.database.models import User, UserRoleAssignment

    rows = db.execute(
        select(
            UserRoleAssignment.id,
            UserRoleAssignment.scope_id,
            UserRoleAssignment.user_id,
            User.email,
        )
        .join(User, User.id == UserRoleAssignment.user_id, isouter=True)
        .where(
            UserRoleAssignment.role == role_name,
            UserRoleAssignment.scope_type == ASSIGNMENT_SCOPE_TYPE,
            UserRoleAssignment.scope_id.is_not(None),
        )
        .order_by(UserRoleAssignment.granted_at.desc(), UserRoleAssignment.id)
    ).all()
    return [
        {
            "id": str(assignment_id),
            "instance_id": str(scope_id),
            "user_id": str(user_id),
            "user_email": email or "(unknown user)",
        }
        for assignment_id, scope_id, user_id, email in rows
    ]


def _role_effective_access(
    db: Session, role_name: str, *, assigned_instance_count: int
) -> list[dict[str, str]]:
    """Return ``role_name``'s effective verb **and** tier per resource it reaches (Issue #173).

    Resolved exactly as the runtime gate resolves it — the role's inheritance closure, the resource
    tree, deny-beats-allow for the verb (:func:`~src.core.rbac.effective_verb_over_keys`) and the
    same bulk tier resolver the nav layer uses (:func:`~src.core.scope.scope_tiers_for_roles`) — so
    this panel cannot drift from what a request would actually decide. Resources the role reaches
    with no verb at all are omitted: the page answers "what can this role do", not "here is the
    catalog".
    """
    grants = load_effective_grant_keys_for_role(db, role_name)
    parent_by_key = load_resource_parent_map(db)
    keys = sorted(parent_by_key)
    tiers = scope_tiers_for_roles(db, [role_name], keys)
    access: list[dict[str, str]] = []
    for key in keys:
        verb = effective_verb_over_keys(grants, parent_by_key, key)
        if verb is None:
            continue
        tier = tiers[key].value
        access.append(
            {
                "resource": key,
                "verb": verb.upper(),
                "tier": tier,
                "tier_label": scope_tier_label(tier),
                "tier_meaning": scope_tier_meaning(
                    tier, assigned_instance_count=assigned_instance_count
                ),
            }
        )
    return access


@router.get("/admin/rbac/roles/{role_name}", response_class=HTMLResponse)
async def admin_rbac_role_detail(
    request: Request, role_name: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Full-detail page for one role — key facts, **effective access** and scoped assignments.

    The canonical ``/admin/rbac/roles/{name}`` destination the quick-view "Open full" link and the
    list "open full" icon land on (Issue #118). Gated like the RBAC console (``nav.visible("rbac")``);
    a missing role is an honest 404 inside the app shell.

    **Issue #173 (M28)** gave it the two panels the console could not previously answer with:

    * *Effective access* — per resource, the verb **and** the scope tier a holder of this role
      resolves, plus what that tier concretely means for this role ("assigned — 3 properties").
      A grant's breadth was authored (Issue #161) and enforced (Issues #156–#172) long before any
      screen showed it.
    * *Instance assignments* — the scoped ``user_roles`` rows that make an ``assigned`` grant
      mean anything. They are edited on ``/admin/rbac/users/{id}``; surfacing them here turns
      "grant assigned-tier READ, then assign these instances" into one flow with a link, instead
      of two screens that never mention each other.
    """
    from src.database.models import RbacRole, RolePermission, User

    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = page_context(request, db, active_nav="rbac", page_title=role_name)
    if not ctx["nav"].visible("rbac"):
        return _forbidden_html(request, db)

    row = db.execute(
        select(RbacRole).where(RbacRole.name == role_name)
    ).scalar_one_or_none()
    if row is None:
        return _not_found_html(request, db)

    user_count = int(
        db.execute(
            select(func.count()).select_from(User).where(User.role == row.name)
        ).scalar_one()
    )
    permission_count = int(
        db.execute(
            select(func.count())
            .select_from(RolePermission)
            .where(RolePermission.role == row.name)
        ).scalar_one()
    )
    role = {
        "name": row.name,
        "description": row.description,
        "is_system": row.is_system,
        "user_count": user_count,
        "permission_count": permission_count,
        "created_at": _detail_dt(row.created_at),
        "modified_at": _detail_dt(row.modified_at),
    }
    breadcrumbs = [
        {"label": "Access control", "href": "/admin/rbac/roles"},
        {"label": "Roles", "href": "/admin/rbac/roles"},
        {"label": row.name, "current": True},
    ]
    assignments = _role_instance_assignments(db, row.name)
    effective_access = _role_effective_access(
        db,
        row.name,
        assigned_instance_count=len({a["instance_id"] for a in assignments}),
    )
    # Direction cards, each RBAC-gated so a card never points somewhere the caller can't go.
    can = ctx["can"]
    nav_cards: list[dict[str, str]] = []
    if can("rbac", "update"):
        nav_cards.append(
            {
                "title": "Permissions",
                "description": "Edit this role's per-resource grants.",
                "href": "/admin/rbac/permissions",
                "icon": "permissions",
            }
        )
    if can("users", "read"):
        nav_cards.append(
            {
                "title": "Users",
                "description": "See who holds a role.",
                "href": "/admin/rbac/users",
                "icon": "users",
            }
        )
    if can("rbac", "update"):
        # The other half of an ``assigned``-tier grant (Issue #173): the tier says how a holder is
        # narrowed, the property assignments say to what. Both are needed, so both are one click
        # from the role that ties them together.
        nav_cards.append(
            {
                "title": "Assign instances",
                "description": "Scope a holder of this role to specific instances.",
                "href": "/admin/rbac/users",
                "icon": "list",
            }
        )
    nav_cards.append(
        {
            "title": "All roles",
            "description": "Back to the roles list.",
            "href": "/admin/rbac/roles",
            "icon": "list",
        }
    )
    ctx.update(
        role=role,
        breadcrumbs=breadcrumbs,
        nav_cards=nav_cards,
        effective_access=effective_access,
        instance_assignments=assignments,
    )
    return templates.TemplateResponse(request, "admin/rbac/role_detail.html", ctx)


@router.get("/admin/rbac/users/{user_id}", response_class=HTMLResponse)
async def admin_rbac_user_detail(
    request: Request, user_id: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Full-detail page for one user — breadcrumbs, key facts and navigation cards (Issue #118).

    The canonical ``/admin/rbac/users/{id}`` destination the quick-view "Open full" link and the list
    "open full" icon land on. Gated like the RBAC console *and* the separate ``users`` resource its
    write actions actually enforce (Issue #155 — same reasoning as the Users tab/section route),
    not just ``rbac``; a missing or soft-deleted user is a 404.
    """
    from src.database.models import User

    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = page_context(request, db, active_nav="rbac", page_title="User")
    if not ctx["nav"].visible("rbac") or not ctx["can"]("users", "read"):
        return _forbidden_html(request, db)

    row = db.execute(
        select(User).where(User.id == user_id, User.is_deleted.is_(False))
    ).scalar_one_or_none()
    if row is None:
        return _not_found_html(request, db)

    full_name = " ".join(p for p in (row.first_name, row.last_name) if p)
    user = {
        "id": str(row.id),
        "email": row.email,
        "role": row.role,
        "is_verified": bool(row.is_verified),
        "first_name": row.first_name,
        "last_name": row.last_name,
        "full_name": full_name,
        "last_login": _detail_dt(row.last_login),
        "created_at": _detail_dt(row.created_at),
    }
    breadcrumbs = [
        {"label": "Access control", "href": "/admin/rbac/roles"},
        {"label": "Users", "href": "/admin/rbac/users"},
        {"label": row.email, "current": True},
    ]
    can = ctx["can"]
    nav_cards: list[dict[str, str]] = []
    if can("rbac", "read"):
        nav_cards.append(
            {
                "title": "Permissions",
                "description": "See what this user's role can do.",
                "href": "/admin/rbac/permissions",
                "icon": "permissions",
            }
        )
    nav_cards.append(
        {
            "title": "All users",
            "description": "Back to the users list.",
            "href": "/admin/rbac/users",
            "icon": "list",
        }
    )
    ctx.update(user=user, breadcrumbs=breadcrumbs, nav_cards=nav_cards)
    return templates.TemplateResponse(request, "admin/rbac/user_detail.html", ctx)


_MESSAGES_SECTIONS = ("inbox", "sent", "deleted", "drafts")


@router.get("/admin/messages", response_class=HTMLResponse)
async def admin_messages(request: Request) -> RedirectResponse:
    """The bare messages URL sends to Inbox, so each sub-view has its own path (Issue #132 follow-up)."""
    return RedirectResponse(url="/admin/messages/inbox", status_code=302)


@router.get("/admin/messages/compose", response_class=HTMLResponse)
async def admin_message_compose(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Compose a new message in a full page (Issue #132 follow-up).

    Registered before the ``{section}`` route below so "compose" is never mistaken for an unknown
    section. Unlike Announcements/Alerts, a message thread is anchored to a record (a unit, lease,
    work order or application) rather than a free-form audience, so this page collects that anchor
    instead. Opening/sending happens client-side against the existing messaging API.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = page_context(request, db, active_nav="messages", page_title="New message")
    if not ctx["nav"].visible("messages"):
        return _forbidden_html(request, db)
        # Issue #168 (M28): a mutation-only page requires the verb it exists to exercise, not
        # the console's READ. A reader-only role was shown a full compose screen that could only
        # fail on submit — the click-then-403 this issue removes, one layer up from the button.
    if not ctx["nav"].can("communications.messages", "create"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(request, "admin/message_compose.html", ctx)


@router.get("/admin/messages/{section}", response_class=HTMLResponse)
async def admin_messages_section(
    request: Request, section: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Messaging console, one URL per sub-view: /admin/messages/{inbox|sent|deleted|drafts}.

    Read and reply to the threads the caller's own scope covers — the whole business for a
    ``business``-scoped grant, just their own participant threads for an ``own``-scoped one (the
    messaging API's ``resolve_scope`` call, Issue #157 on M28's shared resolver). Each sub-tab gates on its own section
    resource (``communications.messages.{section}``, Issue #145 — mirroring the maintenance
    sub-nav, Issue #143) via a pure permission check (``nav.can``, not the surface gate — Issue
    #157 dropped the extra management-role wall this used to layer on top), so a role scoped to
    one tab reaches only that tab regardless of its role type; a coarse ``communications.messages``
    grant still reaches every tab, via inheritance. An unknown section falls back to Inbox rather
    than 404ing.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    selected = section.strip().lower()
    if selected not in _MESSAGES_SECTIONS:
        return RedirectResponse(url="/admin/messages/inbox", status_code=302)  # type: ignore[return-value]
    ctx = page_context(
        request, db, active_nav="messages", page_title="Messages", messages_tab=selected
    )
    if not ctx["nav"].can(f"communications.messages.{selected}", "read"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(request, "admin/messages.html", ctx)


@router.get("/admin/messages/drafts/{draft_id}", response_class=HTMLResponse)
async def admin_message_draft_edit(
    request: Request, draft_id: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Full-page editor for one reply draft — the "Full edit" link from the Drafts quick-edit
    slider opens this in a new tab (Issue #132 follow-up). Fetches/saves client-side against the
    existing drafts API; the shell only carries the draft id and re-checks the same RBAC gate.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = page_context(
        request, db, active_nav="messages", page_title="Edit draft", draft_id=draft_id
    )
    if not ctx["nav"].visible("messages"):
        return _forbidden_html(request, db)
        # Issue #168 (M28): a mutation-only page requires the verb it exists to exercise, not
        # the console's READ. A reader-only role was shown a full draft screen that could only
        # fail on submit — the click-then-403 this issue removes, one layer up from the button.
    if not ctx["nav"].can("communications.messages", "create"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(request, "admin/message_draft_edit.html", ctx)


_ANNOUNCEMENT_SECTIONS = ("inbox", "sent", "drafts", "deleted")


@router.get("/admin/announcements", response_class=HTMLResponse)
async def admin_announcements(request: Request) -> RedirectResponse:
    """The bare announcements URL sends to Inbox (Issue #132 follow-up)."""
    return RedirectResponse(url="/admin/announcements/inbox", status_code=302)


@router.get("/admin/announcements/compose", response_class=HTMLResponse)
async def admin_announcement_compose(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Compose an announcement in a full page, opened in a new tab (Issue #132 follow-up).

    Registered before the ``{section}`` route below so "compose" is never mistaken for an unknown
    section. Composing/sending happens client-side against the existing announcements API.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = page_context(
        request, db, active_nav="announcements", page_title="New announcement"
    )
    if not ctx["nav"].visible("announcements"):
        return _forbidden_html(request, db)
        # Issue #168 (M28): a mutation-only page requires the verb it exists to exercise, not
        # the console's READ. A reader-only role was shown a full compose screen that could only
        # fail on submit — the click-then-403 this issue removes, one layer up from the button.
    if not ctx["nav"].can("communications.announcements", "read"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(request, "admin/announcement_compose.html", ctx)


@router.get("/admin/announcements/drafts/{draft_id}", response_class=HTMLResponse)
async def admin_announcement_draft_edit(
    request: Request, draft_id: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Full-page editor for one announcement draft, opened from its quick-edit slider."""
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = page_context(
        request,
        db,
        active_nav="announcements",
        page_title="Edit draft",
        draft_id=draft_id,
    )
    if not ctx["nav"].visible("announcements"):
        return _forbidden_html(request, db)
        # Issue #168 (M28): a mutation-only page requires the verb it exists to exercise, not
        # the console's READ. A reader-only role was shown a full draft screen that could only
        # fail on submit — the click-then-403 this issue removes, one layer up from the button.
    if not ctx["nav"].can("communications.announcements", "read"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(
        request, "admin/announcement_draft_edit.html", ctx
    )


@router.get("/admin/announcements/{section}", response_class=HTMLResponse)
async def admin_announcements_section(
    request: Request, section: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Announcements console, one URL per sub-view: /admin/announcements/{inbox|sent|drafts|deleted}.

    Promoted off the old /admin/messages collapsible section into its own console (Issue #132
    follow-up). Each sub-tab gates on its own section resource
    (``communications.announcements.{section}``, Issue #145) rather than the coarse module grant,
    so a role scoped to one tab reaches only that tab; a coarse ``communications.announcements``
    grant still reaches every tab, via inheritance. An unknown section falls back to Inbox rather
    than 404ing.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    selected = section.strip().lower()
    if selected not in _ANNOUNCEMENT_SECTIONS:
        return RedirectResponse(url="/admin/announcements/inbox", status_code=302)  # type: ignore[return-value]
    ctx = page_context(
        request,
        db,
        active_nav="announcements",
        page_title="Announcements",
        announcements_tab=selected,
    )
    if not ctx["nav"].can_surface(f"communications.announcements.{selected}"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(request, "admin/announcements.html", ctx)


_ALERT_SECTIONS = ("inbox", "sent", "drafts", "deleted")


@router.get("/admin/alerts", response_class=HTMLResponse)
async def admin_alerts(request: Request) -> RedirectResponse:
    """The bare alerts URL sends to Inbox (Issue #132 follow-up)."""
    return RedirectResponse(url="/admin/alerts/inbox", status_code=302)


@router.get("/admin/alerts/compose", response_class=HTMLResponse)
async def admin_alert_compose(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Compose a staff alert in a full page, opened in a new tab (Issue #132 follow-up).

    Registered before the ``{section}`` route below so "compose" is never mistaken for an unknown
    section. Composing/sending happens client-side against the alerts API.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = page_context(request, db, active_nav="alerts", page_title="New alert")
    if not ctx["nav"].visible("alerts"):
        return _forbidden_html(request, db)
        # Issue #168 (M28): a mutation-only page requires the verb it exists to exercise, not
        # the console's READ. A reader-only role was shown a full compose screen that could only
        # fail on submit — the click-then-403 this issue removes, one layer up from the button.
    if not ctx["nav"].can("communications.alerts", "create"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(request, "admin/alert_compose.html", ctx)


@router.get("/admin/alerts/drafts/{draft_id}", response_class=HTMLResponse)
async def admin_alert_draft_edit(
    request: Request, draft_id: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Full-page editor for one alert draft, opened from its quick-edit slider."""
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = page_context(
        request, db, active_nav="alerts", page_title="Edit draft", draft_id=draft_id
    )
    if not ctx["nav"].visible("alerts"):
        return _forbidden_html(request, db)
        # Issue #168 (M28): a mutation-only page requires the verb it exists to exercise, not
        # the console's READ. A reader-only role was shown a full draft screen that could only
        # fail on submit — the click-then-403 this issue removes, one layer up from the button.
    if not ctx["nav"].can("communications.alerts", "create"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(request, "admin/alert_draft_edit.html", ctx)


@router.get("/admin/alerts/{section}", response_class=HTMLResponse)
async def admin_alerts_section(
    request: Request, section: str, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Alerts console, one URL per sub-view: /admin/alerts/{inbox|sent|drafts|deleted}.

    A staff-authored alert (Issue #132 follow-up). Each sub-tab gates on its own section resource
    (``communications.alerts.{section}``, Issue #145) rather than the coarse module grant, so a
    role scoped to one tab reaches only that tab; a coarse ``communications.alerts`` grant still
    reaches every tab, via inheritance. An unknown section falls back to Inbox rather than 404ing.
    """
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    selected = section.strip().lower()
    if selected not in _ALERT_SECTIONS:
        return RedirectResponse(url="/admin/alerts/inbox", status_code=302)  # type: ignore[return-value]
    ctx = page_context(
        request, db, active_nav="alerts", page_title="Alerts", alerts_tab=selected
    )
    if not ctx["nav"].can_surface(f"communications.alerts.{selected}"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(request, "admin/alerts.html", ctx)


@router.get("/admin/widgets", response_class=HTMLResponse)
async def admin_widgets(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """The example module's console — the shape a page in this app has.

    Authenticate, check the surface's own grant, resolve the caller's narrowing **once**, and hand
    it to the service. The narrowing call is the same one
    :func:`src.modules.widgets.router.list_widgets` makes, which is what keeps the page and the API
    behind it showing the same rows to the same caller.
    """
    from src.core.scope import instance_ids_in_scope
    from src.modules.widgets import service as widgets_service

    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = page_context(request, db, active_nav="widgets", page_title="Widgets")
    if not ctx["nav"].visible("widgets"):
        return _forbidden_html(request, db)
    user = peek_user_from_refresh_cookie(db, request)
    instance_ids = (
        instance_ids_in_scope(db, user, "widgets") if user is not None else set()
    )
    ctx["widgets"] = widgets_service.list_widgets(db, instance_ids=instance_ids)
    return templates.TemplateResponse(request, "admin/widgets.html", ctx)


@router.get("/admin/notifications", response_class=HTMLResponse)
async def admin_notifications(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Notification delivery viewer — recipient, channel, type, status and failure reason per
    message (drives the notifications list API, gated by the ``logs`` RBAC verb)."""
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = page_context(
        request, db, active_nav="notifications", page_title="Notifications"
    )
    if not ctx["nav"].visible("notifications"):
        return _forbidden_html(request, db)
    return templates.TemplateResponse(request, "admin/notifications.html", ctx)


def _logs_page_context(request: Request, db: Session) -> dict:
    now_jhb = datetime.now(APP_TIMEZONE)
    return page_context(
        request,
        db,
        logs_default_year=now_jhb.year,
        logs_default_month=now_jhb.month,
        logs_default_day=now_jhb.day,
    )


@router.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if not require_authenticated_html(request, db):
        return _redirect_to_sign_in(request)  # type: ignore[return-value]
    ctx = _logs_page_context(request, db)
    ctx["active_nav"] = "logs"
    ctx["page_title"] = "Application logs"
    if not ctx["nav"].visible("logs"):
        return _forbidden_html(request, db)
    # This user may view logs, so warm the default (today, all levels) S3 listing in the background
    # once the page has been sent — a sync task, so Starlette runs it off the event loop. By the time
    # the page's JS calls the list API, the full listing is usually cached and pages instantly.
    background_tasks.add_task(
        warm_logs_listing,
        level=S3LogListingLevel.ALL,
        path_filter=None,
        year=ctx["logs_default_year"],
        month=ctx["logs_default_month"],
        day=ctx["logs_default_day"],
        keyword=None,
    )
    return templates.TemplateResponse(request, "logs.html", ctx)
