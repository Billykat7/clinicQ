"""HTTP routes for a clinic's profile (Issue 23).

The shape is the widgets example's, with the one difference every ClinicQ module has: **a route
that names a clinic goes through the site guard**, not through the generic instance scope. So
there are two groups here, and the split is the whole authorization story:

* **Platform routes** — listing every clinic, creating one, deleting one, and the geocoding proxy.
  These have no ``{site_id}`` to be scoped by, so they gate on a ``business``-tier grant
  (:func:`~src.api.rbac_deps.require` with ``scope=BUSINESS``), which only ``platform_admin``
  holds. The listing is still narrowed: a caller below that tier sees the clinics they hold a role
  at, and nothing else.
* **Clinic routes** — reading and editing one clinic. These take
  :func:`~src.core.site_scope.require_site_access`, so another clinic's id answers **404**, with
  the same body an id that never existed gets (non-negotiable 3), and the verb is resolved with the
  roles the caller holds *at that clinic*.

Every mutation writes an :class:`~src.database.models.audit_event.AuditEvent` before the commit, so
the change and its trail land in one transaction.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from src.api.rbac_deps import CurrentUser, DbSession, require
from src.commons.enums import (
    AuditAction,
    AuditEntityType,
    BoardLanguage,
    DisplayMode,
    GrantScope,
    SiteSector,
    SiteStatus,
    TransferPlacement,
)
from src.commons.time import business_date, now_sast
from src.core.audit import record_audit_event
from src.core.client_ip import resolve_client_ip
from src.core.config import Settings, get_settings
from src.core.domain_events import BoardSettingsChanged, publish_after_commit
from src.core.request_logging import bind_request_context
from src.core.security import CurrentStaff
from src.core.site_scope import (
    SiteAccess,
    require_site_access,
    site_ids_in_scope,
    site_not_found,
)
from src.database.models.site import Site
from src.modules.appointments.call_forward import (
    DEFAULT_TRAVEL_MINUTES,
    VIRTUAL_WAITING_EXPLANATION,
)
from src.modules.discovery import analytics as discovery_analytics
from src.modules.queue.timers import timeout_minutes
from src.modules.sites import (
    catalogue,
    hours_service,
    onboarding,
    payment_profile,
    service,
)
from src.modules.sites import settings as display_settings
from src.modules.sites.availability import join_gate
from src.modules.sites.geocoding import GeocodingUnavailableError, geocode_address
from src.modules.sites.hours import open_state, schedule_for
from src.modules.sites.schemas import (
    AcceptedSchemeOut,
    AnalyticsSettingsIn,
    AnalyticsSettingsOut,
    BoardThemeOptionOut,
    ClinicServiceIn,
    ClinicServiceListOut,
    ClinicServiceOut,
    ClosureIn,
    ClosureListOut,
    ClosureOut,
    DiscoveryConversionOut,
    DisplayModeOptionOut,
    DisplayOptionsOut,
    DisplaySettingsIn,
    DisplaySettingsOut,
    GeocodeCandidateOut,
    GeocodeIn,
    GeocodeOut,
    HolidayListOut,
    HolidayOut,
    HolidayRuleIn,
    OpenStateOut,
    PaymentProfileIn,
    PaymentProfileOut,
    RecallSettingsIn,
    RecallSettingsOut,
    SchemeOptionOut,
    SiteIn,
    SiteListOut,
    SiteLocationOut,
    SiteOut,
    SiteRegistrationIn,
    SiteRegistrationOut,
    TransferSettingsIn,
    TransferSettingsOut,
    VerificationDecisionIn,
    VerificationQueueItemOut,
    VerificationQueueOut,
    VirtualWaitingSettingsIn,
    VirtualWaitingSettingsOut,
    WeeklyHoursIn,
    WeeklyHoursOut,
)

router = APIRouter(prefix="/sites", tags=["sites"])

_MAX_LIMIT = 200

#: The operator's console: the whole directory, creating a clinic, removing one. ``business`` is
#: the tier ``platform_admin`` holds on ``sites``. A clinic manager's grant is ``assigned`` and does
#: **not** satisfy these — deliberately: a manager reaches their clinic by its id (below), and their
#: "which clinics do I work at" list is the site switcher's, built on assignments (Issue 28).
SitesDirectory = Annotated[
    None, Depends(require("sites", "read", scope=GrantScope.BUSINESS))
]
SitesCreate = Annotated[
    None, Depends(require("sites", "create", scope=GrantScope.BUSINESS))
]
SitesDelete = Annotated[
    None, Depends(require("sites", "delete", scope=GrantScope.BUSINESS))
]

#: One clinic, named in the path: the site guard answers "whose clinic" before the verb, and the
#: verb is resolved with the roles the caller holds *at that clinic*.
SiteProfileRead = Annotated[
    SiteAccess, Depends(require_site_access("sites.profile", "read"))
]
SiteProfileUpdate = Annotated[
    SiteAccess, Depends(require_site_access("sites.profile", "update"))
]
#: Opening hours, holiday rules and closures are the clinic's profile: a receptionist reads them,
#: a clinic manager changes them, and closing the clinic is the same grant as changing its hours
#: because it is the same decision made at short notice.
SiteHoursRead = SiteProfileRead
SiteHoursUpdate = SiteProfileUpdate

#: The board's display mode is its own resource (``sites.display``), so a receptionist can be shown
#: what the screen is set to without being able to change it, and changing it is the clinic
#: manager's alone — non-negotiable 4.
SiteDisplayRead = Annotated[
    SiteAccess, Depends(require_site_access("sites.display", "read"))
]
SiteDisplayUpdate = Annotated[
    SiteAccess, Depends(require_site_access("sites.display", "update"))
]

#: Operational settings (``sites.settings``) and the clinic's reports (``sites.reports``) are the
#: clinic manager's: a receptionist neither sees nor changes them (Issue 38).
#: The application's settings, for defaults a clinic's own setting falls back to.
SettingsDep = Annotated[Settings, Depends(get_settings)]
SiteSettingsRead = Annotated[
    SiteAccess, Depends(require_site_access("sites.settings", "read"))
]
SiteSettingsUpdate = Annotated[
    SiteAccess, Depends(require_site_access("sites.settings", "update"))
]
SiteReportsRead = Annotated[
    SiteAccess, Depends(require_site_access("sites.reports", "read"))
]

#: The services catalogue is part of the clinic's profile: the front desk reads it (it is what a
#: walk-in is asked which of), and the clinic manager decides what is on it.
SiteServicesRead = SiteProfileRead
SiteServicesUpdate = SiteProfileUpdate


def _audit(
    db: Session,
    request: Request,
    actor: str,
    actor_id: str | None,
    action: AuditAction,
    site_id: str,
    context: str,
) -> None:
    """Record one clinic mutation. Called before the commit so both land in one transaction.

    The site is bound into the request context first, because
    :func:`~src.core.audit.record_audit_event` reads ``site_id`` from there — and on the platform
    routes (creating a clinic, removing one) nothing has bound it: the site guard, which normally
    does, is deliberately not on those. Without this line a clinic's own audit trail (Issue 20)
    would be missing the row that created it.
    """
    bind_request_context(site_id=site_id)
    record_audit_event(
        db,
        action=action,
        entity_type=AuditEntityType.SITE,
        entity_id=site_id,
        actor=actor,
        actor_id=actor_id,
        ip_address=resolve_client_ip(request),
        context=context,
    )


def _claims_actor(current_user: dict[str, Any]) -> tuple[str, str | None]:
    """Return ``(actor label, actor id)`` from the caller's token claims."""
    actor = current_user.get("email") or current_user.get("sub") or "unknown"
    return str(actor), current_user.get("uid")


