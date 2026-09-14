"""A clinic manager's settings screens: profile, hours, queues, services, staff and display (Issue 54).

Screens over services that already exist, each saved through the JSON API that already enforces it:
the profile and its map pin (Issue 23), opening hours, holidays and closures (24), queues (25),
services (26), the waiting-room screen (27), staff and rooms (28) and invitations (22). **No rule is
added here.** Every write is a request to one of those routes, which checks the grant at this clinic,
validates the payload and writes the audit row in the same transaction as the change.

Three things this module does decide, all about what is **offered**:

* **Whose settings.** Every tab opens through the dashboard frame
  (:func:`~src.web.dashboard.routes.open_clinic_page`), so another clinic's id is the not-found page,
  the same as an id that does not exist.
* **Which tabs.** :data:`SETTINGS_TABS` names the grant each tab's writes need. A tab renders, and
  opens, only for a caller holding that grant at this clinic; the waiting-room screen keeps its
  read-only gate from Issue 27, so a receptionist can still see what the screen is set to.
* **What each tab starts from.** The current values are read on the server through the same
  services the API uses, so the form a manager edits is the state the server holds.

Every tab is its own URL (``docs/IDE/RULES/list-view-ui-pattern.mdc``); the bare settings URL and an
unknown section redirect to the first tab the caller may open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from src.commons.enums import (
    DisplayDeviceStatus,
    PermissionVerb,
    QueueKind,
    SaProvince,
    ServiceCategory,
    SiteSector,
    UserRole,
)
from src.commons.time import stored_sast
from src.core.config import get_settings
from src.core.nav_visibility import NavVisibility
from src.core.rbac_language import role_label
from src.database.session import get_db
from src.modules.display import devices as display_devices
from src.modules.display.router import device_out
from src.modules.patients.consent import display_preview
from src.modules.queues.schemas import QueueIn
from src.modules.queues.service import list_queues
from src.modules.sites import catalogue, hours_service, service
from src.modules.staff import assignments, invitations
from src.modules.staff.service import list_staff
from src.web.dashboard.routes import ClinicPage, open_clinic_page, render_clinic_page
from src.web.routes import _not_found_html

router = APIRouter(tags=["web"])

DbSession = Annotated[Session, Depends(get_db)]

#: The weekday names, Monday first, as :meth:`datetime.date.weekday` numbers them.
WEEKDAY_NAMES: Final = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
#: How many spans the hours form offers per day. The API allows up to four; two covers a lunch break,
#: and a day that needs more is saved through the API rather than squeezed into a reception screen.
SPANS_ON_FORM: Final = 2


class SettingsSection(StrEnum):
    """The settings tabs, each its own URL segment under ``/dashboard/sites/{id}/settings/``."""

    PROFILE = "profile"
    HOURS = "hours"
    QUEUES = "queues"
    SERVICES = "services"
    STAFF = "staff"
    DISPLAY = "display"
    DEVICES = "devices"
    PAYMENT = "payment"


@dataclass(frozen=True, slots=True)
class SettingsTab:
    """One tab: its label, and the grant (resource and verb) the caller needs at this clinic."""

    section: SettingsSection
    label: str
    resource: str
    verb: PermissionVerb

    def allowed(self, nav: NavVisibility) -> bool:
        """Whether ``nav`` (visibility at this clinic) holds this tab's grant."""
        if (
            self.section is SettingsSection.PAYMENT
            and not get_settings().payment_filter_enabled
        ):
            return False
        return nav.can(self.resource, self.verb)


