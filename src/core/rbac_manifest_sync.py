"""Idempotent DB sync for a module's RBAC manifest (Issue #149, M26).

Written once, reused by every module forever: a new module never needs new sync code, only a new
``rbac_manifest.py`` and a one-line entry in ``ALL_MANIFESTS``
(``src.core.rbac_manifest_registry``). Mirrors the read-into-dict / diff-by-natural-key / flush-
then-wire-parents idiom ``src.core.rbac.seed_resource_catalog`` already established for the
enum-derived catalog (Issue #139), so the two seeding paths stay consistent while the enum survives
as a compat shim.

Deploy wiring: run as the second half of every deploy, chained after the schema migration —
``alembic upgrade head && python -m src.core.rbac_manifest_sync`` (this module's ``__main__``
block) — never as a new Alembic migration per module (design doc §4: the *data* converges to match
the code on every deploy; only the table *shape* needs migration history).
"""

from collections.abc import Iterable
from dataclasses import dataclass, fields

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import PermissionEffect
from src.core.rbac import catalog_display_name, rebuild_resource_closure
from src.core.rbac_manifest import ModuleManifest, iter_resources
from src.database.models import (
    Action,
    NavGateOverride,
    Permission,
    RbacRole,
    Resource,
    RolePermission,
)


@dataclass(slots=True)
class SyncReport:
    """Counts of what one sync call changed. All-zero means the manifest was already in sync."""

    roles_created: int = 0
    resources_created: int = 0
    resources_updated: int = 0
    actions_created: int = 0
    permissions_created: int = 0
    nav_gates_created: int = 0
    grants_created: int = 0
    named_grants_created: int = 0

    def summary(self) -> str:
        """Render the report as one deploy-log line (Issue #169, M28).

        ``roles +0, resources +3, actions +0, permissions +12, nav-gates +15, grants +0,
        named-grants +0`` — so a deploy log shows what a sync actually changed rather than only
        that it ran.
        """
        return (
            f"roles +{self.roles_created}, "
            f"resources +{self.resources_created} (updated {self.resources_updated}), "
            f"actions +{self.actions_created}, permissions +{self.permissions_created}, "
            f"nav-gates +{self.nav_gates_created}, grants +{self.grants_created}, "
            f"named-grants +{self.named_grants_created}"
        )

    @property
    def is_noop(self) -> bool:
        """Return True when nothing changed — the expected result of re-running a stable manifest."""
        return not any(getattr(self, f.name) for f in fields(self))

    def __add__(self, other: SyncReport) -> SyncReport:
        """Return the field-wise sum of two reports (used to total a multi-manifest sync run)."""
        return SyncReport(
            **{
                f.name: getattr(self, f.name) + getattr(other, f.name)
                for f in fields(self)
            }
        )


def sync_module_manifest(db: Session, manifest: ModuleManifest) -> SyncReport:
    """Idempotently upsert one module's resource tree, actions, permissions and nav-gate defaults.

    - INSERT a resource/action/permission row that doesn't exist yet (by natural key: ``key`` for
      resources/actions, ``(resource_id, action_id)`` for permissions).
    - UPDATE a resource's ``name``/``description`` when the manifest's value changed.
    - **Never DELETE** — removing a resource is a deliberate, audited admin action from the catalog
      console, not a side effect of a deploy.
    - Seed a ``nav_gate_overrides`` default (resource, verb/action **and** required scope tier —
      Issue #165) only when the surface has no row yet — once seeded, the row is DB-authoritative,
      so a later sync must never silently revert a live admin re-gate.

    Safe to call twice with the same manifest: the second call returns an all-zero
    (:attr:`SyncReport.is_noop`) report. Callers commit; this function only flushes.
    """
    report = SyncReport()
    nodes = list(iter_resources(manifest))

    # -- Resources: insert missing, update a changed name/description, wire parents once ids exist.
    resource_by_key: dict[str, Resource] = {
        row.key: row for row in db.execute(select(Resource)).scalars().all()
    }
    for node in nodes:
        existing = resource_by_key.get(node.full_key)
        if existing is None:
            new_resource = Resource(
                key=node.full_key,
                name=node.name,
                description=node.description,
                is_system=True,
            )
            db.add(new_resource)
            resource_by_key[node.full_key] = new_resource
            report.resources_created += 1
        elif existing.name != node.name or existing.description != node.description:
            existing.name = node.name
            existing.description = node.description
            report.resources_updated += 1
    db.flush()  # assign ids before wiring self-referential parent_id below

    for node in nodes:
        if node.parent_full_key is not None:
            resource_by_key[node.full_key].parent_id = resource_by_key[
                node.parent_full_key
            ].id

    # -- Actions: ensure every cumulative + named action key this manifest uses exists globally.
    action_keys_needed: set[str] = set()
    for node in nodes:
        action_keys_needed.update(node.actions)
        action_keys_needed.update(node.named_actions)
    action_by_key: dict[str, Action] = {
        row.key: row for row in db.execute(select(Action)).scalars().all()
    }
    for key in sorted(action_keys_needed - action_by_key.keys()):
        new_action = Action(key=key, name=catalog_display_name(key), is_system=True)
        db.add(new_action)
        action_by_key[key] = new_action
        report.actions_created += 1
    db.flush()

    # -- Permissions: the (resource x its own actions) cross-product, plus one row per named action.
    existing_pairs = {
        (row.resource_id, row.action_id)
        for row in db.execute(select(Permission)).scalars().all()
    }
    for node in nodes:
        resource = resource_by_key[node.full_key]
        for action_key in (*node.actions, *node.named_actions):
            action = action_by_key[action_key]
            pair = (resource.id, action.id)
            if pair not in existing_pairs:
                db.add(Permission(resource_id=resource.id, action_id=action.id))
                existing_pairs.add(pair)
                report.permissions_created += 1

    # -- Nav-gate defaults: insert-only, one row per node that declares NavMeta.
    existing_surface_keys = {
        row[0] for row in db.execute(select(NavGateOverride.surface_key)).all()
    }
    for node in nodes:
        if node.nav is None or node.full_key in existing_surface_keys:
            continue
        db.add(
            NavGateOverride(
                surface_key=node.full_key,
                resource_key=node.full_key,
                verb=None if node.nav.action else node.nav.verb,
                action=node.nav.action,
                # The required scope tier ships as part of the default gate (Issue #165), so a
                # freshly synced database and one migrated by ``0065`` agree row for row.
                scope=node.nav.scope,
            )
        )
        report.nav_gates_created += 1

    # Bring the closure in line with the now-current `resources` table. Dialect-aware: on Postgres
    # the trigger has already done it (and rebuilding again in Python would collide with it), under
    # SQLite the Python twin is the only thing that maintains it.
    rebuild_resource_closure(db)
    return report