@router.get("/info", summary="Module metadata", operation_id="sitesInfo")
def sites_info() -> dict[str, str]:
    """Return sites module metadata (unauthenticated, like every other ``/info``)."""
    info = service.get_module_info()
    return {"context": info.context.value, "summary": info.summary}


def _geocode(address: str) -> GeocodeOut:
    """Ask the configured provider and shape the answer. Shared by the two routes below.

    **The browser never calls a geocoder.** The Content-Security-Policy allows ``connect-src
    'self'`` only, so the form posts to ClinicQ and ClinicQ makes the outbound request
    (:mod:`src.modules.sites.geocoding`): one egress point, one timeout, one place a provider
    credential lives. A deployment with no provider answers 503 and the operator types the
    coordinate in, which is a supported path rather than a failure mode.
    """
    try:
        places = geocode_address(address)
    except GeocodingUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return GeocodeOut(
        query=address,
        candidates=[
            GeocodeCandidateOut(
                label=place.label, location=SiteLocationOut.of(place.point)
            )
            for place in places
        ],
    )


@router.post(
    "/geocode",
    response_model=GeocodeOut,
    operation_id="sitesGeocodeAddress",
    summary="Look up a typed address while onboarding a clinic",
)
def geocode(payload: GeocodeIn, _authz: SitesCreate) -> GeocodeOut:
    """Candidate coordinates for an address, for the operator putting a new clinic on the map."""
    return _geocode(payload.address)


@router.post(
    "/{site_id}/geocode",
    response_model=GeocodeOut,
    operation_id="sitesGeocodeAddressForSite",
    summary="Look up a typed address for a clinic that already exists",
)
def geocode_for_site(payload: GeocodeIn, _access: SiteProfileUpdate) -> GeocodeOut:
    """The same proxy for a manager correcting their own clinic's address.

    A separate route rather than a wider grant on the one above: a clinic manager's role is held
    **at a site**, so it only resolves on a route that names one (Issue 19). The clinic in the path
    is checked before the lookup, so another clinic's id is a 404 here too.
    """
    return _geocode(payload.address)


# --------------------------------------------------------------------------------------
# Onboarding and verification (Issue 29)
#
# Declared **before** ``/{site_id}``: FastAPI matches in declaration order, so a literal segment
# that could also be read as an id has to come first.
# --------------------------------------------------------------------------------------

#: What a submitter is told. Server-side, so the page, a channel and a curl all say the same thing.
SUBMITTED_MESSAGE = (
    "Thank you. A ClinicQ administrator will check these details and get in touch. Your clinic "
    "will not appear to patients until it has been checked."
)


@router.post(
    "/register",
    response_model=SiteRegistrationOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="sitesRegisterPublic",
    summary="Submit a clinic for checking",
)
def register_clinic(
    payload: SiteRegistrationIn, db: DbSession, request: Request
) -> SiteRegistrationOut:
    """The public registration form's endpoint. **No session, and it grants nothing.**

    A submission creates a clinic in ``pending_verification`` with the submitter's contact details
    on it, and no account, no role and nothing visible to a patient. It is safe to leave open for
    exactly that reason: the worst a stranger can do is put an entry in a queue a platform admin
    reads, and a rejected entry is invisible for its whole life.
    """
    try:
        site = onboarding.submit_registration(db, payload)
    except service.SlugAlreadyUsedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    record_audit_event(
        db,
        action=AuditAction.CREATE,
        entity_type=AuditEntityType.SITE,
        entity_id=site.id,
        actor=str(payload.contact_email),
        actor_id=None,
        ip_address=resolve_client_ip(request),
        context=f"submitted {site.slug} for verification",
    )
    db.commit()
    db.refresh(site)
    return SiteRegistrationOut(
        id=site.id,
        slug=site.slug,
        name=site.name,
        status=site.status_enum,
        submitted_at=site.submitted_at,
        message=SUBMITTED_MESSAGE,
    )