#: The tabs in the order they appear. Each names the grant its API writes need, except the
#: waiting-room screen, which keeps Issue 27's read gate so the front desk can see its setting.
SETTINGS_TABS: Final[tuple[SettingsTab, ...]] = (
    SettingsTab(
        SettingsSection.PROFILE, "Profile", "sites.profile", PermissionVerb.UPDATE
    ),
    SettingsTab(
        SettingsSection.HOURS,
        "Hours and closures",
        "sites.profile",
        PermissionVerb.UPDATE,
    ),
    SettingsTab(SettingsSection.QUEUES, "Queues", "queues", PermissionVerb.DELETE),
    SettingsTab(
        SettingsSection.SERVICES, "Services", "sites.profile", PermissionVerb.UPDATE
    ),
    SettingsTab(SettingsSection.STAFF, "Staff", "sites.staff", PermissionVerb.UPDATE),
    SettingsTab(
        SettingsSection.DISPLAY,
        "Waiting-room screen",
        "sites.display",
        PermissionVerb.READ,
    ),
    SettingsTab(
        SettingsSection.DEVICES,
        "Display boards",
        "sites.display",
        PermissionVerb.READ,
    ),
    SettingsTab(
        SettingsSection.PAYMENT,
        "Payment and medical aid",
        "sites.profile",
        PermissionVerb.READ,
    ),
)
_TABS_BY_SECTION: Final = {tab.section: tab for tab in SETTINGS_TABS}


@dataclass(frozen=True, slots=True)
class TabLink:
    """A settings tab the caller may open, ready to render."""

    label: str
    href: str
    active: bool


@dataclass(frozen=True, slots=True)
class ClosureView:
    """A live closure with its times in Johannesburg, as the hours tab shows it."""

    id: str
    reason: str
    starts_at: datetime
    ends_at: datetime | None


class ActiveFilter(StrEnum):
    """The status filter on a settings list: switched on, or switched off."""

    ACTIVE = "active"
    INACTIVE = "inactive"


#: The status filter's options and their words.
ACTIVE_FILTER_CHOICES: Final = (
    (ActiveFilter.ACTIVE.value, "Active"),
    (ActiveFilter.INACTIVE.value, "Deactivated"),
)


@dataclass(frozen=True, slots=True)
class ListFilters:
    """A settings list's GET filters. Empty means everything; a filtered list cannot be reordered."""

    status: ActiveFilter | None = None
    kind: str | None = None

    @property
    def active(self) -> bool:
        """Whether any filter narrows the list."""
        return self.status is not None or self.kind is not None

    def admits(self, *, is_active: bool, kind: str | None = None) -> bool:
        """Whether a row with this state and kind passes the filters."""
        if self.status is ActiveFilter.ACTIVE and not is_active:
            return False
        if self.status is ActiveFilter.INACTIVE and is_active:
            return False
        return self.kind is None or kind == self.kind


def _settings_href(site_id: str, section: SettingsSection) -> str:
    """A settings tab's URL at ``site_id``."""
    return f"/dashboard/sites/{site_id}/settings/{section.value}"


def open_tabs(nav: NavVisibility) -> list[SettingsTab]:
    """The tabs ``nav`` may open, in order."""
    return [tab for tab in SETTINGS_TABS if tab.allowed(nav)]


def _open_settings(
    request: Request, db: Session, site_id: str, section: SettingsSection, title: str
) -> ClinicPage | Response:
    """Open one settings tab, adding the tab bar to its context."""
    tab = _TABS_BY_SECTION[section]
    opened = open_clinic_page(
        request,
        db,
        site_id,
        active_key="clinic_settings",
        page_title=title,
        allowed=tab.allowed,
    )
    if isinstance(opened, ClinicPage):
        opened.context["settings_tabs"] = [
            TabLink(
                label=other.label,
                href=_settings_href(site_id, other.section),
                active=other.section is section,
            )
            for other in open_tabs(opened.shell.nav)
        ]
    return opened


@router.get("/dashboard/sites/{site_id}/settings", response_class=HTMLResponse)
async def clinic_settings(site_id: str, request: Request, db: DbSession) -> Response:
    """The settings: redirects to the first tab the caller may open here.

    Gated like its link in the menu (the ``clinic_settings`` destination), so a URL the menu does not
    offer is refused rather than quietly redirected. A single tab with a wider gate (the waiting-room
    screen, which a receptionist may read) still opens at its own URL.
    """
    opened = open_clinic_page(
        request,
        db,
        site_id,
        active_key="clinic_settings",
        page_title="Clinic settings",
        allowed=lambda nav: nav.visible("clinic_settings") and bool(open_tabs(nav)),
    )
    if not isinstance(opened, ClinicPage):
        return opened
    first = open_tabs(opened.shell.nav)[0]
    return RedirectResponse(_settings_href(site_id, first.section), status_code=302)


