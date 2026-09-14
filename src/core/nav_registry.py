"""Declarative navigation & action registry — the single source of truth for RBAC-driven UX.

Navigation visibility used to be computed field by field in :mod:`src.core.nav_visibility`
(one ``show_*`` flag per destination) and the CRUD/action buttons were shown by hand-written
``{% if bool_can_* %}`` checks. Adding a console, a sub-console or a custom role meant editing
Python **and** templates, and the grants under ``/admin/rbac/permissions`` were not the single
source of truth for what rendered (Issue #104).

This module makes that data-driven. Each :class:`NavDestination` maps a rail/console destination
to the ``(resource, verb)`` it requires; :class:`NavGroup` carries the grouped-rail structure
(Leasing / Maintenance / Communications). :mod:`src.core.nav_visibility` evaluates this registry
against the caller's *effective* grant matrix, so changing a grant in the RBAC admin changes the
rendered nav with **no** code edit, and a custom role gets exactly the destinations its grants
imply. The server still re-checks every route (the registry only drives *rendering* — the M17
defence-in-depth guarantee is unchanged).
"""

from dataclasses import dataclass

from src.commons.enums import GrantScope, PermissionVerb

#: The path segment a clinic destination's ``href`` carries in place of the site id (Issue 48).
SITE_ID_PLACEHOLDER = "{site_id}"


@dataclass(frozen=True, slots=True)
class NavDestination:
    """One RBAC-gated navigation destination (a rail icon or a grouped console tab).

    ``key`` is the stable identifier used both as the template lookup key and as the
    ``active_nav`` value that highlights the destination. ``resource``/``verb`` are the grant a
    caller must hold for the destination to render — evaluated against the caller's effective
    (parent→child inherited) verbs — and, since Issue #164 (M28), they are the **whole** gate.
    ``group`` names the :class:`NavGroup` the destination belongs to (``None`` for a standalone
    rail icon).

    There used to be a ``management: bool = True`` field here: a second gate ANDing the grant with
    "is this caller's role one of the three portal names" (Issue #103's answer to the fact that a
    bare READ could not separate "my lease" from "every lease"). That distinction is now carried by
    the grant itself — ``role_permission.scope`` (Issue #156) — and the data behind every one of
    these consoles narrows per caller accordingly (Issues #157–#159), so the nav layer's cruder,
    name-based approximation of the same thing was not just redundant but wrong for any role that
    is not one of the three grandfathered names: an operator could never express a new portal-style
    role, because that wall answered ``True`` for any name outside its list, regardless of any
    grant. Issue #172 deleted the function and the name set with it.
    """

    key: str
    label: str
    href: str
    # A plain manifest-declared resource key (Issue #154 — every resource is one, permanently).
    resource: str
    verb: PermissionVerb
    # The **scope tier** the caller's grant must reach for this surface to open (Issue #165, M28) —
    # the second axis of the gate, alongside the verb. ``BUSINESS`` (the default, and what every
    # back-office console below declares) means "this page is the whole-business view": an ``own``
    # grant on the same resource opens the caller's own portal, never this console. ``OWN`` marks a
    # surface that is *itself* a first-person view, so the narrowest tier suffices. See
    # :meth:`src.core.nav_visibility.NavVisibility.visible`.
    scope: GrantScope = GrantScope.BUSINESS
    group: str | None = None
    # The key that opens this destination from the keyboard, pressed after ``g`` (Issue 48). Read by
    # ``dashboard-shell.js`` off the rendered link, so the shortcut and its entry in the help dialog
    # come from this one declaration.
    shortcut: str | None = None

    @property
    def site_scoped(self) -> bool:
        """Whether this destination lives inside one clinic (its ``href`` names ``{site_id}``).

        A clinic's screens are per site (Issue 48): the same staff member may be a receptionist at
        one clinic and hold no role at another, so the destination's link, and the grant that
        decides whether it renders, are both resolved **at a site**.
        """
        return SITE_ID_PLACEHOLDER in self.href

    def href_at(self, site_id: str) -> str:
        """The destination's link at ``site_id`` (the ``href`` itself for a site-less one)."""
        return self.href.replace(SITE_ID_PLACEHOLDER, site_id)