@router.get(
    "/verification",
    response_model=VerificationQueueOut,
    operation_id="sitesVerificationQueue",
    summary="Clinics waiting for a platform admin",
)
def verification_queue(
    db: DbSession,
    _authz: SitesDirectory,
    queue_status: Annotated[SiteStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> VerificationQueueOut:
    """The operator's queue, oldest submission first.

    Behind a ``business``-tier grant on ``sites``, which only ``platform_admin`` holds. This is a
    cross-clinic console by nature, so it is not behind the site guard: that guard's job is to stop
    one clinic reading another, and it would 404 the operator, who is assigned to none of them.
    """
    wanted = queue_status or SiteStatus.PENDING_VERIFICATION
    rows = list(
        db.execute(onboarding.pending_queue(db, status=wanted, query=q)).scalars()
    )
    return VerificationQueueOut(
        total=len(rows),
        status=wanted,
        items=[_queue_item(row) for row in rows],
    )


def _queue_item(site: Site) -> VerificationQueueItemOut:
    """One clinic as the verification console lists it."""
    return VerificationQueueItemOut(
        id=site.id,
        slug=site.slug,
        name=site.name,
        sector=SiteSector(site.sector),
        status=site.status_enum,
        city=site.city,
        suburb=site.suburb,
        province=site.province_enum,
        contact_name=site.contact_name,
        contact_email=site.contact_email,
        contact_phone=site.contact_phone,
        submitted_at=site.submitted_at,
        reviewed_at=site.reviewed_at,
        review_note=site.review_note,
    )


@router.put(
    "/{site_id}/verification",
    response_model=VerificationQueueItemOut,
    operation_id="sitesDecideVerification",
    summary="Approve, reject, ask for more, or suspend",
)
def decide_verification(
    site_id: str,
    payload: VerificationDecisionIn,
    request: Request,
    db: DbSession,
    staff: CurrentStaff,
    _authz: SitesDelete,
) -> VerificationQueueItemOut:
    """Move one clinic's listing. The only route that writes ``site.status``.

    Approving makes it visible to patients; suspending stops joins **immediately**, because the
    join gate and the patient-facing search both read the status on every request rather than from
    a list somebody has to rebuild. Rejecting or asking for more information must say what is
    wrong: the submitter is shown exactly what the admin writes.
    """
    site = service.get_site(db, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    try:
        onboarding.transition(
            db,
            site,
            payload.status,
            decided_by=staff,
            note=payload.note,
            ip_address=resolve_client_ip(request),
        )
    except onboarding.TransitionNotAllowedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except onboarding.ReviewNoteRequiredError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.commit()
    db.refresh(site)
    return _queue_item(site)


@router.get("", response_model=SiteListOut, operation_id="sitesList")
def list_sites(
    db: DbSession,
    staff: CurrentStaff,
    _authz: SitesDirectory,
    sector: SiteSector | None = None,
    site_status: Annotated[SiteStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SiteListOut:
    """List the clinics this caller may see, by name.

    A platform admin sees the platform; everyone else sees the clinics they hold a role at, which
    is the same set the site guard admits one at a time.
    """
    return service.list_sites(
        db,
        site_ids=site_ids_in_scope(db, staff, "sites.profile"),
        sector=sector,
        status=site_status,
        query=q,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=SiteOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="sitesCreate",
)
def create_site(
    payload: SiteIn,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    _authz: SitesCreate,
) -> SiteOut:
    """Create a clinic. It starts as a draft, whatever the caller asks for."""
    try:
        site = service.create_site(db, payload)
    except service.SlugAlreadyUsedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    actor, actor_id = _claims_actor(current_user)
    _audit(
        db,
        request,
        actor,
        actor_id,
        AuditAction.CREATE,
        site.id,
        f"created clinic {site.slug} ({site.status})",
    )
    db.commit()
    db.refresh(site)
    return service.site_out(site)


@router.get("/{site_id}", response_model=SiteOut, operation_id="sitesGet")
def get_site(access: SiteProfileRead, db: DbSession) -> SiteOut:
    """Return one clinic's profile. Another clinic's id is a 404, like an id that never existed."""
    site = _site_or_404(db, access)
    return service.site_out(site)


@router.put("/{site_id}", response_model=SiteOut, operation_id="sitesUpdate")
def update_site(
    payload: SiteIn,
    request: Request,
    access: SiteProfileUpdate,
    db: DbSession,
) -> SiteOut:
    """Replace a clinic's editable profile fields; records an UPDATE audit event."""
    site = _site_or_404(db, access)
    try:
        service.update_site(db, site, payload)
    except service.SlugAlreadyUsedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    # A clinic that is now public keeps no payment profile (Issue 37): the rule is the server's.
    dropped = payment_profile.remove_if_public(db, access, site)
    _audit(
        db,
        request,
        access.user.email,
        str(access.user.id),
        AuditAction.UPDATE,
        site.id,
        f"updated the profile of {site.slug}"
        + ("; removed its payment profile, as a public clinic" if dropped else ""),
    )
    db.commit()
    db.refresh(site)
    return service.site_out(site)


@router.delete(
    "/{site_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="sitesDelete",
)
def delete_site(
    site_id: str,
    request: Request,
    db: DbSession,
    staff: CurrentStaff,
    _authz: SitesDelete,
) -> None:
    """Soft-delete a clinic. The operator's action: a manager cannot remove their own clinic.

    Not behind :func:`~src.core.site_scope.require_site_access`, because a platform admin is
    deliberately assigned to no clinic (Issue 19) and the guard would 404 them here. The
    ``business``-tier gate above is what authorises it, and the removal is audited.
    """
    site = service.get_site(db, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    service.delete_site(db, site)
    _audit(
        db,
        request,
        staff.email,
        str(staff.id),
        AuditAction.DELETE,
        site.id,
        f"removed clinic {site.slug} from the directory",
    )
    db.commit()


# --------------------------------------------------------------------------------------
# Opening hours, public holidays and closures (Issue 24)
#
# Declared after ``/{site_id}`` deliberately: FastAPI matches in declaration order, and these are
# all longer paths under the same prefix, so nothing here can be swallowed by it.
# --------------------------------------------------------------------------------------


@router.get(
    "/{site_id}/open",
    response_model=OpenStateOut,
    operation_id="sitesOpenState",
    summary="Is this clinic open, and is it taking patients?",
)
def site_open_state(access: SiteHoursRead, db: DbSession) -> OpenStateOut:
    """One answer for discovery, the board and all four channel menus.

    ``accepting_joins`` is the **server's** decision (:mod:`src.modules.sites.availability`) and is
    a function of the clinic and the moment, never of the channel asking — which is what makes a
    closure stop web, USSD, WhatsApp and walk-in joins at the same instant.
    """
    site = _site_or_404(db, access)
    schedule = schedule_for(db, access)
    state = open_state(schedule)
    gate = join_gate(site, schedule)
    return OpenStateOut(
        site_id=site.id,
        is_open=state.is_open,
        next_open_at=state.next_open_at,
        closure_reason=state.closure_reason,
        accepting_joins=gate.allowed,
        refusal=gate.reason,
    )


@router.get(
    "/{site_id}/hours", response_model=WeeklyHoursOut, operation_id="sitesGetHours"
)
def get_hours(access: SiteHoursRead, db: DbSession) -> WeeklyHoursOut:
    """The clinic's ordinary week, all seven days."""
    return hours_service.weekly_hours(db, access)


@router.put(
    "/{site_id}/hours", response_model=WeeklyHoursOut, operation_id="sitesSetHours"
)
def set_hours(
    payload: WeeklyHoursIn,
    request: Request,
    access: SiteHoursUpdate,
    db: DbSession,
) -> WeeklyHoursOut:
    """Replace the weekdays the payload names; records an UPDATE audit event."""
    hours = hours_service.replace_weekly_hours(db, access, payload)
    _audit(
        db,
        request,
        access.user.email,
        str(access.user.id),
        AuditAction.UPDATE,
        access.site_id,
        f"set opening hours for {len(payload.days)} weekday(s)",
    )
    db.commit()
    return hours


@router.get(
    "/{site_id}/holidays",
    response_model=HolidayListOut,
    operation_id="sitesListHolidays",
)
def list_holidays(access: SiteHoursRead, db: DbSession) -> HolidayListOut:
    """The public-holiday calendar, with this clinic's answer for each one."""
    return hours_service.holiday_calendar(db, access)


@router.put(
    "/{site_id}/holidays/{holiday_date}",
    response_model=HolidayOut,
    operation_id="sitesSetHolidayRule",
)
def set_holiday_rule(
    holiday_date: date,
    payload: HolidayRuleIn,
    request: Request,
    access: SiteHoursUpdate,
    db: DbSession,
) -> HolidayOut:
    """Say whether this clinic opens on one public holiday, and for which hours."""
    try:
        rule = hours_service.set_holiday_rule(db, access, holiday_date, payload)
    except hours_service.HolidayNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    _audit(
        db,
        request,
        access.user.email,
        str(access.user.id),
        AuditAction.UPDATE,
        access.site_id,
        f"{holiday_date}: {'open' if rule.is_open else 'closed'} ({rule.name})",
    )
    db.commit()
    return rule


@router.get(
    "/{site_id}/closures",
    response_model=ClosureListOut,
    operation_id="sitesListClosures",
)
def list_closures(
    access: SiteHoursRead,
    db: DbSession,
    include_past: Annotated[bool, Query()] = False,
) -> ClosureListOut:
    """The clinic's closures. Live ones by default; past ones are kept and shown when asked for."""
    return hours_service.list_closures(db, access, include_past=include_past)


@router.post(
    "/{site_id}/closures",
    response_model=ClosureOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="sitesAnnounceClosure",
)
def announce_closure(
    payload: ClosureIn,
    request: Request,
    access: SiteHoursUpdate,
    db: DbSession,
) -> ClosureOut:
    """Close the clinic, with a reason patients will be shown.

    New joins stop on every channel the moment this commits, because every channel asks the same
    gate. The people already holding a ticket are told by the notification service (Issue 63),
    which subscribes to the event this raises: nothing is sent from here, so a clinic can still
    close when the SMS gateway is down.
    """
    try:
        closure = hours_service.announce_closure(db, access, payload)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    _audit(
        db,
        request,
        access.user.email,
        str(access.user.id),
        AuditAction.CREATE,
        access.site_id,
        f"closed the clinic: {closure.reason}",
    )
    db.commit()
    db.refresh(closure)
    return ClosureOut.model_validate(closure)


@router.delete(
    "/{site_id}/closures/{closure_id}",
    response_model=ClosureOut,
    operation_id="sitesLiftClosure",
)
def lift_closure(
    closure_id: str,
    request: Request,
    access: SiteHoursUpdate,
    db: DbSession,
) -> ClosureOut:
    """End a closure early. The row stays, so "why were we shut on the 14th" stays answerable."""
    try:
        closure = hours_service.lift_closure(db, access, closure_id)
    except hours_service.ClosureNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    _audit(
        db,
        request,
        access.user.email,
        str(access.user.id),
        AuditAction.UPDATE,
        access.site_id,
        "lifted the closure early",
    )
    db.commit()
    db.refresh(closure)
    return ClosureOut.model_validate(closure)


# --------------------------------------------------------------------------------------
# Display and privacy settings (Issue 27, non-negotiable 4)
# --------------------------------------------------------------------------------------


def _settings_out(site: Site) -> DisplaySettingsOut:
    """One clinic's display settings, with the warning that describes what they mean."""
    warning = display_settings.warning_for(
        display_mode=site.display_mode_enum, show_comment=site.display_show_comment
    )
    return DisplaySettingsOut(
        site_id=site.id,
        display_mode=site.display_mode_enum,
        display_show_comment=site.display_show_comment,
        board_language=site.board_language_enum,
        board_theme=site.board_theme_enum,
        announce_audio=site.announce_audio,
        announce_volume=site.announce_volume,
        reason_retention_days=site.reason_retention_days,
        reason_retention_ceiling_days=display_settings.REASON_RETENTION_CEILING_DAYS,
        warning_lines=list(warning.lines),
    )


@router.get(
    "/{site_id}/settings/display-options",
    response_model=DisplayOptionsOut,
    operation_id="sitesDisplayOptions",
    summary="What this clinic may choose, and what each choice means",
)
def display_options(_access: SiteDisplayRead) -> DisplayOptionsOut:
    """The modes, the languages, the retention bounds and the warning text.

    Served rather than hardcoded in the screen so the sentence a manager reads and the rule the
    server enforces come from the same place (:mod:`src.modules.sites.settings`). The failure this
    prevents is a screen that reassures somebody about a setting the server treats differently.

    Under ``/{site_id}/`` even though the answer is the same everywhere, because a clinic manager's
    role is held **at a site** and only resolves on a route that names one — and because the
    warning is about *this clinic's* screen. The first version of this endpoint had no site in the
    path, and the settings page 403'd for the only role that opens it.
    """
    return DisplayOptionsOut(
        modes=[
            DisplayModeOptionOut(
                value=mode,
                warning=display_settings.DISPLAY_MODE_WARNINGS[mode],
                requires_confirmation=display_settings.warning_for(
                    display_mode=mode, show_comment=False
                ).requires_confirmation,
            )
            for mode in DisplayMode
        ],
        languages=list(BoardLanguage),
        themes=[
            BoardThemeOptionOut(value=theme, label=label, description=description)
            for theme, (
                label,
                description,
            ) in display_settings.BOARD_THEME_CHOICES.items()
        ],
        retention_floor_days=display_settings.REASON_RETENTION_FLOOR_DAYS,
        retention_ceiling_days=display_settings.REASON_RETENTION_CEILING_DAYS,
        comment_warning=display_settings.COMMENT_WARNING,
        comment_with_full_name_warning=display_settings.COMMENT_WITH_FULL_NAME_WARNING,
    )


@router.get(
    "/{site_id}/settings/display",
    response_model=DisplaySettingsOut,
    operation_id="sitesGetDisplaySettings",
)
def get_display_settings(access: SiteDisplayRead, db: DbSession) -> DisplaySettingsOut:
    """What this clinic's board is set to show. A receptionist may read it, and no more."""
    return _settings_out(_site_or_404(db, access))


@router.put(
    "/{site_id}/settings/display",
    response_model=DisplaySettingsOut,
    operation_id="sitesSetDisplaySettings",
)
def set_display_settings(
    payload: DisplaySettingsIn,
    request: Request,
    access: SiteDisplayUpdate,
    db: DbSession,
) -> DisplaySettingsOut:
    """Change what the waiting-room board may show.

    Three things happen here and nowhere else (non-negotiable 4): the grant decides **who**
    (``sites.display:update``, the clinic manager's), the confirmation decides **whether** — a
    change that newly puts a name or a reason on a public screen is refused without an explicit
    one, so a client that renders no warning cannot make it — and the audit row records **what
    moved**, field by field.
    """
    site = _site_or_404(db, access)
    try:
        _, changed = display_settings.apply_display_settings(
            site,
            display_settings.DisplaySettingsChange(
                display_mode=payload.display_mode,
                show_comment=payload.display_show_comment,
                board_language=payload.board_language,
                announce_audio=payload.announce_audio,
                announce_volume=payload.announce_volume,
                retention_days=payload.reason_retention_days,
                board_theme=payload.board_theme,
            ),
            confirm_public_display=payload.confirm_public_display,
            confirm_comment_with_full_name=payload.confirm_comment_with_full_name,
        )
    except display_settings.ConfirmationRequiredError as exc:
        # 409, not 403: the caller *may* do this, and has not yet said they understand it.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except display_settings.RetentionOutOfRangeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    if changed:
        _audit(
            db,
            request,
            access.user.email,
            str(access.user.id),
            AuditAction.UPDATE,
            access.site_id,
            "display settings: " + "; ".join(changed),
        )
        # Every open screen of the clinic hears about it once this commits (Issue 49).
        publish_after_commit(db, BoardSettingsChanged(site_id=access.site_id))
    db.commit()
    db.refresh(site)
    return _settings_out(site)


# --------------------------------------------------------------------------------------
# The services catalogue (Issue 26)
# --------------------------------------------------------------------------------------


@router.get(
    "/{site_id}/services",
    response_model=ClinicServiceListOut,
    operation_id="sitesListServices",
)
def list_services(
    access: SiteServicesRead,
    db: DbSession,
    include_inactive: Annotated[bool, Query()] = True,
) -> ClinicServiceListOut:
    """What this clinic offers, in the order the clinic put it in.

    Deactivated services are included by default, because a manager's screen has to show one in
    order to bring it back; anything a patient chooses from asks for ``include_inactive=false``.
    """
    return catalogue.list_services(db, access, include_inactive=include_inactive)


@router.post(
    "/{site_id}/services",
    response_model=ClinicServiceOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="sitesCreateService",
)
def create_service(
    payload: ClinicServiceIn,
    request: Request,
    access: SiteServicesUpdate,
    db: DbSession,
) -> ClinicServiceOut:
    """Add a service to this clinic's catalogue; records a CREATE audit event."""
    try:
        service_row = catalogue.create_service(db, access, payload)
    except catalogue.ServiceNameTakenError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    _audit_entity(
        db,
        request,
        access,
        AuditAction.CREATE,
        AuditEntityType.CLINIC_SERVICE,
        service_row.id,
        f"added {service_row.name!r} ({service_row.expected_minutes} min)",
    )
    db.commit()
    db.refresh(service_row)
    return catalogue.service_out(service_row)


@router.put(
    "/{site_id}/services/{service_id}",
    response_model=ClinicServiceOut,
    operation_id="sitesUpdateService",
)
def update_service(
    service_id: str,
    payload: ClinicServiceIn,
    request: Request,
    access: SiteServicesUpdate,
    db: DbSession,
) -> ClinicServiceOut:
    """Replace a service's editable fields; records an UPDATE audit event."""
    service_row = catalogue.get_service(db, access, service_id)
    if service_row is None:
        raise site_not_found()
    try:
        catalogue.update_service(db, access, service_row, payload)
    except catalogue.ServiceNameTakenError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    _audit_entity(
        db,
        request,
        access,
        AuditAction.UPDATE,
        AuditEntityType.CLINIC_SERVICE,
        service_row.id,
        f"updated {service_row.name!r}",
    )
    db.commit()
    db.refresh(service_row)
    return catalogue.service_out(service_row)


@router.delete(
    "/{site_id}/services/{service_id}",
    response_model=ClinicServiceOut,
    operation_id="sitesDeactivateService",
)
def deactivate_service(
    service_id: str, request: Request, access: SiteServicesUpdate, db: DbSession
) -> ClinicServiceOut:
    """Take a service out of new joins. **Its history stays**, which is why this is not a delete."""
    service_row = catalogue.get_service(db, access, service_id)
    if service_row is None:
        raise site_not_found()
    catalogue.deactivate_service(db, service_row)
    _audit_entity(
        db,
        request,
        access,
        AuditAction.UPDATE,
        AuditEntityType.CLINIC_SERVICE,
        service_row.id,
        f"deactivated {service_row.name!r}; past tickets still name it",
    )
    db.commit()
    db.refresh(service_row)
    return catalogue.service_out(service_row)


def _audit_entity(
    db: Session,
    request: Request,
    access: SiteAccess,
    action: AuditAction,
    entity_type: AuditEntityType,
    entity_id: str,
    context: str,
) -> None:
    """Record one mutation of something *inside* a clinic, rather than of the clinic itself."""
    bind_request_context(site_id=access.site_id)
    record_audit_event(
        db,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=access.user.email,
        actor_id=str(access.user.id),
        ip_address=resolve_client_ip(request),
        context=context,
    )


def _site_or_404(db: Session, access: SiteAccess) -> Site:
    """The clinic the request names, or the guard's 404.

    ``access.site_id`` has already been checked against the caller's assignments, so this is a
    lookup and not a second authorization decision; a soft-deleted clinic still answers 404.
    """
    site = service.get_site(db, access.site_id)
    if site is None:
        from src.core.site_scope import site_not_found

        raise site_not_found()
    return site


# --------------------------------------------------------------------------------------
# Payment and medical aid (Issue 37)
# --------------------------------------------------------------------------------------


def _payment_out(
    site: Site, found: payment_profile.PaymentProfile | None
) -> PaymentProfileOut:
    """The API shape of a clinic's payment profile, with the controlled scheme list."""
    return PaymentProfileOut(
        applicable=site.sector_enum is SiteSector.PRIVATE,
        accepts_cash=found.accepts_cash if found else None,
        accepts_card=found.accepts_card if found else None,
        schemes=[
            AcceptedSchemeOut(scheme=item.scheme, label=item.label)
            for item in (found.schemes if found else ())
        ],
        copay_notice=found.copay_notice if found else None,
        last_confirmed_at=found.last_confirmed_at if found else None,
        stale=found.stale if found else False,
        notice=payment_profile.CLINIC_REPORTED_NOTICE,
        scheme_options=[
            SchemeOptionOut(value=scheme, label=label)
            for scheme, label in payment_profile.SCHEME_LABELS.items()
        ],
    )


@router.get(
    "/{site_id}/payment-profile",
    response_model=PaymentProfileOut,
    operation_id="sitesGetPaymentProfile",
    summary="What this clinic says it accepts: cash, card and medical aids",
)
def get_payment_profile(access: SiteProfileRead, db: DbSession) -> PaymentProfileOut:
    """The clinic's self-reported payment profile. ``applicable`` is false for a public clinic."""
    site = _site_or_404(db, access)
    return _payment_out(site, payment_profile.get_profile(db, access))


@router.put(
    "/{site_id}/payment-profile",
    response_model=PaymentProfileOut,
    operation_id="sitesSetPaymentProfile",
    summary="Declare what this private clinic accepts, confirming it as of now",
    responses={status.HTTP_409_CONFLICT: {"description": "The clinic is public."}},
)
def set_payment_profile(
    payload: PaymentProfileIn,
    request: Request,
    access: SiteProfileUpdate,
    db: DbSession,
) -> PaymentProfileOut:
    """Replace the profile. Refused for a public clinic on the server, whatever the client shows."""
    site = _site_or_404(db, access)
    try:
        saved = payment_profile.save_profile(
            db,
            access,
            site,
            payment_profile.PaymentChange(
                accepts_cash=payload.accepts_cash,
                accepts_card=payload.accepts_card,
                schemes=frozenset(payload.schemes),
                other_name=payload.other_scheme_name,
                copay_notice=payload.copay_notice,
            ),
            confirmed_by=str(access.user.id),
        )
    except payment_profile.PublicClinicPaymentProfileError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    _audit(
        db,
        request,
        access.user.email,
        str(access.user.id),
        AuditAction.UPDATE,
        site.id,
        f"declared the payment profile of {site.slug}: cash {payload.accepts_cash}, card "
        f"{payload.accepts_card}, {len(payload.schemes)} scheme(s)",
    )
    db.commit()
    return _payment_out(site, saved)


@router.post(
    "/{site_id}/payment-profile/confirm",
    response_model=PaymentProfileOut,
    operation_id="sitesConfirmPaymentProfile",
    summary="Re-confirm this clinic's payment profile unchanged",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No profile has been declared yet."},
        status.HTTP_409_CONFLICT: {"description": "The clinic is public."},
    },
)
def confirm_payment_profile(
    request: Request, access: SiteProfileUpdate, db: DbSession
) -> PaymentProfileOut:
    """Stamp today's date on the profile, which clears its staleness."""
    site = _site_or_404(db, access)
    try:
        confirmed = payment_profile.confirm_profile(
            db, access, site, confirmed_by=str(access.user.id)
        )
    except payment_profile.PublicClinicPaymentProfileError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except payment_profile.PaymentProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    _audit(
        db,
        request,
        access.user.email,
        str(access.user.id),
        AuditAction.UPDATE,
        site.id,
        f"re-confirmed the payment profile of {site.slug}",
    )
    db.commit()
    return _payment_out(site, confirmed)


# --------------------------------------------------------------------------------------
# Discovery analytics (Issue 38): the opt-out and the view-to-join report
# --------------------------------------------------------------------------------------


#: Said beside the recall timeout, wherever a manager sets it (Issue 43).
RECALL_EXPLANATION = (
    "A called patient who has not arrived within this many minutes is called once more and sent a "
    "message; if they have still not arrived after the same time again, the ticket is marked missed "
    "and the room moves on. A queue can set its own time."
)


def _recall_out(site: Site, settings: Settings) -> RecallSettingsOut:
    return RecallSettingsOut(
        site_id=site.id,
        recall_timeout_minutes=site.recall_timeout_minutes,
        effective_minutes=timeout_minutes(
            None, site.recall_timeout_minutes, settings.queue_recall_timeout_minutes
        ),
        explanation=RECALL_EXPLANATION,
    )


@router.get(
    "/{site_id}/settings/recall",
    response_model=RecallSettingsOut,
    operation_id="sitesGetRecallSettings",
    summary="How long a called patient has to arrive before a recall, then a no-show",
)
def get_recall_settings(
    access: SiteSettingsRead, db: DbSession, settings: SettingsDep
) -> RecallSettingsOut:
    """The clinic's recall timeout and the one that applies."""
    return _recall_out(_site_or_404(db, access), settings)


@router.put(
    "/{site_id}/settings/recall",
    response_model=RecallSettingsOut,
    operation_id="sitesSetRecallSettings",
    summary="Set how long a called patient has to arrive at this clinic",
)
def set_recall_settings(
    payload: RecallSettingsIn,
    request: Request,
    access: SiteSettingsUpdate,
    db: DbSession,
    settings: SettingsDep,
) -> RecallSettingsOut:
    """Set the clinic's timeout, or clear it to use the platform default. Audited when it changes."""
    site = _site_or_404(db, access)
    if site.recall_timeout_minutes != payload.recall_timeout_minutes:
        site.recall_timeout_minutes = payload.recall_timeout_minutes
        _audit(
            db,
            request,
            access.user.email,
            str(access.user.id),
            AuditAction.UPDATE,
            site.id,
            f"recall timeout set to {payload.recall_timeout_minutes or 'the default'} "
            f"for {site.slug}",
        )
    db.commit()
    db.refresh(site)
    return _recall_out(site, settings)


#: Said beside each transfer placement, wherever a manager chooses one (Issue 45).
TRANSFER_EXPLANATIONS = {
    TransferPlacement.ARRIVAL_ORDER: (
        "A patient moved to another queue goes in among the patients waiting there by when their "
        "visit began, so time already spent at the clinic is not lost. This is the default."
    ),
    TransferPlacement.BACK_OF_LINE: (
        "A patient moved to another queue goes behind everyone already waiting there, as if they "
        "had just joined."
    ),
}


def _transfer_out(site: Site) -> TransferSettingsOut:
    placement = TransferPlacement(site.transfer_placement)
    return TransferSettingsOut(
        site_id=site.id,
        transfer_placement=placement,
        explanation=TRANSFER_EXPLANATIONS[placement],
    )


@router.get(
    "/{site_id}/settings/transfers",
    response_model=TransferSettingsOut,
    operation_id="sitesGetTransferSettings",
    summary="Where a patient moved to another queue lands in it",
)
def get_transfer_settings(
    access: SiteSettingsRead, db: DbSession
) -> TransferSettingsOut:
    """The clinic's transfer placement and what it means."""
    return _transfer_out(_site_or_404(db, access))


@router.put(
    "/{site_id}/settings/transfers",
    response_model=TransferSettingsOut,
    operation_id="sitesSetTransferSettings",
    summary="Choose where a patient moved to another queue lands in it",
)
def set_transfer_settings(
    payload: TransferSettingsIn,
    request: Request,
    access: SiteSettingsUpdate,
    db: DbSession,
) -> TransferSettingsOut:
    """Set arrival order or the back of the line. Audited when it changes."""
    site = _site_or_404(db, access)
    if site.transfer_placement != payload.transfer_placement.value:
        site.transfer_placement = payload.transfer_placement.value
        _audit(
            db,
            request,
            access.user.email,
            str(access.user.id),
            AuditAction.UPDATE,
            site.id,
            f"transfer placement set to {payload.transfer_placement.value} for {site.slug}",
        )
    db.commit()
    db.refresh(site)
    return _transfer_out(site)


def _analytics_out(site: Site) -> AnalyticsSettingsOut:
    return AnalyticsSettingsOut(
        site_id=site.id,
        analytics_enabled=site.analytics_enabled,
        explanation=discovery_analytics.ANALYTICS_EXPLANATION,
    )


@router.get(
    "/{site_id}/settings/analytics",
    response_model=AnalyticsSettingsOut,
    operation_id="sitesGetAnalyticsSettings",
    summary="Whether patients' views of and joins at this clinic are counted",
)
def get_analytics_settings(
    access: SiteSettingsRead, db: DbSession
) -> AnalyticsSettingsOut:
    """The clinic's analytics switch and what it means."""
    return _analytics_out(_site_or_404(db, access))


@router.put(
    "/{site_id}/settings/analytics",
    response_model=AnalyticsSettingsOut,
    operation_id="sitesSetAnalyticsSettings",
    summary="Turn counting this clinic's views and joins on or off",
)
def set_analytics_settings(
    payload: AnalyticsSettingsIn,
    request: Request,
    access: SiteSettingsUpdate,
    db: DbSession,
) -> AnalyticsSettingsOut:
    """Opt the clinic in or out. Events already recorded stay; nothing new is counted while off."""
    site = _site_or_404(db, access)
    if site.analytics_enabled != payload.analytics_enabled:
        site.analytics_enabled = payload.analytics_enabled
        _audit(
            db,
            request,
            access.user.email,
            str(access.user.id),
            AuditAction.UPDATE,
            site.id,
            f"discovery analytics {'on' if payload.analytics_enabled else 'off'} for {site.slug}",
        )
    db.commit()
    db.refresh(site)
    return _analytics_out(site)


def _virtual_waiting_out(site: Site) -> VirtualWaitingSettingsOut:
    return VirtualWaitingSettingsOut(
        site_id=site.id,
        virtual_waiting_enabled=site.virtual_waiting_enabled,
        default_travel_minutes=DEFAULT_TRAVEL_MINUTES,
        explanation=VIRTUAL_WAITING_EXPLANATION,
    )


@router.get(
    "/{site_id}/settings/virtual-waiting",
    response_model=VirtualWaitingSettingsOut,
    operation_id="sitesGetVirtualWaitingSettings",
    summary="Whether this clinic runs a virtual waiting room",
)
def get_virtual_waiting_settings(
    access: SiteSettingsRead, db: DbSession
) -> VirtualWaitingSettingsOut:
    """The clinic's virtual waiting room switch and what it means (Issue 86)."""
    return _virtual_waiting_out(_site_or_404(db, access))


@router.put(
    "/{site_id}/settings/virtual-waiting",
    response_model=VirtualWaitingSettingsOut,
    operation_id="sitesSetVirtualWaitingSettings",
    summary="Turn the virtual waiting room on or off",
)
def set_virtual_waiting_settings(
    payload: VirtualWaitingSettingsIn,
    request: Request,
    access: SiteSettingsUpdate,
    db: DbSession,
) -> VirtualWaitingSettingsOut:
    """Turn it on or off. Off stops the travel question and every alert at once; places are untouched."""
    site = _site_or_404(db, access)
    if site.virtual_waiting_enabled != payload.virtual_waiting_enabled:
        site.virtual_waiting_enabled = payload.virtual_waiting_enabled
        _audit(
            db,
            request,
            access.user.email,
            str(access.user.id),
            AuditAction.UPDATE,
            site.id,
            f"virtual waiting room {'on' if payload.virtual_waiting_enabled else 'off'} for {site.slug}",
        )
    db.commit()
    db.refresh(site)
    return _virtual_waiting_out(site)


@router.get(
    "/{site_id}/reports/discovery-conversion",
    response_model=DiscoveryConversionOut,
    operation_id="sitesDiscoveryConversion",
    summary="How many patients viewed this clinic, and how many joined a queue",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The range is backwards or longer than a year."
        }
    },
)
def discovery_conversion(
    access: SiteReportsRead,
    db: DbSession,
    start: Annotated[
        date | None, Query(description="First service day, inclusive.")
    ] = None,
    end: Annotated[
        date | None, Query(description="Last service day, inclusive.")
    ] = None,
) -> DiscoveryConversionOut:
    """Views and joins over a range of service days; the last 30 days by default."""
    site = _site_or_404(db, access)
    last = end or business_date(now_sast())
    first = start or last - timedelta(days=discovery_analytics.DEFAULT_REPORT_DAYS - 1)
    if first > last:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The start comes after the end."
        )
    if (last - first).days + 1 > discovery_analytics.MAX_REPORT_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"A report covers at most {discovery_analytics.MAX_REPORT_DAYS} days.",
        )
    found = discovery_analytics.conversion_for_site(db, access, start=first, end=last)
    return DiscoveryConversionOut(
        site_id=found.site_id,
        start=found.start,
        end=found.end,
        views=found.views,
        joins_started=found.joins_started,
        joins_completed=found.joins_completed,
        conversion_rate=found.conversion_rate,
        analytics_enabled=site.analytics_enabled,
    )