@router.get("/dashboard/sites/{site_id}/settings/profile", response_class=HTMLResponse)
async def settings_profile(site_id: str, request: Request, db: DbSession) -> Response:
    """The clinic's profile and its location, with a map pin the manager can move (Issues 23, 54)."""
    opened = _open_settings(
        request, db, site_id, SettingsSection.PROFILE, "Clinic profile"
    )
    if not isinstance(opened, ClinicPage):
        return opened
    site = service.get_site(db, site_id, site_ids=frozenset({site_id}))
    if site is None:
        return _not_found_html(request, db)
    opened.context.update(
        profile=service.site_out(site),
        sectors=list(SiteSector),
        provinces=list(SaProvince),
    )
    return render_clinic_page(request, opened, "dashboard/settings_profile.html")


@router.get("/dashboard/sites/{site_id}/settings/hours", response_class=HTMLResponse)
async def settings_hours(site_id: str, request: Request, db: DbSession) -> Response:
    """The ordinary week, the public holidays and one-tap temporary closure (Issue 24)."""
    opened = _open_settings(
        request, db, site_id, SettingsSection.HOURS, "Hours and closures"
    )
    if not isinstance(opened, ClinicPage):
        return opened
    opened.context.update(
        week=hours_service.weekly_hours(db, opened.access),
        holidays=hours_service.holiday_calendar(db, opened.access),
        closures=[
            ClosureView(
                id=closure.id,
                reason=closure.reason,
                starts_at=stored_sast(closure.starts_at),
                ends_at=stored_sast(closure.ends_at) if closure.ends_at else None,
            )
            for closure in hours_service.list_closures(db, opened.access).items
        ],
        weekday_names=WEEKDAY_NAMES,
        spans_on_form=SPANS_ON_FORM,
    )
    return render_clinic_page(request, opened, "dashboard/settings_hours.html")


@router.get("/dashboard/sites/{site_id}/settings/queues", response_class=HTMLResponse)
async def settings_queues(
    site_id: str,
    request: Request,
    db: DbSession,
    status: Annotated[ActiveFilter | None, Query()] = None,
    kind: Annotated[QueueKind | None, Query()] = None,
) -> Response:
    """The clinic's queues: add, rename, reorder, deactivate and bring back (Issue 25)."""
    opened = _open_settings(request, db, site_id, SettingsSection.QUEUES, "Queues")
    if not isinstance(opened, ClinicPage):
        return opened
    filters = ListFilters(status=status, kind=kind.value if kind else None)
    opened.context.update(
        queues=[
            queue
            for queue in list_queues(db, opened.access).items
            if filters.admits(is_active=queue.is_active, kind=queue.kind.value)
        ],
        queue_kinds=list(QueueKind),
        # The kind a new queue starts as: the API's own default, so the form cannot suggest another.
        default_queue_kind=QueueIn.model_fields["kind"].default.value,
        filters=filters,
        status_choices=ACTIVE_FILTER_CHOICES,
    )
    return render_clinic_page(request, opened, "dashboard/settings_queues.html")


@router.get("/dashboard/sites/{site_id}/settings/services", response_class=HTMLResponse)
async def settings_services(
    site_id: str,
    request: Request,
    db: DbSession,
    status: Annotated[ActiveFilter | None, Query()] = None,
    category: Annotated[ServiceCategory | None, Query()] = None,
) -> Response:
    """What the clinic offers, and which queue handles each service (Issue 26)."""
    opened = _open_settings(request, db, site_id, SettingsSection.SERVICES, "Services")
    if not isinstance(opened, ClinicPage):
        return opened
    queues = list_queues(db, opened.access).items
    filters = ListFilters(status=status, kind=category.value if category else None)
    queue_names = {queue.id: queue.name for queue in queues}
    services = [
        item
        for item in catalogue.list_services(db, opened.access).items
        if filters.admits(is_active=item.is_active, kind=item.category.value)
    ]
    opened.context.update(
        services=services,
        service_queue_names={
            item.id: [queue_names[qid] for qid in item.queue_ids if qid in queue_names]
            for item in services
        },
        filters=filters,
        status_choices=ACTIVE_FILTER_CHOICES,
        queues=queues,
        categories=list(ServiceCategory),
    )
    return render_clinic_page(request, opened, "dashboard/settings_services.html")