def sync_all_manifests(db: Session, manifests: Iterable[ModuleManifest]) -> SyncReport:
    """Sync every manifest in ``manifests`` in order; returns the field-wise summed report."""
    total = SyncReport()
    for manifest in manifests:
        total = total + sync_module_manifest(db, manifest)
    return total


# --------------------------------------------------------------------------------------
# Issue #169 (M28) — the two halves the manifest sync never covered
# --------------------------------------------------------------------------------------
#
# ``sync_module_manifest`` above walks *manifests*, so it can only ever seed a nav-gate default for
# a surface a manifest declares — 19 of them. The other 19 surfaces are ``NAV_DESTINATIONS``, which
# is Python-declared structure, not manifest data; they had no row at all. The console still listed
# all 38 because ``_all_nav_gate_defaults()`` merges the destinations in at read time, so nothing
# looked broken — but there was no single place to diff "what gates did we ship" against "what gates
# are live", and a surface only acquired a row the first time somebody edited it.
#
# Default *grants* had the same shape of gap one level down: they existed only inside hand-written
# Alembic revisions, so a fresh database's authorization state depended on which revisions had run
# rather than on what the code declares.


def sync_nav_destination_gates(db: Session) -> int:
    """Seed a ``nav_gate_overrides`` default for every :data:`NAV_DESTINATIONS` entry.

    Insert-only on ``surface_key``, exactly like the manifest path: once a row exists it is
    DB-authoritative, so a redeploy never reverts a live admin re-gate. Returns the number created.

    A destination's shipped gate is its ``resource``/``verb``/``scope`` — the same triple
    ``NavVisibility.visible`` falls back to when no row exists — so seeding the row changes no
    decision on the day it runs. What it changes is that the shipped default becomes *data*: visible
    in ``/admin/rbac/catalog/nav-gates`` as a row rather than a merged-in Python default, and
    diffable by :func:`~scripts.db.seed_rbac.check_drift`.
    """
    from src.core.nav_registry import NAV_DESTINATIONS

    existing = {row[0] for row in db.execute(select(NavGateOverride.surface_key)).all()}
    created = 0
    for dest in NAV_DESTINATIONS:
        if dest.key in existing:
            continue
        db.add(
            NavGateOverride(
                surface_key=dest.key,
                resource_key=dest.resource,
                verb=dest.verb.value,
                action=None,
                scope=dest.scope.value,
            )
        )
        created += 1
    return created


def sync_system_roles(db: Session) -> int:
    """Seed the roles a grant can be *held by*, before any grant references one.

    ``role_permission.role`` is a plain string, so a grant on a role with no ``rbac_role`` row
    inserts happily and then behaves like a role that does not exist: it grants nothing the
    resolver can find, and the console lists no role to edit. Seeding the rows first is what makes
    the rest of this sync mean anything.

    ``admin`` is seeded ``is_scope_exempt`` — the flag :func:`~src.core.scope.business_instance_ids`
    reads to decide "sees everything" — because a deployment whose administrator is narrowed to the
    instances they happen to be assigned has no way back in. Every other role is narrowable, which
    is the safe default for one you add.

    Insert-only on the name: an operator's edit to a seeded role's description or exemption
    survives a deploy, exactly as their edit to a seeded grant does.
    """
    from src.commons.enums import UserRole
    from src.core.rbac import default_system_roles

    existing = set(db.execute(select(RbacRole.name)).scalars().all())
    created = 0
    for name, description in default_system_roles():
        if name in existing:
            continue
        db.add(
            RbacRole(
                name=name,
                description=description,
                is_system=True,
                is_scope_exempt=(name == UserRole.ADMIN.value),
            )
        )
        created += 1
    return created


