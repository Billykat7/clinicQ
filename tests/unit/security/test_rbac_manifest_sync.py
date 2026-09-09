"""Sync-correctness + idempotency guard for the module manifest framework (Issue #149, M26).

``sync_module_manifest`` is written once and reused by every module forever, so its contract
(insert missing, update a changed name/description, **never** delete, seed a nav-gate default only
once) is exercised here directly against small, purpose-built manifests — isolated from the real
``ALL_MANIFESTS`` registry (covered separately by ``test_rbac_manifest_registry.py``) so a failure
here always points at the sync mechanism itself, not at any one module's data.
"""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core import rbac, rbac_manifest_sync
from src.core.rbac import rebuild_resource_closure
from src.core.rbac_manifest import ModuleManifest, NavMeta, ResourceSpec
from src.core.rbac_manifest_registry import ALL_MANIFESTS
from src.core.rbac_manifest_sync import (
    SyncReport,
    sync_all_manifests,
    sync_module_manifest,
)
from src.database.models import (
    Action,
    NavGateOverride,
    Permission,
    Resource,
    ResourceDescendant,
)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Generator[Session]:
    """A blank SQLite session (schema created, no catalog seeded)."""
    with session_factory() as session:
        yield session


def _small_manifest(*, description: str | None = "A test module.") -> ModuleManifest:
    """A minimal two-level manifest exercising nesting, a named action and NavMeta."""
    return ModuleManifest(
        key="widgets",
        name="Widgets",
        description=description,
        nav=NavMeta(icon="box", href="/admin/widgets"),
        children=(
            ResourceSpec(
                key="gadgets",
                name="Widgets / Gadgets",
                nav=NavMeta(tab=True, href="/admin/widgets/gadgets"),
                named_actions=("calibrate",),
            ),
        ),
    )


def test_sync_creates_resources_actions_and_permissions(db: Session) -> None:
    """A fresh manifest creates one resource per node, the actions it needs, and their permissions."""
    report = sync_module_manifest(db, _small_manifest())
    db.commit()

    assert report.resources_created == 2  # "widgets" + "widgets.gadgets"
    assert (
        report.actions_created == 5
    )  # read/create/update/delete + the named "calibrate"
    # 4 CRUD perms on the root + (4 CRUD + 1 named) on the child = 9.
    assert report.permissions_created == 9
    assert report.nav_gates_created == 2

    keys = {r.key for r in db.execute(select(Resource)).scalars().all()}
    assert keys == {"widgets", "widgets.gadgets"}


def test_sync_wires_parent_child_correctly(db: Session) -> None:
    """The child resource's ``parent_id`` points at the root's row."""
    sync_module_manifest(db, _small_manifest())
    db.commit()

    root = db.execute(select(Resource).where(Resource.key == "widgets")).scalar_one()
    child = db.execute(
        select(Resource).where(Resource.key == "widgets.gadgets")
    ).scalar_one()
    assert root.parent_id is None
    assert child.parent_id == root.id


def test_sync_is_idempotent(db: Session) -> None:
    """Re-running the same manifest reports an all-zero, no-op :class:`SyncReport`."""
    sync_module_manifest(db, _small_manifest())
    db.commit()

    second = sync_module_manifest(db, _small_manifest())
    db.commit()

    assert second == SyncReport()
    assert second.is_noop


def test_sync_updates_a_changed_name_without_creating_a_duplicate(db: Session) -> None:
    """A renamed node updates the existing row in place; the resource count is unchanged."""
    sync_module_manifest(db, _small_manifest())
    db.commit()

    renamed = ModuleManifest(key="widgets", name="Widgets (renamed)")
    report = sync_module_manifest(db, renamed)
    db.commit()

    assert report.resources_created == 0
    assert report.resources_updated == 1
    row = db.execute(select(Resource).where(Resource.key == "widgets")).scalar_one()
    assert row.name == "Widgets (renamed)"
    assert db.execute(select(Resource)).scalars().all()  # nothing else disturbed


def test_sync_never_deletes_a_resource_dropped_from_the_manifest(db: Session) -> None:
    """Removing a child from the manifest and re-syncing leaves its DB row in place."""
    sync_module_manifest(db, _small_manifest())
    db.commit()

    shrunk = ModuleManifest(key="widgets", name="Widgets")
    report = sync_module_manifest(db, shrunk)
    db.commit()

    assert report.resources_created == 0
    keys = {r.key for r in db.execute(select(Resource)).scalars().all()}
    assert keys == {"widgets", "widgets.gadgets"}