@dataclass(frozen=True, slots=True)
class StaffRow:
    """One staff member as the list shows them, and the record their panel is filled from."""

    name: str
    role_labels: list[str]
    room_names: list[str]
    record: dict[str, object]


@router.get("/dashboard/sites/{site_id}/settings/staff", response_class=HTMLResponse)
async def settings_staff(
    site_id: str,
    request: Request,
    db: DbSession,
    status: Annotated[ActiveFilter | None, Query()] = None,
    role: Annotated[UserRole | None, Query()] = None,
) -> Response:
    """Who works here: invitations, roles at this clinic, rooms and deactivation (Issues 22, 28)."""
    opened = _open_settings(request, db, site_id, SettingsSection.STAFF, "Staff")
    if not isinstance(opened, ClinicPage):
        return opened
    access = opened.access
    filters = ListFilters(status=status, kind=role.value if role else None)
    queues = list_queues(db, access, include_inactive=False).items
    queue_names = {queue.id: queue.name for queue in queues}
    rows = []
    for member in list_staff(db, access).items:
        if not member.is_active and filters.status is ActiveFilter.ACTIVE:
            continue
        if member.is_active and filters.status is ActiveFilter.INACTIVE:
            continue
        if filters.kind is not None and filters.kind not in member.roles:
            continue
        rooms = [
            row.queue_id
            for row in assignments.room_assignments(db, access, member.id)
            if row.is_active
        ]
        rows.append(
            StaffRow(
                name=" ".join(
                    part for part in (member.first_name, member.last_name) if part
                )
                or member.email,
                role_labels=[role_label(held) for held in member.roles],
                room_names=[queue_names[qid] for qid in rooms if qid in queue_names],
                record={**member.model_dump(mode="json"), "queue_ids": rooms},
            )
        )
    opened.context.update(
        staff_rows=rows,
        queues=queues,
        invitations=[
            (row, invitations.invitation_state(row))
            for row in invitations.list_invitations(db, access)
        ],
        assignable_roles=[
            (held.value, role_label(held))
            for held in sorted(assignments.ASSIGNABLE_AT_A_SITE, key=lambda r: r.value)
        ],
        role_label=role_label,
        filters=filters,
        status_choices=ACTIVE_FILTER_CHOICES,
    )
    return render_clinic_page(request, opened, "dashboard/settings_staff.html")


@router.get("/dashboard/sites/{site_id}/settings/display", response_class=HTMLResponse)
async def site_display_settings_page(
    site_id: str, request: Request, db: DbSession
) -> Response:
    """The waiting-room screen's settings for one clinic (Issue 27, non-negotiable 4).

    Two gates, in the order that keeps a 404 honest: a role **at this clinic** (another clinic's id
    renders the not-found page, never a 403 that would confirm it), then the ``sites.display`` grant
    there. The JSON API behind it re-checks the grant and adds ``update`` for saving.

    The modes, their descriptions, the bounds and the warnings come from
    ``/api/v1/sites/{id}/settings/display-options``; the live preview is rendered here for every
    mode through the board's own rule (:func:`display_preview`).
    """
    opened = _open_settings(
        request, db, site_id, SettingsSection.DISPLAY, "Waiting-room screen"
    )
    if not isinstance(opened, ClinicPage):
        return opened
    site = opened.shell.site
    opened.context.update(
        preview={
            "with_reason": display_preview(show_comment=True),
            "without_reason": display_preview(show_comment=False),
        },
        current_mode=site.display_mode_enum.value,
    )
    return render_clinic_page(request, opened, "dashboard/settings_display.html")