def sync_default_grants(db: Session) -> tuple[int, int]:
    """Seed every shipped ``role_permission`` row, cumulative and named (Issue #169, M28).

    Reads :func:`~src.core.rbac_manifest_registry.shipped_role_grants` and
    :func:`~src.core.rbac_manifest_registry.shipped_named_action_grants` — the one place that unions
    the manifest-declared grants with the historical helpers — and inserts what is missing.
    Insert-only on the natural key (``(role, resource)`` for a cumulative grant,
    ``(role, resource, action)`` for a named one), so an operator's own edit to a seeded grant
    survives a deploy: this brings a database *up to* the shipped catalog, it does not reset it to
    it. Returns ``(cumulative_created, named_created)``.

    A grant whose ``scope`` the manifest leaves unset resolves per role through
    :func:`~src.core.rbac.seeded_grant_scope` — the *seed-data* table, not
    :func:`~src.core.rbac.default_grant_scope`, which since Issue #172 is the narrowest tier for
    every role. The two were one function until then, which is exactly how a console-created grant
    for a new role came to be stored at the widest tier: seeding ``admin`` at ``business`` and
    defaulting an unspecified grant to ``business`` are different statements.
    """
    from src.core.rbac import seeded_grant_scope
    from src.core.rbac_manifest_registry import (
        shipped_named_action_grants,
        shipped_role_grants,
    )

    cumulative_existing = {
        (row.role, row.resource)
        for row in db.execute(
            select(RolePermission).where(RolePermission.action.is_(None))
        )
        .scalars()
        .all()
    }
    named_existing = {
        (row.role, row.resource, row.action)
        for row in db.execute(
            select(RolePermission).where(RolePermission.action.is_not(None))
        )
        .scalars()
        .all()
    }
    created = 0
    for grant in shipped_role_grants():
        if (grant.role, grant.resource) in cumulative_existing:
            continue
        cumulative_existing.add((grant.role, grant.resource))
        db.add(
            RolePermission(
                role=grant.role,
                resource=grant.resource,
                max_verb=grant.verb,
                scope=grant.scope or seeded_grant_scope(grant.role).value,
            )
        )
        created += 1
    named_created = 0
    for grant in shipped_named_action_grants():
        if (grant.role, grant.resource, grant.action) in named_existing:
            continue
        named_existing.add((grant.role, grant.resource, grant.action))
        db.add(
            RolePermission(
                role=grant.role,
                resource=grant.resource,
                action=grant.action,
                max_verb=None,
                effect=PermissionEffect.ALLOW.value,
                scope=grant.scope or seeded_grant_scope(grant.role).value,
            )
        )
        named_created += 1
    return created, named_created


def sync_rbac_catalog(db: Session) -> SyncReport:
    """Bring ``db`` to the full shipped catalog in one idempotent pass (Issue #169, M28).

    The single entrypoint the deploy path and ``scripts/db/seed_rbac.py`` both call: the system
    roles; every manifest's resources, actions and permissions; a nav-gate default for every
    surface (each manifest node carrying ``NavMeta``, plus every ``NAV_DESTINATIONS`` entry); and
    every shipped role grant, cumulative and named. Callers commit.

    Insert/update-only throughout — never a delete. An admin-added resource, action or re-gate must
    survive a deploy; pruning is a deliberate, audited console action, not a side effect of shipping.
    """
    from src.core.rbac_manifest_registry import ALL_MANIFESTS

    roles_created = sync_system_roles(db)
    db.flush()
    report = sync_all_manifests(db, ALL_MANIFESTS)
    report.roles_created += roles_created
    db.flush()
    report.nav_gates_created += sync_nav_destination_gates(db)
    grants, named = sync_default_grants(db)
    report.grants_created += grants
    report.named_grants_created += named
    return report


def _main() -> None:
    """Entrypoint for ``python -m src.core.rbac_manifest_sync`` — the deploy-time sync step.

    Kept as a thin alias of ``python -m scripts.db.seed_rbac`` (Issue #169) so the runbook line and
    the container entrypoint that already reference this module keep working; the script is the
    documented entrypoint because it is where ``--check`` and ``--dry-run`` live.
    """
    from src.database.session import get_db_context

    with get_db_context() as db:
        report = sync_rbac_catalog(db)
    print(f"rbac sync: {report.summary()}")  # noqa: T201 — a deploy-log line


if __name__ == "__main__":
    _main()