@dataclass(frozen=True, slots=True)
class NavGroup:
    """A grouped-rail destination — related consoles under one icon with tabbed sub-nav.

    The rail shows one icon per group (Issue #92); ``member_keys`` lists its destinations in the
    order the group icon opens them (the first the caller can reach), and ``active_keys`` are the
    ``active_nav`` values that light the group icon. Kept as data so the rail, the tab bar and
    Issue #105's module groups all render from one structure.
    """

    key: str
    label: str
    member_keys: tuple[str, ...]
    active_keys: tuple[str, ...]


# The RBAC-gated destinations. Each declares the effective grant it needs: a ``(resource, verb)``
# pair **and** the ``scope`` tier that grant must reach (Issue #165, M28). Both halves come from
# the grant an admin wrote — never from the caller's role name.
#
# Everything below is a kernel surface: the Communications consoles (when their flags are on), the
# access-control console, the log viewer. Your own consoles are added here, and the resource each
# names must be declared by a manifest registered in
# :mod:`src.core.rbac_manifest_registry` — ``make seed-rbac`` writes the nav-gate default row from
# this list, and a destination naming an undeclared resource can never be granted.
NAV_DESTINATIONS: tuple[NavDestination, ...] = (
    NavDestination(
        key="messages",
        label="Messages",
        href="/admin/messages",
        resource="communications.messages",
        verb=PermissionVerb.READ,
        # The one Communications console that is *not* whole-business-only (Issue #157, re-stated
        # as a tier by Issue #165): the messaging API narrows an ``own``-scoped caller to their own
        # participant threads, so the surface is a first-person view for them and the tier must let
        # them in. Its siblings keep the ``BUSINESS`` default.
        scope=GrantScope.OWN,
        group="communications",
    ),
    NavDestination(
        key="notifications",
        label="Notifications",
        href="/admin/notifications",
        resource="logs",
        verb=PermissionVerb.READ,
        group="communications",
    ),
    NavDestination(
        key="announcements",
        label="Announcements",
        href="/admin/announcements",
        resource="communications.announcements",
        verb=PermissionVerb.READ,
        group="communications",
    ),
    NavDestination(
        key="alerts",
        label="Alerts",
        href="/admin/alerts",
        resource="communications.alerts",
        verb=PermissionVerb.READ,
        group="communications",
    ),
    # The clinic dashboard (Issue 48): one clinic's screens, each at ``/dashboard/sites/{site_id}/…``
    # and each gated on the grant its own API enforces, resolved with the roles the caller holds
    # **at that clinic**. Which role sees which follows from the manifests' grants, never from a
    # role name: the front desk is ``queues.tickets`` at ``assigned`` (a nurse's grant there is
    # ``own``, so their board is their room), the room is ``visits.notes``, which only a clinician
    # holds, and the settings are ``sites.settings:update``, which only a manager holds.
    NavDestination(
        key="board",
        label="Front desk",
        href="/dashboard/sites/{site_id}/board",
        resource="queues.tickets",
        verb=PermissionVerb.READ,
        scope=GrantScope.ASSIGNED,
        shortcut="b",
    ),
    NavDestination(
        key="room",
        label="My room",
        href="/dashboard/sites/{site_id}/room",
        resource="visits.notes",
        verb=PermissionVerb.UPDATE,
        scope=GrantScope.OWN,
        shortcut="r",
    ),
    NavDestination(
        key="clinic_settings",
        label="Clinic settings",
        href="/dashboard/sites/{site_id}/settings",
        resource="sites.settings",
        verb=PermissionVerb.UPDATE,
        scope=GrantScope.ASSIGNED,
        shortcut="s",
    ),
    NavDestination(
        key="widgets",
        label="Widgets",
        href="/admin/widgets",
        resource="widgets",
        verb=PermissionVerb.READ,
    ),
    NavDestination(
        key="rbac",
        label="Access control",
        href="/admin/rbac/roles",
        resource="rbac",
        verb=PermissionVerb.READ,
    ),
    NavDestination(
        key="logs",
        label="Application logs",
        href="/admin/logs",
        resource="logs",
        verb=PermissionVerb.READ,
    ),
)


