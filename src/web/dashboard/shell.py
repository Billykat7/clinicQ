"""The clinic dashboard's frame: which clinic, who is signed in, and what they may open there (Issue 48).

Every staff screen of a clinic (the front desk, a room, the settings) lives inside one clinic, and
the same person can hold different roles at different clinics. This module resolves that **once per
request**, so no page re-derives it and no template decides it:

* **Which clinics** a staff member works at is read from their role assignments
  (:func:`~src.core.site_scope.permitted_site_ids`), never from a column or a token, so a removed
  assignment disappears from the switcher on the next request.
* **Which clinic is current** is the one in the URL (``/dashboard/sites/{site_id}/…``). A cookie
  remembers the last one so ``/dashboard`` reopens it, but the cookie is only a preference: its value
  is checked against the assignments every time, and a clinic the caller no longer works at is
  ignored rather than trusted.
* **What they may open there** is the nav registry evaluated with the roles held at that clinic
  (:func:`~src.core.nav_visibility.nav_visibility_at_site`), the role set the clinic's API resolves a
  request with. A link is therefore shown exactly when the page and the API behind it would admit
  the caller.

Switching clinics is a link, not a form: ``/dashboard/sites/{other}?section=<key>`` reopens the same
screen at the other clinic when the caller may open it there, and the clinic's first screen when
not. Nothing about the session changes, so there is nothing to sign in to again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlencode

from fastapi import Request, Response
from sqlalchemy.orm import Session

from src.commons.enums import AssignmentScopeType
from src.core.config import get_settings
from src.core.nav_registry import NavDestination, site_destinations
from src.core.nav_visibility import (
    NavVisibility,
    nav_visibility_at_site,
    nav_visibility_for_roles,
)
from src.core.rbac import active_roles_for_user
from src.core.rbac_language import role_label
from src.core.site_scope import permitted_site_ids
from src.database.models import Site, User
from src.modules.sites.service import live_sites

#: The cookie that remembers the clinic a staff member last worked in. A preference, never a grant.
SITE_COOKIE_NAME: Final = "clinicq_site"
#: How long the preference lasts: a term's worth of shifts at the same front desk.
SITE_COOKIE_MAX_AGE_SECONDS: Final = 180 * 24 * 60 * 60
#: The clinic home's query parameter naming the screen to reopen after a switch.
SECTION_PARAM: Final = "section"
#: The prefix every clinic screen's URL starts with.
SITES_PATH: Final = "/dashboard/sites"


@dataclass(frozen=True, slots=True)
class ClinicLink:
    """One clinic destination the caller may open at the current clinic, ready to render."""

    key: str
    label: str
    href: str
    shortcut: str | None
    active: bool


@dataclass(frozen=True, slots=True)
class SiteChoice:
    """Another clinic the caller works at, and the link that switches to it."""

    id: str
    name: str
    href: str


@dataclass(frozen=True, slots=True)
class ClinicShell:
    """Everything the dashboard frame shows about the current clinic, resolved once."""

    site: Site
    #: Visibility at this clinic: the registry evaluated with the roles held here.
    nav: NavVisibility
    links: tuple[ClinicLink, ...]
    other_sites: tuple[SiteChoice, ...]
    #: The caller's roles at this clinic as people say them ("nurse or doctor").
    role_labels: tuple[str, ...]
    display_name: str

    @property
    def home_href(self) -> str:
        """The clinic's home: the first screen the caller may open here."""
        return clinic_home_href(self.site.id)


def clinic_home_href(site_id: str, section: str | None = None) -> str:
    """``/dashboard/sites/{site_id}``, optionally asking to reopen ``section`` there."""
    href = f"{SITES_PATH}/{site_id}"
    return f"{href}?{urlencode({SECTION_PARAM: section})}" if section else href


def staff_sites(db: Session, user: User) -> list[Site]:
    """The live clinics ``user`` holds a role at, by name. Empty for someone who works at none."""
    site_ids = permitted_site_ids(db, user)
    if not site_ids:
        return []
    return list(
        db.execute(live_sites(site_ids).order_by(Site.name, Site.id)).scalars().all()
    )


def remembered_site_id(request: Request, sites: Sequence[Site]) -> str | None:
    """The clinic to reopen: the remembered one if the caller still works there, else the first."""
    if not sites:
        return None
    remembered = request.cookies.get(SITE_COOKIE_NAME)
    ids = [site.id for site in sites]
    return remembered if remembered in ids else ids[0]


def remember_site(response: Response, site_id: str) -> None:
    """Remember ``site_id`` as the clinic ``/dashboard`` reopens. httpOnly: no script needs it."""
    response.set_cookie(
        key=SITE_COOKIE_NAME,
        value=site_id,
        max_age=SITE_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=not get_settings().is_development,
        samesite="lax",
        path="/",
    )


def _display_name(user: User) -> str:
    """The name the header shows: first and last name when known, else the email address."""
    name = " ".join(part for part in (user.first_name, user.last_name) if part)
    return name or str(user.email)


def _roles_at(db: Session, user: User, site_id: str) -> set[str] | None:
    """The roles ``user`` holds at ``site_id`` (the set enforcement uses), or ``None`` with auth off."""
    if not get_settings().auth_enabled:
        return None
    return active_roles_for_user(
        db, user, scope_type=AssignmentScopeType.SITE.value, scope_id=site_id
    )


def first_open_destination(nav: NavVisibility) -> NavDestination | None:
    """The first clinic destination, in registry order, that ``nav`` lets the caller open."""
    return next((dest for dest in site_destinations() if nav.visible(dest.key)), None)


def build_shell(
    db: Session,
    user: User,
    site: Site,
    sites: Sequence[Site],
    *,
    active_key: str = "",
) -> ClinicShell:
    """Resolve the frame for ``user`` at ``site``; ``sites`` are every clinic they work at.

    The caller has already established that ``site`` is one of ``sites``: this function renders an
    answer, it does not make the membership decision.
    """
    roles = _roles_at(db, user, site.id)
    nav = (
        nav_visibility_for_roles(db, sorted(roles))
        if roles is not None
        else nav_visibility_at_site(db, user, site.id)
    )
    links = tuple(
        ClinicLink(
            key=dest.key,
            label=dest.label,
            href=dest.href_at(site.id),
            shortcut=dest.shortcut,
            active=dest.key == active_key,
        )
        for dest in site_destinations()
        if nav.visible(dest.key)
    )
    other_sites = tuple(
        SiteChoice(
            id=other.id,
            name=other.name,
            href=clinic_home_href(other.id, active_key or None),
        )
        for other in sites
        if other.id != site.id
    )
    return ClinicShell(
        site=site,
        nav=nav,
        links=links,
        other_sites=other_sites,
        role_labels=tuple(sorted(role_label(role) for role in roles or ())),
        display_name=_display_name(user),
    )