@dataclass(frozen=True, slots=True)
class DeviceRow:
    """One waiting-room screen as the clinic's list shows it (Issue 61)."""

    label: str
    status: DisplayDeviceStatus
    queue_names: tuple[str, ...]
    #: When it was last heard from, in Johannesburg; ``None`` if never.
    last_seen_at: datetime | None
    #: The API's view of the screen, for the slideover's forms.
    record: dict[str, object]


#: What each screen status is called, and the badge it wears.
DEVICE_STATUS_WORDS: Final = {
    DisplayDeviceStatus.ONLINE: ("Showing the board", "badge-ok"),
    DisplayDeviceStatus.SILENT: ("Not heard from", "badge-warn"),
    DisplayDeviceStatus.REVOKED: ("Removed", "badge-muted"),
    DisplayDeviceStatus.PAIRING: ("Waiting to pair", "badge-muted"),
}


@router.get("/dashboard/sites/{site_id}/settings/devices", response_class=HTMLResponse)
async def settings_devices(
    site_id: str,
    request: Request,
    db: DbSession,
    status: Annotated[DisplayDeviceStatus | None, Query()] = None,
) -> Response:
    """The clinic's waiting-room screens: pair one with the code on its screen, rename it, remove it (Issue 61).

    A server-rendered list (``docs/IDE/RULES/list-view-ui-pattern.mdc``, second wiring style): the status
    filter is a GET form, the columns sort in place, and a row opens its screen in the slideover. Every
    change is a request to ``/api/v1/sites/{site_id}/display-devices``, which checks the grant and audits.
    """
    opened = _open_settings(
        request, db, site_id, SettingsSection.DEVICES, "Display boards"
    )
    if not isinstance(opened, ClinicPage):
        return opened
    access = opened.access
    queues = list_queues(db, access, include_inactive=False).items
    names = {queue.id: queue.name for queue in queues}
    rows = []
    for device in display_devices.devices_at(db, access):
        out = device_out(device)
        if status is not None and out.status is not status:
            continue
        rows.append(
            DeviceRow(
                label=device.label or "Unnamed screen",
                status=out.status,
                queue_names=tuple(
                    names[q] for q in (device.queue_ids or []) if q in names
                ),
                last_seen_at=stored_sast(device.last_seen_at)
                if device.last_seen_at
                else None,
                record={
                    **out.model_dump(mode="json"),
                    "label": device.label or "",
                    "queue_ids": device.queue_ids or [],
                    "is_active": out.status is not DisplayDeviceStatus.REVOKED,
                },
            )
        )
    opened.context.update(
        device_rows=rows,
        queues=queues,
        status_filter=status.value if status else "",
        status_words={key.value: words for key, words in DEVICE_STATUS_WORDS.items()},
        silent_minutes=get_settings().display_device_silent_minutes,
    )
    return render_clinic_page(request, opened, "dashboard/settings_devices.html")


@router.get("/dashboard/sites/{site_id}/settings/payment", response_class=HTMLResponse)
async def site_payment_profile_page(
    site_id: str, request: Request, db: DbSession
) -> Response:
    """A private clinic's payment methods and medical aids, as it reports them (Issue 37).

    Behind ``PAYMENT_FILTER_ENABLED``: while the feature is off the page is not served at all. The
    page renders no rule: whether the clinic may hold a profile, the scheme list and the notice all
    come from ``/api/v1/sites/{site_id}/payment-profile``.
    """
    if not get_settings().payment_filter_enabled:
        return _not_found_html(request, db)
    opened = _open_settings(
        request, db, site_id, SettingsSection.PAYMENT, "Payment and medical aid"
    )
    if not isinstance(opened, ClinicPage):
        return opened
    return render_clinic_page(request, opened, "dashboard/settings_payment.html")


@router.get(
    "/dashboard/sites/{site_id}/settings/{section}", response_class=HTMLResponse
)
async def settings_unknown_section(
    site_id: str, section: str, request: Request, db: DbSession
) -> Response:
    """An unrecognised tab redirects to the settings (and so to the first tab), rather than 404ing."""
    del (
        section
    )  # any unknown segment lands on the default, per the list-view convention
    return RedirectResponse(f"/dashboard/sites/{site_id}/settings", status_code=302)
