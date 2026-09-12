"""Explicit aggregator of every module's RBAC manifest (Issue #149, M26).

One import + one tuple entry per module — recommended over filesystem auto-discovery
(design doc §6) because it matches how routers are already registered
(``src/api/v1/router.py``), stays greppable/PR-reviewable, and fails loudly (an import error) on a
typo'd module path instead of silently never registering it.

``python -m scripts.db.seed_rbac`` walks this tuple on every deploy; the CI guard
(``tests/unit/security/test_rbac_manifest_registry.py``) walks it to validate every
``require()``/``require_action()`` call site's resource key.

This tuple is the **only** source of truth for the resource catalog. Every resource key the
application ships is declared by exactly one manifest below, so :func:`manifest_resource_rows` /
:func:`manifest_parent_map` derive the catalog's shape (keys, names, parents) from it. Adding a
resource means editing a module's own ``rbac_manifest.py`` — never a shared file — per
``docs/architecture/rbac-module-self-registration.md`` §7.

Manifests outside ``src/modules/`` live next to the routes they gate: ``rbac``, ``dashboard`` and
the ``reports`` admin-shell hub (with its ``logs``/``users`` children) in
``src/api/v1/routes/rbac_manifest.py``.
"""

from src.api.v1.routes.rbac_manifest import (
    DASHBOARD_MANIFEST,
    RBAC_MANIFEST,
    REPORTS_SHELL_MANIFEST,
)
from src.commons.enums import GrantScope, ScopeShape
from src.core.rbac_manifest import ModuleManifest, RoleGrant, iter_resources
from src.modules.audit.rbac_manifest import MANIFEST as AUDIT_MANIFEST
from src.modules.communications.rbac_manifest import MANIFEST as COMMUNICATIONS_MANIFEST
from src.modules.documents.rbac_manifest import MANIFEST as DOCUMENTS_MANIFEST
from src.modules.patients.rbac_manifest import MANIFEST as PATIENTS_MANIFEST
from src.modules.queues.rbac_manifest import MANIFEST as QUEUES_MANIFEST
from src.modules.sites.rbac_manifest import MANIFEST as SITES_MANIFEST
from src.modules.widgets.rbac_manifest import MANIFEST as WIDGETS_MANIFEST

# ── your manifests ───────────────────────────────────────────────────────────
# from src.modules.<name>.rbac_manifest import MANIFEST as <NAME>_MANIFEST

ALL_MANIFESTS: tuple[ModuleManifest, ...] = (
    DASHBOARD_MANIFEST,
    REPORTS_SHELL_MANIFEST,
    RBAC_MANIFEST,
    COMMUNICATIONS_MANIFEST,
    DOCUMENTS_MANIFEST,
    AUDIT_MANIFEST,
    WIDGETS_MANIFEST,
    PATIENTS_MANIFEST,
    SITES_MANIFEST,
    QUEUES_MANIFEST,
)


def manifest_resource_rows() -> list[tuple[str, str, str | None, str | None]]:
    """Return every declared resource as ``(key, name, parent_key, description)``, parents first.

    :func:`~src.core.rbac_manifest.iter_resources` yields each manifest's root before its
    descendants, so a consumer that wires ``parent_key`` in one pass never references a key it has
    not seen yet.
    """
    return [
        (node.full_key, node.name, node.parent_full_key, node.description)
        for manifest in ALL_MANIFESTS
        for node in iter_resources(manifest)
    ]


def manifest_scope_shape_map() -> dict[str, ScopeShape]:
    """Return ``{resource_key: scope_shape}`` for every resource a manifest declares one for.

    The manifest-derived source for :func:`src.core.scope.scope_shape_for` (Issue #156, M28): a
    module declares what "the caller's own rows" means for its own subtree, next to the subtree
    itself, and a resource that declares nothing (its own or inherited) is simply absent here — the
    resolver applies its documented default rather than this map guessing one.
    """
    return {
        node.full_key: node.scope_shape
        for manifest in ALL_MANIFESTS
        for node in iter_resources(manifest)
        if node.scope_shape is not None
    }


