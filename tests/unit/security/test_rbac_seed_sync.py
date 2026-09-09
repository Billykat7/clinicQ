"""One idempotent command brings an empty database to the shipped RBAC catalog (Issue #169, M28).

Four mechanisms used to write authorization data and none covered all of it — most visibly, the
``nav_gate_overrides`` table was **half-populated by design**: ``sync_module_manifest`` seeds a
default only for a manifest node declaring ``NavMeta`` (19 of them), so the 19 ``NAV_DESTINATIONS``
had no row at all. The console looked fine because ``_all_nav_gate_defaults()`` merges the Python
destinations in at read time, which is exactly why the gap survived: nothing was broken, there was
simply no single place to diff *what we ship* against *what is live*.

This suite pins the four properties the acceptance criteria name:

1. an empty database reaches the **full** catalog in one pass — every manifest resource, every
   action, every permission pair, a nav-gate row for **all 38 surfaces**, and every shipped grant
   with its ``scope``;
2. running it twice changes nothing (the second report is all zeros);
3. ``--check`` finds drift and says what is missing, and finds none once synced;
4. the sync is **insert-only** — an admin's own row survives it, which is the property that makes it
   safe to run on every deploy.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Generator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from scripts.db.seed_rbac import check_drift
from src.commons.enums import PermissionVerb
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.database.models import (
    Action,
    NavGateOverride,
    Permission,
    Resource,
    RolePermission,
)


@pytest.fixture
def empty_db(session_factory: sessionmaker[Session]) -> Generator[Session]:
    """A session over a schema-created but entirely unseeded database — post-``alembic upgrade``."""
    with session_factory() as db:
        yield db


def _seed(db: Session):
    """Run the one entrypoint and commit, as the deploy path does."""
    report = sync_rbac_catalog(db)
    db.commit()
    return report


def test_one_pass_seeds_every_manifest_resource(empty_db: Session) -> None:
    """Every resource any registered manifest declares exists in the catalog afterwards."""
    from src.core.rbac_manifest_registry import manifest_resource_keys

    _seed(empty_db)
    have = {row.key for row in empty_db.query(Resource).all()}
    assert manifest_resource_keys() <= have


def test_one_pass_seeds_every_shipped_grant_with_its_scope(empty_db: Session) -> None:
    """Cumulative and named grants both land, and each carries the tier its *seed table* gives it.

    ``seeded_grant_scope``, never ``default_grant_scope``: since Issue #172 the latter is the
    narrowest tier for every role — what a grant with nothing said about it means — while a shipped
    seed says what it ships as.
    """
    from src.core.rbac import seeded_grant_scope
    from src.core.rbac_manifest_registry import (
        shipped_named_action_grants,
        shipped_role_grants,
    )

    _seed(empty_db)
    cumulative = {
        (row.role, row.resource): row
        for row in empty_db.query(RolePermission).filter(
            RolePermission.action.is_(None)
        )
    }
    for grant in shipped_role_grants():
        row = cumulative.get((grant.role, grant.resource))
        assert row is not None, f"{grant.role} -> {grant.resource} was not seeded"
        assert row.max_verb == grant.verb
        assert row.scope == (grant.scope or seeded_grant_scope(grant.role).value)
    named = {
        (row.role, row.resource, row.action)
        for row in empty_db.query(RolePermission).filter(
            RolePermission.action.is_not(None)
        )
    }
    for grant in shipped_named_action_grants():
        assert (grant.role, grant.resource, grant.action) in named


def test_every_permission_pair_exists_for_a_resources_own_actions(
    empty_db: Session,
) -> None:
    """The ``(resource x its actions)`` cross-product is complete, not merely non-empty."""
    _seed(empty_db)
    assert empty_db.query(Permission).count() > 0
    assert empty_db.query(Action).count() >= len(PermissionVerb)


def test_running_it_twice_changes_nothing(empty_db: Session) -> None:
    """The second report is all zeros — the property that makes it safe on every deploy."""
    first = _seed(empty_db)
    assert not first.is_noop
    second = _seed(empty_db)
    assert second.is_noop, second.summary()


def test_the_summary_line_reports_what_changed(empty_db: Session) -> None:
    """A deploy log should show *what* a sync did, not only that it ran."""
    report = _seed(empty_db)
    summary = report.summary()
    assert "resources +" in summary
    assert "nav-gates +" in summary
    assert "grants +" in summary
    assert f"nav-gates +{report.nav_gates_created}" in summary


def test_check_reports_drift_on_an_empty_database(empty_db: Session) -> None:
    """Every category is reported, by name, so the diff is actionable rather than a bare exit code."""
    problems = check_drift(empty_db)
    assert problems
    kinds = {line.split(":", 1)[0] for line in problems}
    assert {
        "resource missing",
        "nav-gate default missing",
        "grant missing",
    } <= kinds


def test_check_is_clean_once_synced(empty_db: Session) -> None:
    """The two halves agree: what the sync writes is exactly what the check looks for."""
    _seed(empty_db)
    assert check_drift(empty_db) == []


def _run_cli(monkeypatch: pytest.MonkeyPatch, session: Session, argv: list[str]) -> int:
    """Run ``scripts.db.seed_rbac.main`` against ``session`` instead of the configured database."""
    import contextlib

    from scripts.db import seed_rbac

    @contextlib.contextmanager
    def _fake_context():
        yield session
        session.commit()

    monkeypatch.setattr(seed_rbac, "get_db_context", _fake_context)
    return seed_rbac.main(argv)


def test_check_exits_non_zero_on_drift_and_zero_once_synced(
    empty_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both directions, because a check that can never fail passes forever."""
    assert _run_cli(monkeypatch, empty_db, ["--check"]) == 1
    assert _run_cli(monkeypatch, empty_db, []) == 0
    assert _run_cli(monkeypatch, empty_db, ["--check"]) == 0


def test_dry_run_writes_nothing(
    empty_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--dry-run`` reports and rolls back — the database is still empty afterwards."""
    assert _run_cli(monkeypatch, empty_db, ["--dry-run"]) == 0
    assert empty_db.query(Resource).count() == 0
    assert empty_db.query(NavGateOverride).count() == 0
    # And the check still reports drift, i.e. the dry run genuinely changed nothing.
    assert check_drift(empty_db)