def test_sync_seeds_a_nav_gate_default_only_once(db: Session) -> None:
    """A nav-gate row is inserted on first sync and never overwritten by a later one."""
    sync_module_manifest(db, _small_manifest())
    db.commit()

    row = db.execute(
        select(NavGateOverride).where(NavGateOverride.surface_key == "widgets")
    ).scalar_one()
    assert row.resource_key == "widgets"
    assert row.verb == "read"
    assert row.action is None

    # Re-sync with a *different* NavMeta verb — the existing row must not change (insert-only,
    # "DB-authoritative post-seed").
    changed_gate = ModuleManifest(
        key="widgets", name="Widgets", nav=NavMeta(verb="update")
    )
    report = sync_module_manifest(db, changed_gate)
    db.commit()

    assert report.nav_gates_created == 0
    row_after = db.execute(
        select(NavGateOverride).where(NavGateOverride.surface_key == "widgets")
    ).scalar_one()
    assert row_after.verb == "read"


def test_sync_named_action_gets_its_own_permission(db: Session) -> None:
    """The named action gets a global ``Action`` row and a dedicated ``(resource, action)`` pair."""
    sync_module_manifest(db, _small_manifest())
    db.commit()

    action = db.execute(select(Action).where(Action.key == "calibrate")).scalar_one()
    gadgets = db.execute(
        select(Resource).where(Resource.key == "widgets.gadgets")
    ).scalar_one()
    pair = db.execute(
        select(Permission).where(
            Permission.resource_id == gadgets.id, Permission.action_id == action.id
        )
    ).scalar_one_or_none()
    assert pair is not None


def test_all_registered_manifests_sync_cleanly_and_idempotently(db: Session) -> None:
    """The real ``ALL_MANIFESTS`` registry syncs without error and converges on a second run."""
    first = sync_all_manifests(db, ALL_MANIFESTS)
    db.commit()
    assert first.resources_created > 0

    second = sync_all_manifests(db, ALL_MANIFESTS)
    db.commit()
    assert second.is_noop


# --- The closure rebuild is dialect-aware -------------------------------------------------
#
# Postgres maintains `resource_descendants` itself: `0052`'s statement-level `trg_resources_changed`
# trigger rebuilds the whole closure after every statement touching `resources`. Rebuilding it again
# in Python is not the harmless duplicate it reads as — the twin's DELETE flushes immediately while
# its re-INSERTs stay ORM-pending until after the next `resources` write re-fires the trigger, so the
# two rebuilds collide and `python -m src.core.rbac_manifest_sync` died on `pk_resource_descendants`
# against a real Postgres (never caught: the suite is SQLite-only, where there is no trigger at all).
#
# These pin the guard rather than the crash — the crash needs a live Postgres, the guard is what
# stands between the two rebuilds, and it is what regressed.


def _bound_to(dialect_name: str) -> SimpleNamespace:
    """A stand-in session exposing only what the dialect branch reads: ``get_bind().dialect.name``.

    Deliberately not a patched real ``Session``: the session consults ``get_bind()`` itself to get a
    connection, so faking it on a live session breaks the ORM rather than the dialect check. On the
    Postgres path ``rebuild_resource_closure`` touches nothing else, so a stub says exactly as much.
    """
    bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))
    return SimpleNamespace(get_bind=lambda: bind)


def test_rebuild_resource_closure_defers_to_the_trigger_on_postgres() -> None:
    """On Postgres the closure rebuild is a no-op — running the twin there is what crashed."""
    with patch.object(rbac, "refresh_resource_descendants_py") as twin:
        rebuild_resource_closure(_bound_to("postgresql"))  # type: ignore[arg-type]
    twin.assert_not_called()


def test_rebuild_resource_closure_runs_the_twin_off_postgres(db: Session) -> None:
    """The guard must not over-fire: with no trigger, the twin is all that maintains the closure."""
    with patch.object(rbac, "refresh_resource_descendants_py") as twin:
        rebuild_resource_closure(db)
    twin.assert_called_once_with(db)


def test_sync_rebuilds_the_closure_through_the_dialect_aware_entrypoint(
    db: Session,
) -> None:
    """Sync must go through the guard, not call the twin itself — calling it directly was the bug."""
    with (
        patch.object(rbac_manifest_sync, "rebuild_resource_closure") as guarded,
        patch.object(rbac, "refresh_resource_descendants_py") as twin,
    ):
        sync_module_manifest(db, _small_manifest())

    guarded.assert_called_once_with(db)
    twin.assert_not_called()


def test_sync_maintains_the_closure_on_sqlite(db: Session) -> None:
    """End-to-end on the dialect CI actually runs: the closure reflects the synced tree."""
    sync_module_manifest(db, _small_manifest())
    db.commit()

    # "widgets" and "widgets.gadgets" each paired with itself, plus root -> child.
    assert len(db.execute(select(ResourceDescendant)).scalars().all()) == 3
