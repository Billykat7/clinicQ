"""Module self-registration vocabulary for RBAC (Issue #149, M26).

Two files were hand-maintained, shared chokepoints every new module had to edit:
``PermissionResource``/``PERMISSION_RESOURCE_PARENT`` (``src.commons.enums``) and the
per-``(resource, verb)`` named dependency functions in ``src.api.rbac_deps``. This module is the
fix's declarative half: a :class:`ModuleManifest` lets a module declare its own name, full
resource tree (any nesting depth), actions and nav placement in **one file inside its own
directory** (``src/modules/<name>/rbac_manifest.py``). :mod:`src.core.rbac_manifest_sync` pushes a
manifest into the DB catalog idempotently; :func:`src.api.rbac_deps.require` replaces the
per-resource dependency functions. Full design:
``docs/architecture/rbac-module-self-registration.md``.

The enum survived as a compat shim through this issue and the M27 module rollout, and was
permanently deleted by Issue #154 (M27). A manifest is now the *only* way a resource enters the
catalog.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from src.commons.enums import GrantScope, ScopeShape


@dataclass(frozen=True, slots=True)
class NavMeta:
    """Nav placement + gate metadata carried alongside a resource (design doc §3, §8).

    ``icon``/``group``/``href``/``label``/``tab`` are informational — the actual sidebar/tab
    *structure* stays Python-declared (``src.core.nav_registry`` today, or a module's own
    routes+templates once Issue #146 lands), because a genuinely new tab always needs a new route
    and template regardless of where its metadata lives
    (``docs/architecture/rbac-universal-surface-framework.md`` §5, Option C). Only the *gate*
    (``verb`` or ``action``, and since Issue #165 the required ``scope`` tier) is synced into the
    ``nav_gate_overrides`` table as the shipped default a later admin edit can re-point without a
    deploy.
    """

    icon: str | None = None
    group: str | None = None
    href: str | None = None
    label: str | None = None
    tab: bool = False
    # The requirement this node's own surface gates on. Exactly one of ``verb``/``action`` is
    # meaningful at a time — set ``action`` for a named-action gate and leave ``verb`` at its
    # default; :func:`src.core.rbac_manifest_sync.sync_module_manifest` treats ``action`` as
    # authoritative when both would otherwise apply.
    verb: str = "read"
    action: str | None = None
    # The **scope tier** the caller's grant must reach for this surface to open (Issue #165, M28) —
    # the sub-tab counterpart of :attr:`src.core.nav_registry.NavDestination.scope`, and synced into
    # ``nav_gate_overrides.scope`` alongside the verb/action so an admin can re-point it too. The
    # ``business`` default matches every back-office tab; a module sets ``own`` on a tab that is
    # itself a first-person view (Communications *Messages*, the report tabs).
    scope: str = GrantScope.BUSINESS.value


@dataclass(frozen=True, slots=True)
class RoleGrant:
    """One default ``role_permission`` row a module ships with (Issue #169, M28).

    Default grants used to live only in hand-written Alembic migrations, which meant a new module
    could not ship its own: adding one was a shared-file edit in a numbered revision, the exact
    chokepoint :class:`ModuleManifest` exists to remove for resources. A module now declares its
    grants next to the resources they are grants *on*, and the one seed entrypoint
    (``python -m scripts.db.seed_rbac``) applies them.

    ``scope`` is the :class:`~src.commons.enums.GrantScope` tier (Issue #156); left ``None`` it
    resolves from :func:`~src.core.rbac.seeded_grant_scope`, the seed-data table, so a module
    declaring a grant for a portal role gets ``own`` and one for ``admin`` gets ``business``
    without having to know the convention. Note this is *not*
    :func:`~src.core.rbac.default_grant_scope`, which since Issue #172 is the narrowest tier for
    every role: a shipped seed and an unspecified new grant are different statements.

    Historical grants stay where they are. The Alembic revisions that seeded them already ran on
    every live database, and history is immutable — moving them here would rewrite what those
    revisions mean. :func:`~src.core.rbac_manifest_registry.shipped_role_grants` is the single
    reader that unions both sources, so a consumer never has to know which is which.
    """

    role: str
    resource: str
    verb: str
    scope: str | None = None
    action: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """One node in a module's resource tree — module, submodule, tab or sub-tab, uniformly.

    ``key`` is this node's own path segment; the DB key it resolves to is
    ``f"{parent_full_key}.{key}"`` unless ``full_key`` is given, in which case that value is used
    verbatim (and becomes the prefix its own children compose against). The override exists only
    for resources predating this framework whose dotted key does not match their parent's key
    (e.g. ``lease.details`` under the ``leases`` root, or ``units`` — no dot at all — under
    ``properties``) — a brand-new module should never need it.
    """

    key: str
    name: str
    description: str | None = None
    full_key: str | None = None
    # ``None`` inherits the nearest ancestor's (ultimately the module's) actions; an explicit
    # tuple (including an empty one) overrides it for this node and everything below it.
    actions: tuple[str, ...] | None = None
    named_actions: tuple[str, ...] = ()
    nav: NavMeta | None = None
    # ``None`` inherits the nearest ancestor's (ultimately the module's) shape; an explicit value
    # overrides it for this node and everything below it (Issue #156, M28). The shape says what
    # "the caller's own rows" means here, which is what a ``scope=own`` grant narrows by.
    scope_shape: ScopeShape | None = None
    children: tuple[ResourceSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    """A module's whole RBAC footprint: name, resource tree, default actions and nav.

    The root of a :class:`ResourceSpec` tree plus module-level bookkeeping. One manifest per
    module, co-located with its routes (``src/modules/<name>/rbac_manifest.py``) and registered in
    ``ALL_MANIFESTS`` (``src.core.rbac_manifest_registry``) — see
    ``docs/architecture/rbac-module-self-registration.md``.
    """

    key: str
    name: str
    description: str | None = None
    actions: tuple[str, ...] = ("read", "create", "update", "delete")
    named_actions: tuple[str, ...] = ()
    nav: NavMeta | None = None
    # The default ``role_permission`` rows this module ships with (Issue #169, M28) — see
    # :class:`RoleGrant`. Empty for a module whose grants predate the framework and still live in
    # their original Alembic revision.
    grants: tuple[RoleGrant, ...] = ()
    # The module-wide default shape a ``scope=own`` grant on any of its resources narrows by
    # (Issue #156, M28); a child overrides it for its own subtree. Left unset, the module's
    # resources resolve to ``src.core.scope.DEFAULT_SCOPE_SHAPE``.
    scope_shape: ScopeShape | None = None
    children: tuple[ResourceSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class FlatResource:
    """One :func:`iter_resources`-resolved node: a DB-ready key, parent and effective actions."""

    full_key: str
    name: str
    description: str | None
    parent_full_key: str | None
    actions: tuple[str, ...]
    named_actions: tuple[str, ...]
    nav: NavMeta | None
    #: The effective scope shape (this node's own, else the nearest ancestor's) — ``None`` when
    #: neither this node nor any ancestor declares one (Issue #156).
    scope_shape: ScopeShape | None = None


def iter_resources(manifest: ModuleManifest) -> Iterator[FlatResource]:
    """Walk a manifest's tree depth-first, yielding one :class:`FlatResource` per node.

    Resolves each node's dotted DB key (the ``full_key`` override, else
    ``f"{parent_full_key}.{key}"``), its effective actions and its effective scope shape (its own,
    else the nearest ancestor's — the module's own when nothing overrides it), so callers never
    re-derive the inheritance/override rules themselves. The module root is always yielded first.
    """
    yield FlatResource(
        full_key=manifest.key,
        name=manifest.name,
        description=manifest.description,
        parent_full_key=None,
        actions=manifest.actions,
        named_actions=manifest.named_actions,
        nav=manifest.nav,
        scope_shape=manifest.scope_shape,
    )
    yield from _iter_children(
        manifest.children, manifest.key, manifest.actions, manifest.scope_shape
    )


def _iter_children(
    children: tuple[ResourceSpec, ...],
    parent_full_key: str,
    inherited_actions: tuple[str, ...],
    inherited_scope_shape: ScopeShape | None,
) -> Iterator[FlatResource]:
    """Recursive helper for :func:`iter_resources` — resolves one sibling level at a time."""
    for child in children:
        full_key = child.full_key or f"{parent_full_key}.{child.key}"
        actions = child.actions if child.actions is not None else inherited_actions
        scope_shape = (
            child.scope_shape
            if child.scope_shape is not None
            else inherited_scope_shape
        )
        yield FlatResource(
            full_key=full_key,
            name=child.name,
            description=child.description,
            parent_full_key=parent_full_key,
            actions=actions,
            named_actions=child.named_actions,
            nav=child.nav,
            scope_shape=scope_shape,
        )
        yield from _iter_children(child.children, full_key, actions, scope_shape)


def all_full_keys(manifest: ModuleManifest) -> frozenset[str]:
    """Return every resource key (module root + every descendant) a manifest declares."""
    return frozenset(node.full_key for node in iter_resources(manifest))


def named_action_keys(manifest: ModuleManifest) -> frozenset[tuple[str, str]]:
    """Return every ``(resource_key, action_key)`` pair a manifest declares as a named action."""
    return frozenset(
        (node.full_key, action)
        for node in iter_resources(manifest)
        for action in node.named_actions
    )