# The grouped-rail structure (Issue #92): related consoles under one icon with tabbed sub-nav.
#
# Membership is **derived** from the destinations above rather than listed again — a destination
# names its own group, and duplicating that list here is how a rail icon ends up opening a tab that
# no longer exists. A group whose destinations are all gated off simply never appears.
_GROUP_LABELS: tuple[tuple[str, str], ...] = (("communications", "Communications"),)


def _members_of(group_key: str) -> tuple[str, ...]:
    """Return the destination keys in ``group_key``, in registry order.

    Registry order is the "open the first the caller can reach" precedence the rail applies to the
    group icon's href, so the order destinations are declared in above is the order they open in.
    """
    return tuple(dest.key for dest in NAV_DESTINATIONS if dest.group == group_key)


NAV_GROUPS: tuple[NavGroup, ...] = tuple(
    NavGroup(
        key=key,
        label=label,
        member_keys=_members_of(key),
        active_keys=_members_of(key),
    )
    for key, label in _GROUP_LABELS
    if _members_of(key)
)


# Fast lookups built once from the registry above.
_DESTINATIONS_BY_KEY: dict[str, NavDestination] = {d.key: d for d in NAV_DESTINATIONS}
_GROUPS_BY_KEY: dict[str, NavGroup] = {g.key: g for g in NAV_GROUPS}


def destination(key: str) -> NavDestination:
    """Return the :class:`NavDestination` for ``key`` (raises ``KeyError`` if unknown)."""
    return _DESTINATIONS_BY_KEY[key]


def group(key: str) -> NavGroup:
    """Return the :class:`NavGroup` for ``key`` (raises ``KeyError`` if unknown)."""
    return _GROUPS_BY_KEY[key]


def all_destination_keys() -> tuple[str, ...]:
    """Return every destination key in registry order."""
    return tuple(_DESTINATIONS_BY_KEY)


def site_destinations() -> tuple[NavDestination, ...]:
    """Every clinic destination, in registry order: the order the rail and a site's home use."""
    return tuple(dest for dest in NAV_DESTINATIONS if dest.site_scoped)


def _href_matches(href_segments: list[str], path_segments: list[str]) -> bool:
    """Whether ``path_segments`` starts with ``href_segments``, a ``{site_id}`` matching any one."""
    if len(path_segments) < len(href_segments):
        return False
    return all(
        want in (got, SITE_ID_PLACEHOLDER)
        for want, got in zip(href_segments, path_segments, strict=False)
    )


def destination_for_path(path: str) -> NavDestination | None:
    """Return the destination whose ``href`` best matches ``path``, or ``None`` (Issue #174, M29).

    Backs the simulator's ``{"path": "/admin/leases"}`` target form — the shape an operator
    actually has in hand when they ask "why can this role open this page?", since a URL is what a
    support ticket contains and a surface key is not.

    Exact href first, then the **longest** href that is a path prefix, so ``/admin/leases/{id}``
    resolves to the ``leases`` console rather than to a shorter, coarser destination that happens to
    share a stem. Prefix matching is on whole segments (``/admin/leases`` never matches
    ``/admin/leases-archive``). Returns ``None`` for a path no destination declares, which the
    endpoint reports as an unresolvable target rather than guessing.

    A clinic destination's ``{site_id}`` segment matches any one segment, so
    ``/dashboard/sites/<id>/board`` resolves to ``board`` whichever clinic the ticket names.
    """
    normalized = "/" + path.strip().strip("/") if path.strip().strip("/") else "/"
    path_segments = normalized.strip("/").split("/")
    best: NavDestination | None = None
    for dest in NAV_DESTINATIONS:
        href_segments = dest.href.strip("/").split("/")
        if not _href_matches(href_segments, path_segments):
            continue
        if len(href_segments) == len(path_segments):
            return dest
        if best is None or len(dest.href) > len(best.href):
            best = dest
    return best
