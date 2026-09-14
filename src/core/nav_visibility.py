"""Server-side navigation & action visibility from the refresh-session user and RBAC (Issue 9).

Used by the Jinja base layout for first paint; API authorization remains enforced separately
(the UI is UX only — every route re-checks its grant, the M17 defence-in-depth guarantee).

Visibility is **data-driven** (Issue #104): :class:`NavVisibility` holds the caller's effective,
parent→child-inherited grant matrix and evaluates the declarative registry in
:mod:`src.core.nav_registry` against it. Change a grant in ``/admin/rbac/permissions`` and the
rendered nav/actions change with no code edit; a custom role gets exactly the destinations and
buttons its grants imply. Ported and generalised from the ``maps`` project.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import AssignmentScopeType, GrantScope, PermissionVerb
from src.core.config import get_settings
from src.core.nav_registry import destination, group
from src.core.rbac import (
    Trace,
    TraceOutcome,
    TraceStage,
    _record,
    active_roles_for_user,
    effective_verb_over_keys,
    granted_covers_required,
    load_effective_grant_keys_for_roles,
    load_resource_parent_map,
    role_has_named_action,
)
from src.core.refresh_token_policy import get_valid_refresh_token_row
from src.core.request_logging import bind_request_context
from src.core.scope import scope_tiers_for_roles
from src.core.security import decode_access_token
from src.database.models import NavGateOverride, RefreshToken, User
from src.database.session import get_db_context

logger = logging.getLogger(__name__)


def _coerce_resource(resource: str) -> str:
    """Return the plain resource key a caller (Python or template) passed in.

    Deliberately does **not** validate the key against any registry: ``self.grants`` is keyed by
    the plain resource strings the DB catalog holds, which includes resources an admin added at
    runtime that no manifest declares. A genuine typo is caught by the CI guard over ``require()``
    call sites, not by a runtime crash here — a typo'd template resource string just resolves to
    "no grant" instead of throwing. Kept as a named function (rather than inlined) so that
    intent stays documented at the one place every check funnels through.
    """
    return resource


def _coerce_verb(verb: PermissionVerb | str) -> PermissionVerb:
    """Accept a verb enum or its wire string (as templates pass it) and return the enum."""
    return verb if isinstance(verb, PermissionVerb) else PermissionVerb(verb)


def _default_surface_scope(surface_key: str) -> GrantScope:
    """Return the Python-declared tier for ``surface_key``, or ``BUSINESS`` when none is declared.

    The :meth:`NavVisibility.can_surface` fallback for a console sub-tab with no
    ``nav_gate_overrides`` row yet — a fresh database, or the window between ``alembic upgrade
    head`` and ``python -m src.core.rbac_manifest_sync`` — read off the manifest that declares the
    tab (Issue #165). A lazy import for the same reason :func:`_all_named_action_pairs` uses one:
    the manifest registry imports the whole module tree, and this module is imported by it.

    ``BUSINESS`` — the closed tier — is the fallback on purpose: a surface nothing declares is
    assumed to be a back-office one, so forgetting to declare a tier under-grants rather than
    over-grants.
    """
    from src.core.rbac_manifest_registry import manifest_nav_scope_map

    return manifest_nav_scope_map().get(surface_key, GrantScope.BUSINESS)


@dataclass(frozen=True, slots=True)
class NavGate:
    """One surface's resolved gate: a resource key, a verb or named action, and a scope tier.

    The in-memory twin of one ``nav_gate_overrides`` row (Issue #146) — never constructed from a
    Python default directly, only from a DB row that exists precisely because an admin (or a
    manifest sync) wrote one. A surface with no row simply falls back to whatever
    :class:`~src.core.nav_registry.NavDestination`/``NavMeta`` already declares in code — see
    :meth:`NavVisibility._surface_allowed`.

    ``scope`` (Issue #165) is the tier the caller's grant must *reach*, not the tier they hold: a
    ``business`` surface refuses an ``own`` grant on the very same resource and verb.
    """

    resource: str
    verb: PermissionVerb | None
    action: str | None
    scope: GrantScope = GrantScope.BUSINESS


def load_nav_gate_overrides(db: Session) -> dict[str, NavGate]:
    """Return every ``nav_gate_overrides`` row as ``{surface_key: NavGate}`` (Issue #146).

    A plain, uncached table read — nav visibility is already recomputed fresh every request (no
    persistent grant cache sits in front of it the way ``effective_role_permissions`` does), so a
    console edit here is reflected on the very next request with no extra invalidation machinery
    needed.
    """
    rows = db.execute(select(NavGateOverride)).scalars().all()
    return {
        row.surface_key: NavGate(
            resource=row.resource_key,
            verb=PermissionVerb(row.verb) if row.verb is not None else None,
            action=row.action,
            scope=GrantScope(row.scope),
        )
        for row in rows
    }


@dataclass(frozen=True)
class NavVisibility:
    """The caller's rendered navigation & action visibility (UX only), evaluated from the registry.

    Rendering reads three things off this object:

    * :meth:`can` — the pure ``(resource, verb)`` grant check for an in-page action button; **not**
      surface-overridable (see :meth:`can` for why) — a re-gate only ever affects :meth:`visible`
      and :meth:`can_surface`.
    * :meth:`visible` / :meth:`group_visible` / :meth:`group_href` — the registry-driven rail and
      grouped-tab visibility;
    * :meth:`can_surface` — the same check for a console sub-tab that has no standalone
      :class:`~src.core.nav_registry.NavDestination` of its own (e.g. a Communications
      Inbox/Sent/Drafts/Deleted tab, Issue #145). Named ``can_manage`` until Issue #164 (M28),
      when the management-role wall it applied on top of the grant was deleted;
    * the ``show_*`` properties — a backward-compatible projection of :meth:`visible` kept for the
      per-role unit tests and JSON bootstrap payloads.

    :meth:`visible` and :meth:`can_surface` are **surface-overridable** (Issue #146): each resolves
    its gate through ``gate_overrides`` first — a DB row an admin can re-point to a different
    resource/verb (or named action) from ``/admin/rbac/catalog`` with no deploy, the Option C
    hybrid ``docs/architecture/rbac-universal-surface-framework.md`` §5 recommends — and falls back
    to the Python-declared default (``NavDestination.resource``/``.verb``/``.scope``, or the
    resource string itself for a bare ``can_surface`` call) when no override row exists. Only the
    *gate* is DB-overridable; the surface's *structure* (label, icon, href, group, order) stays
    Python-declared, unchanged from before this issue — see §6 of that doc, which narrows Issue
    #146's own (stale) acceptance criteria down to exactly this.

    A surface gate has **two** axes since Issue #165 (M28): the verb (or named action), and the
    :class:`~src.commons.enums.GrantScope` **tier** the caller's grant on that resource must reach.
    Both are read off the grant an admin wrote — never off the caller's role name, which Issue #164
    removed from this layer for good. The tier is what stops a coarse portal grant
    (``maintenance:CREATE`` at ``own``, meant for "file a request from my portal") from cascading
    the staff request board, the work-order board and the inspections console open, which is
    precisely the regression #164 shipped and this class now closes. See :meth:`scope_for` and
    :meth:`_surface_allowed`.

    ``all_access`` short-circuits every check to ``True`` (auth disabled — development); an
    unauthenticated caller (``authenticated=False``) sees nothing but the signed-out shell.
    """

    authenticated: bool
    all_access: bool = False
    # resource wire-value -> the role's effective (inherited) maximum verb; empty when anonymous.
    grants: Mapping[str, PermissionVerb] = field(default_factory=dict)
    # resource wire-value -> the caller's effective (inherited) scope tier on it (Issue #165),
    # resolved by the *same* resolver and over the *same* roles as ``grants`` above
    # (:func:`~src.core.scope.scope_tiers_for_roles`), so a verb and a tier can never come from
    # different role sets. A key absent here has no grant anywhere in its chain, and
    # :meth:`scope_for` resolves it to the narrowest tier — such a caller already fails the verb
    # check, so the tier only ever decides *how* a grant-less caller is refused.
    grant_scopes: Mapping[str, GrantScope] = field(default_factory=dict)
    # surface_key -> its DB-overridden gate (Issue #146); a surface with no entry here uses the
    # Python-declared default. Empty for an anonymous/all-access caller (never consulted then).
    gate_overrides: Mapping[str, NavGate] = field(default_factory=dict)
    # surface_keys whose override gate is a *named action* (not a verb) the caller currently holds
    # — precomputed once at build time since a named-action check needs a live DB query
    # (:func:`~src.core.rbac.role_has_named_action`), unlike the plain verb grants in ``grants``.
    named_action_surfaces_ok: frozenset[str] = frozenset()
    # Every ``(resource_key, action_key)`` pair the caller holds, across the whole named-action
    # catalog (Issue #155) — precomputed the same way as ``named_action_surfaces_ok`` (one query
    # per pair at build time), but keyed by the pair itself rather than a surface key, so
    # :meth:`can_action` can answer an in-page button's check the same way :meth:`can` answers a
    # verb check. Distinct from ``named_action_surfaces_ok``: that set only covers surfaces with a
    # DB *override* row; this one covers every named action declared in code (a manifest's
    # ``named_actions`` or the enum-era ``default_named_permissions``), whether or not anyone has
    # ever re-gated it from the console.
    named_actions_held: frozenset[tuple[str, str]] = frozenset()

    # --- Grant checks --------------------------------------------------------------------

    def can(
        self,
        resource: str,
        verb: PermissionVerb | str = PermissionVerb.READ,
        scope: GrantScope | str = GrantScope.OWN,
        *,
        trace: Trace = None,
    ) -> bool:
        """Return True when the caller holds at least ``verb`` on ``resource``, at ``scope`` or wider.

        The pure grant check behind in-page action buttons (create/edit/delete/lifecycle).
        Deliberately **not** surface-overridable (Issue #146): an in-page button has no single
        stable "surface key" of its own the way a nav destination or console tab does — it just
        checks whatever resource the surrounding page logic already decided on. Re-gating is scoped
        to :meth:`visible` and :meth:`can_surface`, the two entry points that already have one.

        ``scope`` (Issue #168, M28) mirrors :func:`src.api.rbac_deps.require`'s argument of the same
        name, so a template gate and the route dependency behind it are written the same way and
        can be compared mechanically::

            {% if can('maintenance.work_orders', 'update', 'business') %}   {# the template #}
            Depends(require("maintenance.work_orders", "update", scope=GrantScope.BUSINESS))

        It defaults to :data:`~src.commons.enums.GrantScope.OWN` — the narrowest tier, which every
        tier satisfies — so a control whose route requires no particular tier needs no third
        argument. Passing the wrong tier is the same class of mistake as naming the wrong resource
        (Issue #155): the button would show for a caller the route then refuses, which is exactly
        the click-then-403 Issue #168 exists to remove.
        """
        if not self.authenticated:
            _record(
                trace,
                TraceStage.SURFACE_GATE,
                TraceOutcome.DENIED,
                "not authenticated",
                resource=_coerce_resource(resource),
            )
            return False
        if self.all_access:
            _record(
                trace,
                TraceStage.SURFACE_GATE,
                TraceOutcome.FINAL,
                "auth disabled (development shell): every grant check passes",
                resource=_coerce_resource(resource),
            )
            return True
        resource_key = _coerce_resource(resource)
        held_tier = self.scope_for(resource_key)
        required_tier = GrantScope(scope)
        if not held_tier.satisfies(required_tier):
            _record(
                trace,
                TraceStage.SCOPE_TIER,
                TraceOutcome.CAPPED,
                "%s: holds tier %s, needs %s",
                resource_key,
                held_tier.value,
                required_tier.value,
                resource=resource_key,
                value=held_tier.value,
            )
            return False
        held_verb = self.grants.get(resource_key)
        allowed = granted_covers_required(held_verb, _coerce_verb(verb))
        _record(
            trace,
            TraceStage.VERB_RESOLUTION,
            TraceOutcome.FINAL if allowed else TraceOutcome.DENIED,
            "%s: holds %s at tier %s, needs %s at %s",
            resource_key,
            held_verb.value if held_verb is not None else "no verb",
            held_tier.value,
            str(_coerce_verb(verb)),
            required_tier.value,
            resource=resource_key,
            value=held_verb.value if held_verb is not None else None,
        )
        return allowed

    def can_action(self, resource: str, action: str) -> bool:
        """Return True when the caller holds the named ``action`` on ``resource`` (Issue #155).

        The named-action counterpart to :meth:`can`: a button gated by ``require_action(...)`` at
        the route (``sign``, ``approve``, ...) cannot have its visibility expressed through
        :meth:`can`, which only ever checks a cumulative CRUD verb — that gap is exactly how
        ``lease_detail.html``'s "Send for signature" button ended up gated on
        ``can('lease.signature', 'read')`` in the template while its route required the ``sign``
        action underneath (Issue #150's self-reported, Issue #155's confirmed and fixed mismatch).
        Like :meth:`can`, not surface-overridable — an in-page button has no surface key of its
        own; see :meth:`can`'s own docstring for why.
        """
        if not self.authenticated:
            return False
        if self.all_access:
            return True
        return (_coerce_resource(resource), action) in self.named_actions_held

    def scope_for(self, resource: str) -> GrantScope:
        """Return the caller's effective :class:`~src.commons.enums.GrantScope` on ``resource``.

        The tier half of the grant matrix (Issue #165), resolved exactly as
        :attr:`grants` resolves the verb half — nearest grant in the resource tree wins, widest tier
        at that level wins across the role closure. A resource with no grant anywhere in its chain
        resolves to :data:`~src.commons.enums.GrantScope.OWN`, the narrowest tier: the closed
        default, matching :func:`~src.core.scope.resolve_scope_tier`'s own fallback.
        """
        return self.grant_scopes.get(_coerce_resource(resource), GrantScope.OWN)

    def _surface_allowed(
        self,
        surface_key: str,
        default_resource: str,
        default_verb: PermissionVerb | str,
        default_scope: GrantScope,
        *,
        trace: Trace = None,
    ) -> bool:
        """Resolve ``surface_key``'s current gate (DB override, else the Python default) and check it.

        Shared by :meth:`visible` and :meth:`can_surface` (Issue #146) — the one place either method
        decides *what* to check before deferring to :meth:`can` or a named-action lookup.

        Both axes are resolved from the same source (Issue #165): if a DB override row exists it
        supplies the resource, the requirement **and** the required tier; otherwise all three come
        from the Python-declared default. The tier check runs against whichever resource won, so
        re-pointing a surface at another resource re-points the tier check with it.

        A **named-action** gate is deliberately exempt from the tier check. The tier exists to stop
        a *cumulative* verb grant from reaching further than it was meant to: verbs are ordered and
        inherit down the whole resource tree, which is exactly how one self-service role's coarse
        grant once reached a staff board (M28). A named action has neither property
        — :func:`~src.core.rbac.named_action_allowed` matches an exact ``(resource, action)`` pair
        and only cascades to descendants when a grant opts in explicitly — so the over-reach the
        tier closes cannot arise, and applying it anyway would silently break the capability an
        ``action`` re-gate exists to express (a role whose *only* grant is that named action holds
        no cumulative grant to carry a tier at all, and would resolve to the narrowest one).
        """
        override = self.gate_overrides.get(surface_key)
        if override is not None and override.action is not None:
            held = surface_key in self.named_action_surfaces_ok
            _record(
                trace,
                TraceStage.SURFACE_GATE,
                TraceOutcome.FINAL if held else TraceOutcome.DENIED,
                "surface %s is re-gated to the named action %s:%s — %s",
                surface_key,
                override.resource,
                override.action,
                "held" if held else "not held",
                resource=override.resource,
                value=override.action,
            )
            return held
        resource = default_resource if override is None else override.resource
        required_scope = default_scope if override is None else override.scope
        _record(
            trace,
            TraceStage.SURFACE_GATE,
            TraceOutcome.MATCHED if override is not None else TraceOutcome.SKIPPED,
            "surface %s gates on %s:%s at tier %s (%s)",
            surface_key,
            resource,
            str(override.verb or default_verb)
            if override is not None
            else str(default_verb),
            required_scope.value,
            "DB gate override" if override is not None else "Python-declared default",
            resource=resource,
            value=required_scope.value,
        )
        held_tier = self.scope_for(resource)
        if not held_tier.satisfies(required_scope):
            _record(
                trace,
                TraceStage.SCOPE_TIER,
                TraceOutcome.CAPPED,
                "%s: holds tier %s, the surface requires %s",
                resource,
                held_tier.value,
                required_scope.value,
                resource=resource,
                value=held_tier.value,
            )
            return False
        if override is None:
            return self.can(default_resource, default_verb, trace=trace)
        return self.can(override.resource, override.verb or default_verb, trace=trace)

    # --- Registry-driven destination visibility ------------------------------------------

    def visible(self, key: str, *, trace: Trace = None) -> bool:
        """Return True when the registry destination ``key`` renders for the caller.

        Visibility is the destination's current gate — a DB override if an admin has re-pointed it,
        else its Python-declared ``resource``/``verb``/``scope`` (Issues #146, #165) — and **nothing
        else** since Issue #164 (M28). It used to AND that grant with ``is_manager``, a role-*type*
        flag derived from a fixed three-name portal-role set, for the 14 destinations that defaulted
        to ``management=True``. That wall existed because a bare READ could not separate "my lease"
        from "every lease" (Issue #103); Issue #172 deleted the name set itself.

        Issue #164 deleted the wall on the grounds that ``role_permission.scope`` (Issue #156) now
        carries that distinction — correctly, but only at the *data* layer: this method still
        compared verbs alone, so ``own`` and ``business`` were indistinguishable here and two dozen
        staff consoles opened for the source project's self-service roles. Issue #165
        finished the job by giving the *surface* a required tier and comparing the caller's
        effective tier against it. The result is the wall's guarantee without the wall's flaw: a
        brand-new portal-style role is still expressible (its tier is whatever an admin granted),
        and a role name still decides nothing.
        """
        if self.all_access:
            _record(
                trace,
                TraceStage.SURFACE_GATE,
                TraceOutcome.FINAL,
                "auth disabled (development shell): destination %s renders for any session",
                key,
                resource=key,
            )
            return self.authenticated
        dest = destination(key)
        return self._surface_allowed(
            key, dest.resource, dest.verb, dest.scope, trace=trace
        )

    def can_surface(
        self,
        resource: str,
        verb: PermissionVerb | str = PermissionVerb.READ,
        scope: GrantScope | str | None = None,
        *,
        trace: Trace = None,
    ) -> bool:
        """Return True when the caller's gate for the ``resource`` **surface** admits them.

        The sub-surface counterpart of :meth:`visible`, for a console tab that has no standalone
        nav destination of its own — a maintenance *requests* / *work-orders* board (Issue #143), or
        a Communications Inbox/Sent/Drafts/Deleted tab (Issue #145). Surface-overridable
        (Issue #146) by treating ``resource`` itself as the surface key — the same convention
        :func:`~src.core.rbac_manifest_sync.sync_module_manifest` already seeds a default row under
        (its own ``full_key``), so an admin re-pointing an existing tab's gate just edits that row
        rather than needing a second, parallel key space. ``all_access`` (auth disabled)
        short-circuits to the auth state.

        **Named ``can_manage`` until Issue #164 (M28)**, when it also required a management
        (non-portal) role. That wall is gone for the same reason it is gone from :meth:`visible`,
        and the method kept its own name rather than collapsing into :meth:`can`: the two are *not*
        equivalent — this one resolves a DB gate override for the surface first, and ``can`` is
        deliberately not surface-overridable. Renaming rather than deleting keeps that distinction
        legible at every call site.

        ``scope`` is the tier the surface requires when no override row supplies one (Issue #165).
        Left ``None`` it resolves from the manifest that declares the surface
        (:func:`~src.core.rbac_manifest_registry.manifest_nav_scope_map`), falling back to
        ``BUSINESS`` — the closed tier — for a surface no manifest declares. That default is what
        keeps a staff sub-tab (``maintenance.requests``, ``communications.announcements.inbox``)
        shut for a portal-scoped caller without every call site having to remember to say so; a
        first-person sub-tab opts out by declaring ``own`` on its own ``NavMeta``.
        """
        if self.all_access:
            _record(
                trace,
                TraceStage.SURFACE_GATE,
                TraceOutcome.FINAL,
                "auth disabled (development shell): surface %s opens for any session",
                resource,
                resource=resource,
            )
            return self.authenticated
        surface_key = _coerce_resource(resource)
        required = (
            _default_surface_scope(surface_key) if scope is None else GrantScope(scope)
        )
        return self._surface_allowed(surface_key, resource, verb, required, trace=trace)

    def group_visible(self, group_key: str) -> bool:
        """Return True when any destination in the grouped-rail ``group_key`` is visible."""
        return any(self.visible(k) for k in group(group_key).member_keys)

    def group_href(self, group_key: str) -> str:
        """Return the href the group icon opens: the first member the caller can reach.

        Falls back to the first member's href when none are visible; the group icon is not
        rendered in that case (see :meth:`group_visible`), so the fallback is never followed.
        """
        grp = group(group_key)
        for member_key in grp.member_keys:
            if self.visible(member_key):
                return destination(member_key).href
        return destination(grp.member_keys[0]).href

    # --- Backward-compatible per-destination projection ----------------------------------

    @property
    def show_dashboard(self) -> bool:
        """The dashboard tile — shown to every signed-in user."""
        return self.authenticated

    @property
    def show_icon_sidebar(self) -> bool:
        """The signed-in icon rail — shown whenever there is a valid signed-in shell."""
        return self.authenticated

    @property
    def show_logs(self) -> bool:
        """The application-logs console."""
        return self.visible("logs")

    @property
    def show_rbac(self) -> bool:
        """The RBAC admin console."""
        return self.visible("rbac")

    @property
    def show_reports(self) -> bool:
        """The reports hub."""
        return self.visible("reports")

    @property
    def show_properties(self) -> bool:
        """The properties & units console."""
        return self.visible("properties")

    @property
    def show_tenants(self) -> bool:
        """The tenant-directory console."""
        return self.visible("tenants")

    @property
    def show_leases(self) -> bool:
        """The leases console."""
        return self.visible("leases")

    @property
    def show_payments(self) -> bool:
        """The invoices & payments console."""
        return self.visible("invoices")

    @property
    def show_inspections(self) -> bool:
        """The inspections console."""
        return self.visible("inspections")

    @property
    def show_maintenance(self) -> bool:
        """The maintenance requests & work orders console."""
        return self.visible("maintenance")

    @property
    def show_vendors(self) -> bool:
        """The vendor-directory console."""
        return self.visible("vendors")

    @property
    def show_applications(self) -> bool:
        """The application-screening console."""
        return self.visible("applications")

    @property
    def show_lease_templates(self) -> bool:
        """The lease-template manager."""
        return self.visible("lease-templates")

    @property
    def show_messages(self) -> bool:
        """The staff messaging console."""
        return self.visible("messages")

    @property
    def show_notifications(self) -> bool:
        """The notification-delivery viewer."""
        return self.visible("notifications")

    def as_json_dict(self) -> dict[str, bool]:
        """Serialize the per-destination flags for JSON bootstrap payloads."""
        return {
            "show_dashboard": self.show_dashboard,
            "show_logs": self.show_logs,
            "show_rbac": self.show_rbac,
            "show_reports": self.show_reports,
            "show_icon_sidebar": self.show_icon_sidebar,
            "show_properties": self.show_properties,
            "show_tenants": self.show_tenants,
            "show_leases": self.show_leases,
            "show_payments": self.show_payments,
            "show_inspections": self.show_inspections,
            "show_maintenance": self.show_maintenance,
            "show_vendors": self.show_vendors,
            "show_applications": self.show_applications,
            "show_lease_templates": self.show_lease_templates,
            "show_messages": self.show_messages,
            "show_notifications": self.show_notifications,
        }


def _all_access_nav() -> NavVisibility:
    """The development shell: auth disabled, so every destination and action renders."""
    return NavVisibility(authenticated=True, all_access=True)


def _signed_out_nav() -> NavVisibility:
    """The signed-out shell: nothing but the public affordances renders."""
    return NavVisibility(authenticated=False)


def _all_known_resource_keys(parent_map: dict[str, str | None]) -> set[str]:
    """Return every resource key that exists anywhere — the live catalog plus every manifest.

    ``parent_map`` is :func:`~src.core.rbac.load_resource_parent_map`'s result, which already
    unions the DB ``resources`` catalog with every registered manifest's declared keys (Issue
    #154) — so a resource an admin added at runtime *and* one a module declares but the next
    ``rbac_manifest_sync`` has not written yet are both walked by :func:`_effective_grants`.
    Before Issue #154 this seeded the set from the ``PermissionResource`` enum; the catalog and
    the manifests together are its strictly larger successor.
    """
    return set(parent_map)


def _effective_grants(
    db: Session, roles: Sequence[str]
) -> tuple[dict[str, PermissionVerb], dict[str, GrantScope]]:
    """Return the caller's effective ``(verbs, scope tiers)`` across every known resource key.

    Walks :func:`~src.core.rbac.effective_verb_over_keys` — the same machinery
    ``ensure_permission_key``/``require()`` use at the API layer — over every key in
    :func:`_all_known_resource_keys`, so the nav-visibility layer stays at parity with API
    enforcement for every resource, however it entered the catalog.

    The tier half (Issue #165) comes from :func:`~src.core.scope.scope_tiers_for_roles` — the bulk
    form of the very resolver ``resolve_scope`` uses at the data layer, given the *same* role list
    this function resolves verbs for. Reusing it (rather than re-deriving the tier here) is what
    guarantees the surface gate and the row-scoping behind it can never disagree about how wide a
    caller's grant reaches.
    """
    grant_rows = load_effective_grant_keys_for_roles(db, roles)
    parent_map = load_resource_parent_map(db)
    keys = _all_known_resource_keys(parent_map)
    grants: dict[str, PermissionVerb] = {}
    for key in keys:
        verb_value = effective_verb_over_keys(grant_rows, parent_map, key)
        if verb_value is not None:
            grants[key] = PermissionVerb(verb_value)
    return grants, scope_tiers_for_roles(db, roles, keys)


def valid_refresh_token_row_for_request(
    db: Session, request: Request
) -> RefreshToken | None:
    """Return the valid ``refresh_token`` row for the request cookie, or None."""
    raw = request.cookies.get(get_settings().refresh_token_cookie_name)
    return get_valid_refresh_token_row(db, raw)


def peek_user_from_refresh_cookie(db: Session, request: Request) -> User | None:
    """Resolve the signed-in user from the httpOnly refresh cookie without updating last_seen.

    An account that has been switched off resolves to ``None`` (Issue 22), the same answer the API
    path gives through :func:`~src.core.security.find_active_user`: deactivation revokes the refresh
    families as well, but the shell must not depend on that having happened — a page rendered for a
    deactivated staff member is a page they should not see, whatever cookie they still hold.
    """
    row = valid_refresh_token_row_for_request(db, request)
    if row is None:
        return None
    user = db.execute(select(User).where(User.id == row.user_id)).scalar_one_or_none()
    if user is None or not user.is_active or user.is_deleted:
        return None
    return user


def peek_access_subject_from_access_cookie(request: Request) -> str | None:
    """Resolve JWT ``sub`` from the access-token cookie, or ``None`` when missing/invalid."""
    raw = request.cookies.get(get_settings().access_token_cookie_name)
    if not raw or not raw.strip():
        return None
    payload = decode_access_token(raw.strip())
    if payload is None:
        return None
    subject = str(payload.get("sub") or "").strip()
    return subject or None


def access_subject_matches_user(access_subject: str, user: User) -> bool:
    """Return True when JWT subject belongs to ``user``."""
    normalized = access_subject.strip().lower()
    if not normalized:
        return False
    user_id = str(user.id).strip().lower()
    user_email = str(user.email or "").strip().lower()
    return normalized in {user_id, user_email}


def _named_action_surfaces_ok(
    db: Session, roles: Sequence[str], gate_overrides: Mapping[str, NavGate]
) -> frozenset[str]:
    """Return the subset of ``gate_overrides`` keys, gated by a named action, ``role`` holds.

    A handful of rows at most (most overrides are plain verbs, resolved straight off ``grants``
    with no extra query) — checked individually via :func:`~src.core.rbac.role_has_named_action`
    rather than precomputing every possible ``(resource, action)`` pair up front.
    """
    return frozenset(
        surface_key
        for surface_key, gate in gate_overrides.items()
        if gate.action is not None
        and role_has_named_action(db, list(roles), gate.resource, gate.action)
    )


def _all_named_action_pairs() -> frozenset[tuple[str, str]]:
    """Return every ``(resource_key, action_key)`` pair declared anywhere as a named action.

    Unions the manifest-declared catalog (:func:`~src.core.rbac_manifest.named_action_keys` over
    :data:`~src.core.rbac_manifest_registry.ALL_MANIFESTS`) with the enum-era catalog
    (:func:`~src.core.rbac.default_named_permissions`) — a lazy import, same reasoning as
    :func:`_all_known_resource_keys`. A handful of pairs today (``lease.signature:sign``,
    ``application.screening:approve``, ``inspection.deductions:approve/reject``); cheap to walk in
    full on every request.
    """
    from src.core.rbac import default_named_permissions
    from src.core.rbac_manifest import named_action_keys
    from src.core.rbac_manifest_registry import ALL_MANIFESTS

    pairs: set[tuple[str, str]] = set(default_named_permissions())
    for manifest in ALL_MANIFESTS:
        pairs |= named_action_keys(manifest)
    return frozenset(pairs)


def _named_actions_held(
    db: Session, roles: Sequence[str]
) -> frozenset[tuple[str, str]]:
    """Return the subset of :func:`_all_named_action_pairs` that ``role`` currently holds.

    One :func:`~src.core.rbac.role_has_named_action` query per catalog pair — see that function's
    own docstring for the grant resolution; kept a plain loop (not batched) since the catalog is
    small and this mirrors :func:`_named_action_surfaces_ok`'s existing shape.
    """
    return frozenset(
        (resource, action)
        for resource, action in _all_named_action_pairs()
        if role_has_named_action(db, list(roles), resource, action)
    )


def nav_visibility_for_role(db: Session, role: str) -> NavVisibility:
    """Compute registry-driven nav/action visibility for a bare ``role`` name (Issue #174, M29).

    Extracted from :func:`nav_visibility_for_user`, which is now a thin wrapper over it, so the
    simulator can answer "can this role open this page?" **through the object the shell itself
    renders from** rather than through a second copy of the surface rules. The simulator's whole
    value depends on that: a simulator with its own copy of the gate answers confidently and
    wrongly the moment the two drift.

    Always authenticated and never ``all_access``: a simulation asks what a signed-in holder of
    ``role`` reaches under enforcement, which is not a question a development shell with auth
    disabled has an answer to.
    """
    return nav_visibility_for_roles(db, [role])


def nav_visibility_for_roles(db: Session, roles: Iterable[str]) -> NavVisibility:
    """Compute registry-driven visibility for the **union** of ``roles`` (Issue 48).

    The shape :func:`~src.core.rbac.ensure_permission_key` resolves a request with: every role the
    caller holds in the scope the request names, unioned. :func:`nav_visibility_for_role` is its
    one-role case. An empty ``roles`` grants nothing, so every gated destination stays hidden.
    """
    role_list = list(dict.fromkeys(role.strip() for role in roles if role.strip()))
    gate_overrides = load_nav_gate_overrides(db)
    grants, grant_scopes = _effective_grants(db, role_list)
    return NavVisibility(
        authenticated=True,
        grants=grants,
        grant_scopes=grant_scopes,
        gate_overrides=gate_overrides,
        named_action_surfaces_ok=_named_action_surfaces_ok(
            db, role_list, gate_overrides
        ),
        named_actions_held=_named_actions_held(db, role_list),
    )


def nav_visibility_at_site(db: Session, user: User, site_id: str) -> NavVisibility:
    """Compute visibility from the roles ``user`` holds **at one clinic** (Issue 48).

    A clinic's screens are gated the way their API is: :func:`~src.core.site_scope.require_site_access`
    checks a grant with the roles held at the site named in the path (plus any unscoped role), so a
    manager at Clinic A is a receptionist at Clinic B if that is what they were given there. Building
    the rail from :attr:`~src.database.models.User.role`, the single-role mirror, would show a
    two-clinic staff member the same menu at both, and a link the site's own gate then refuses.

    Resolved with :func:`~src.core.rbac.active_roles_for_user`, the function enforcement uses, so the
    two answer from one role set. Auth disabled (development) shows everything, as elsewhere.
    """
    if not get_settings().auth_enabled:
        return _all_access_nav()
    roles = active_roles_for_user(
        db, user, scope_type=AssignmentScopeType.SITE.value, scope_id=site_id
    )
    return nav_visibility_for_roles(db, sorted(roles))


def nav_visibility_for_user(
    db: Session,
    user: User | None,
    *,
    auth_enabled: bool,
) -> NavVisibility:
    """Compute registry-driven nav/action visibility for a user (or anonymous)."""
    if not auth_enabled:
        return _all_access_nav()
    if user is None:
        return _signed_out_nav()
    return nav_visibility_for_role(db, (user.role or "").strip())


def nav_visibility_for_request(db: Session, request: Request) -> NavVisibility:
    """Full pipeline: refresh cookie + access cookie → user → registry-driven visibility."""
    settings = get_settings()
    session_user = peek_user_from_refresh_cookie(db, request)
    if settings.auth_enabled:
        access_subject = peek_access_subject_from_access_cookie(request)
        if session_user is None and access_subject is not None:
            logger.debug(
                "nav_visibility: access cookie present without refresh session; deny shell",
                extra={"path": str(request.url.path)},
            )
            return nav_visibility_for_user(db, None, auth_enabled=True)
        if session_user is not None and access_subject is None:
            logger.debug(
                "nav_visibility: refresh session present but access cookie missing/invalid; deny shell",
                extra={"path": str(request.url.path), "user_id": str(session_user.id)},
            )
            return nav_visibility_for_user(db, None, auth_enabled=True)
        if (
            session_user is not None
            and access_subject is not None
            and not access_subject_matches_user(access_subject, session_user)
        ):
            logger.warning(
                "nav_visibility: access subject does not match refresh session user; deny shell",
                extra={"path": str(request.url.path), "user_id": str(session_user.id)},
            )
            return nav_visibility_for_user(db, None, auth_enabled=True)
    if session_user is not None:
        # The page's log lines name who asked for it (Issue 6): the user's id, never the email.
        bind_request_context(actor_id=str(session_user.id))
    return nav_visibility_for_user(db, session_user, auth_enabled=settings.auth_enabled)


def nav_visibility_for_request_safe(request: Request) -> NavVisibility:
    """Load nav visibility for ``request``; on DB errors return a deny-all shell (auth on)."""
    try:
        with get_db_context() as db:
            return nav_visibility_for_request(db, request)
    except Exception:
        logger.warning("nav_visibility: DB error; using deny-all flags", exc_info=True)
        if not get_settings().auth_enabled:
            return _all_access_nav()
        return _signed_out_nav()
