"""Catalog-integrity guard for the DB resource catalog (Issues #139/#154).

The resource/action/permission catalog lives in the ``resources``/``actions``/``permissions``
tables. Since Issue #154 (M27) its shape is declared by the module manifests registered in
``ALL_MANIFESTS`` — the ``PermissionResource`` enum these tests used to check the seed against was
permanently deleted — so these are now the guard that :func:`seed_resource_catalog` (the Python twin
of ``alembic upgrade head && python -m src.core.rbac_manifest_sync``) reproduces the **manifests**
exactly: keys, names, the parent tree, and one permission row per resource x its own declared
actions. That the manifests in turn reproduce the *shipped, pre-M27* catalog is asserted separately,
against a committed snapshot, in ``tests/integration/test_rbac_m27_full_parity.py``.

Essential, isolated RBAC invariants (per ``docs/IDE/RULES/testing-strategy.mdc``); the Postgres
trigger/closure behaviour is exercised by the Postgres-gated integration suite. Seeding here uses the
Python twins (``seed_resource_catalog`` / ``refresh_resource_descendants_py``), since the unit DB is
SQLite.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Generator

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import PermissionVerb
from src.core.rbac import (
    catalog_display_name,
    catalog_resource_closure,
    permission_key,
    refresh_resource_descendants_py,
    seed_resource_catalog,
)
from src.core.rbac_manifest import iter_resources
from src.core.rbac_manifest_registry import (
    ALL_MANIFESTS,
    manifest_parent_map,
    manifest_resource_keys,
)
from src.database.models import Action, Permission, Resource, ResourceDescendant


def _declared_actions_by_resource() -> dict[str, tuple[str, ...]]:
    """``{resource_key: cumulative actions}`` as the manifests declare them.

    Not every resource carries all four verbs: ``reporting`` and its children are ``("read",)``
    only (Issue #153 — nothing writes to a report), which is why the permission cross-product below
    is per-resource rather than ``len(resources) * len(actions)`` as it was under the enum.
    """
    return {
        node.full_key: node.actions
        for manifest in ALL_MANIFESTS
        for node in iter_resources(manifest)
    }


@pytest.fixture
def catalog_db(session_factory: sessionmaker[Session]) -> Generator[Session]:
    """A session with the catalog seeded and ``resource_descendants`` populated (Python twins)."""
    with session_factory() as db:
        seed_resource_catalog(db)
        db.commit()
        refresh_resource_descendants_py(db)
        db.commit()
        yield db


def test_resource_keys_equal_the_registered_manifests(catalog_db: Session) -> None:
    """Every manifest-declared key is a seeded resource, and nothing extra is seeded."""
    seeded = {r.key for r in catalog_db.execute(select(Resource)).scalars().all()}
    assert seeded == set(manifest_resource_keys())


def test_resource_parents_reproduce_the_manifest_parent_map(
    catalog_db: Session,
) -> None:
    """Each seeded resource's parent key equals what its manifest declared (roots have none)."""
    rows = catalog_db.execute(select(Resource)).scalars().all()
    by_id = {r.id: r for r in rows}
    expected = manifest_parent_map()
    for row in rows:
        parent_key = by_id[row.parent_id].key if row.parent_id is not None else None
        assert parent_key == expected[row.key], f"{row.key} parent"


def test_actions_are_the_four_cumulative_verbs(catalog_db: Session) -> None:
    """The seeded actions are exactly ``read``/``create``/``update``/``delete``.

    The named, non-cumulative actions (``sign``/``approve``/``reject``/``send``) are layered on by
    :func:`~src.core.rbac.seed_named_catalog`, which this fixture deliberately does not call.
    """
    seeded = {a.key for a in catalog_db.execute(select(Action)).scalars().all()}
    assert seeded == {verb.value for verb in PermissionVerb}


def test_every_resource_is_system(catalog_db: Session) -> None:
    """Every seeded resource and action is a protected system row (Issue #141 delete guard)."""
    assert all(
        r.is_system for r in catalog_db.execute(select(Resource)).scalars().all()
    )
    assert all(a.is_system for a in catalog_db.execute(select(Action)).scalars().all())


def test_permissions_cover_every_declared_resource_action_pair(
    catalog_db: Session,
) -> None:
    """``permissions`` holds exactly one row per ``(resource, its own declared action)``."""
    declared = _declared_actions_by_resource()
    expected = sum(len(actions) for actions in declared.values())
    permission_count = catalog_db.execute(
        select(func.count()).select_from(Permission)
    ).scalar_one()
    assert permission_count == expected
    assert catalog_db.execute(
        select(func.count()).select_from(Action)
    ).scalar_one() == len(PermissionVerb)
    assert catalog_db.execute(
        select(func.count()).select_from(Resource)
    ).scalar_one() == len(declared)


def test_every_declared_resource_action_pair_has_a_permission(
    catalog_db: Session,
) -> None:
    """Each ``(resource_key, action)`` a manifest declares resolves to a permission row."""
    resource_id_by_key = {
        r.key: r.id for r in catalog_db.execute(select(Resource)).scalars().all()
    }
    action_id_by_key = {
        a.key: a.id for a in catalog_db.execute(select(Action)).scalars().all()
    }
    pairs = {
        (p.resource_id, p.action_id)
        for p in catalog_db.execute(select(Permission)).scalars().all()
    }
    for resource_key, actions in _declared_actions_by_resource().items():
        for action_key in actions:
            key = (resource_id_by_key[resource_key], action_id_by_key[action_key])
            assert key in pairs, (
                f"missing permission for {permission_key(resource_key, action_key)}"
            )


def test_resource_descendants_equal_the_closure(catalog_db: Session) -> None:
    """The populated closure equals the manifest-derived ``(ancestor, descendant)`` key pairs."""
    key_by_id = {
        r.id: r.key for r in catalog_db.execute(select(Resource)).scalars().all()
    }
    seeded_pairs = {
        (key_by_id[row.ancestor_id], key_by_id[row.descendant_id])
        for row in catalog_db.execute(select(ResourceDescendant)).scalars().all()
    }
    assert seeded_pairs == catalog_resource_closure()


def test_closure_includes_every_self_pair(catalog_db: Session) -> None:
    """Every resource is its own descendant (the self-pair the cascade join relies on)."""
    key_by_id = {
        r.id: r.key for r in catalog_db.execute(select(Resource)).scalars().all()
    }
    self_pairs = {
        key_by_id[row.ancestor_id]
        for row in catalog_db.execute(select(ResourceDescendant)).scalars().all()
        if row.ancestor_id == row.descendant_id
    }
    assert self_pairs == set(manifest_resource_keys())


def test_logs_and_users_stay_under_the_reports_shell_hub() -> None:
    """The admin-shell hub's two children keep their historical parent (Issue #154).

    ``logs`` and ``users`` have been children of the ``reports`` hub since the catalog's first
    seed. ``logs`` was registered as a standalone *root* manifest by Issue #152 (``reports`` had no
    manifest of its own yet), which was harmless only because ``sync_module_manifest`` never clears
    a parent. Now that the manifests are the sole source of truth the parenting has to be declared,
    so this pins it: losing it in a future refactor would silently change what a ``reports`` grant
    cascades into.
    """
    parents = manifest_parent_map()
    assert parents["logs"] == "reports"
    assert parents["users"] == "reports"
    assert parents["reports"] is None


def test_reseeding_inserts_nothing(catalog_db: Session) -> None:
    """Re-running the Python seed on a seeded catalog adds no rows (idempotent, like the SQL seed)."""
    before = (
        catalog_db.execute(select(func.count()).select_from(Resource)).scalar_one(),
        catalog_db.execute(select(func.count()).select_from(Action)).scalar_one(),
        catalog_db.execute(select(func.count()).select_from(Permission)).scalar_one(),
    )
    seed_resource_catalog(catalog_db)
    catalog_db.commit()
    after = (
        catalog_db.execute(select(func.count()).select_from(Resource)).scalar_one(),
        catalog_db.execute(select(func.count()).select_from(Action)).scalar_one(),
        catalog_db.execute(select(func.count()).select_from(Permission)).scalar_one(),
    )
    assert before == after


def test_catalog_display_name_humanises_a_dotted_key() -> None:
    """``catalog_display_name`` still derives a label from a key, for actions and ad-hoc rows."""
    assert catalog_display_name("payment.late_fees") == "Payment / Late Fees"
    assert catalog_display_name("read") == "Read"