def manifest_nav_scope_map() -> dict[str, GrantScope]:
    """Return ``{surface_key: required scope tier}`` for every manifest node carrying ``NavMeta``.

    The Python-declared default half of Issue #165's surface tier, for the sub-tabs that have no
    :class:`~src.core.nav_registry.NavDestination` of their own.
    :meth:`src.core.nav_visibility.NavVisibility.can_surface` consults this when the surface has no
    ``nav_gate_overrides`` row to read the tier off — a fresh database, or a deployment between
    ``alembic upgrade head`` and ``make seed-rbac``.

    A surface key absent from this map has no manifest-declared tier at all; the consuming gate
    applies its own documented default (``business``, the closed one) rather than guessing.
    """
    return {
        node.full_key: GrantScope(node.nav.scope)
        for manifest in ALL_MANIFESTS
        for node in iter_resources(manifest)
        if node.nav is not None
    }


def manifest_role_grants() -> list[RoleGrant]:
    """Return every default grant declared on a registered manifest (Issue #169, M28)."""
    return [grant for manifest in ALL_MANIFESTS for grant in manifest.grants]


def shipped_role_grants() -> list[RoleGrant]:
    """Return **every** default ``role_permission`` row a deployment ships with (Issue #169, M28).

    Two sources, in precedence order:

    * the **manifest-declared** grants (:func:`manifest_role_grants`) — where a module ships its
      grants alongside its resources, and where yours belong;
    * the **system** grants in :func:`src.core.rbac.default_role_permissions` — admin over the
      admin shell (``dashboard``, ``users``, ``logs``, ``rbac``) and READ on ``dashboard`` for
      every signed-in user. These are the floor a deployment cannot boot without, which is why
      they are code rather than a manifest.

    De-duplicated on ``(role, resource, action)`` with the **first** occurrence winning, matching
    the insert-if-absent rule every seed path in this codebase uses — so a manifest declaring a
    grant the system floor also carries is a no-op rather than a conflict.
    """
    from src.core.rbac import default_role_permissions

    system: list[RoleGrant] = [
        RoleGrant(role=role, resource=resource, verb=verb.value)
        for role, resource, verb in default_role_permissions()
    ]
    seen: set[tuple[str, str, str | None]] = set()
    out: list[RoleGrant] = []
    for grant in (*manifest_role_grants(), *system):
        key = (grant.role, grant.resource, grant.action)
        if key in seen:
            continue
        seen.add(key)
        out.append(grant)
    return out


def shipped_named_action_grants() -> list[RoleGrant]:
    """Return every default **named-action** grant a deployment ships with (Issue #169, M28).

    Kept separate from :func:`shipped_role_grants` because a named action is not a rung on the
    cumulative ladder: its rows live in the ``action IS NOT NULL`` space the verb resolver never
    reads, and they are inserted with ``max_verb=None``.

    The kernel ships none — a named action is a domain capability (*sign* this, *approve* that), so
    it is declared by the module that owns the verb, on its own manifest's ``named_actions``.
    """
    out: list[RoleGrant] = []
    seen: set[tuple[str, str, str]] = set()
    for grant in manifest_role_grants():
        if not grant.action:
            continue
        key = (grant.role, grant.resource, grant.action)
        if key in seen:
            continue
        seen.add(key)
        out.append(grant)
    return out


def manifest_parent_map() -> dict[str, str | None]:
    """Return ``{resource_key: parent_key}`` over every registered manifest (``None`` for a root).

    Used as the in-code fallback wherever the DB ``resources`` catalog has not been seeded yet — a
    fresh dev database, a test that seeds nothing, or the window between ``alembic upgrade head``
    and ``make seed-rbac``.
    """
    return {
        key: parent for key, _name, parent, _description in manifest_resource_rows()
    }


def manifest_resource_keys() -> frozenset[str]:
    """Return every resource key declared by any registered manifest."""
    return frozenset(
        key for key, _name, _parent, _description in manifest_resource_rows()
    )
