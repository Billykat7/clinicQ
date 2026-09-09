#!/usr/bin/env python3
"""Bring a database to the shipped RBAC catalog — the one seed/sync entrypoint (Issue #169, M28).

Four different mechanisms used to put authorization data into the catalog, and none of them covered
all of it:

* ``resources``/``actions``/``permissions`` came from ``sync_module_manifest`` **and** from the
  enum-era Alembic revisions — two writers for one table, so a fresh database's contents depended on
  which had run;
* ``nav_gate_overrides`` defaults were seeded only for manifest nodes declaring ``NavMeta`` (19 of
  them), so the 19 ``NAV_DESTINATIONS`` had **no row at all**. The console still listed all 38
  because it merges the Python defaults in at read time, so nothing looked broken — but there was no
  single place to diff "what gates did we ship" against "what gates are live";
* ``role_permission`` defaults existed only inside hand-written migrations, so a module could not
  ship its own grants;
* and **nothing ran any of it**: ``make manifest-sync`` existed and the runbook documented it, but
  no workflow or container entrypoint invoked it, or ``alembic upgrade`` either.

This command is the single idempotent pass over all of it. Run it after ``alembic upgrade head``::

    python -m scripts.db.seed_rbac                 # apply
    python -m scripts.db.seed_rbac --dry-run       # report what would change, write nothing
    python -m scripts.db.seed_rbac --check         # exit 1 with a diff if the DB has drifted

``--check`` is what CI runs against a throwaway database, so drift fails a PR instead of a deploy.

**Insert/update-only, never delete.** An admin-added resource, action or re-gate must survive a
deploy; pruning is a deliberate, audited console action, not a side effect of shipping. That is also
what makes ``--check`` meaningful in one direction only: it reports what the manifests declare and
the database lacks, never the reverse.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.rbac_manifest_sync import SyncReport, sync_rbac_catalog
from src.database.models import (
    Action,
    NavGateOverride,
    RbacRole,
    Resource,
    RolePermission,
)
from src.database.session import get_db_context


def _expected_surface_keys() -> set[str]:
    """Every surface a nav-gate default should exist for: manifest ``NavMeta`` nodes + destinations.

    Thirty-eight today (19 + 19) — the two halves have disjoint key spaces on purpose: a manifest
    node's key is its dotted resource path (``communications.messages.inbox``) and a destination's is
    its registry key (``messages``), which is what ``active_nav`` and the template lookups already
    use.
    """
    from src.core.nav_registry import NAV_DESTINATIONS
    from src.core.rbac_manifest import iter_resources
    from src.core.rbac_manifest_registry import ALL_MANIFESTS

    keys = {
        node.full_key
        for manifest in ALL_MANIFESTS
        for node in iter_resources(manifest)
        if node.nav is not None
    }
    keys |= {dest.key for dest in NAV_DESTINATIONS}
    return keys


def check_drift(db: Session) -> list[str]:
    """Return one human-readable line per thing the manifests declare that ``db`` lacks.

    Empty means in sync. Deliberately one-directional (see the module docstring): a row the database
    has and the manifests do not is an admin's, not drift.
    """
    from src.core.rbac import default_system_roles
    from src.core.rbac_manifest_registry import (
        manifest_resource_keys,
        shipped_named_action_grants,
        shipped_role_grants,
    )

    problems: list[str] = []

    have_roles = {row[0] for row in db.execute(select(RbacRole.name)).all()}
    for name, _description in default_system_roles():
        if name not in have_roles:
            problems.append(f"role missing: {name}")

    have_resources = {row[0] for row in db.execute(select(Resource.key)).all()}
    for key in sorted(manifest_resource_keys() - have_resources):
        problems.append(f"resource missing: {key}")

    have_actions = {row[0] for row in db.execute(select(Action.key)).all()}
    want_actions = {
        action
        for grant in shipped_named_action_grants()
        if grant.action
        for action in (grant.action,)
    }
    for key in sorted(want_actions - have_actions):
        problems.append(f"action missing: {key}")

    have_surfaces = {
        row[0] for row in db.execute(select(NavGateOverride.surface_key)).all()
    }
    for key in sorted(_expected_surface_keys() - have_surfaces):
        problems.append(f"nav-gate default missing: {key}")

    have_grants = {
        (row.role, row.resource)
        for row in db.execute(
            select(RolePermission).where(RolePermission.action.is_(None))
        )
        .scalars()
        .all()
    }
    for grant in shipped_role_grants():
        if (grant.role, grant.resource) not in have_grants:
            problems.append(
                f"grant missing: {grant.role} -> {grant.resource}:{grant.verb}"
            )

    have_named = {
        (row.role, row.resource, row.action)
        for row in db.execute(
            select(RolePermission).where(RolePermission.action.is_not(None))
        )
        .scalars()
        .all()
    }
    for grant in shipped_named_action_grants():
        if (grant.role, grant.resource, grant.action) not in have_named:
            problems.append(
                f"named grant missing: {grant.role} -> {grant.resource}:!{grant.action}"
            )
    return problems


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line: apply (default), ``--dry-run`` or ``--check``."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.db.seed_rbac",
        description="Bring the database to the shipped RBAC catalog (idempotent, insert-only).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change and roll back; write nothing.",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero with a diff when the database is missing anything the manifests declare.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the requested mode; return the process exit code."""
    args = _parse_args(argv)

    if args.check:
        with get_db_context() as db:
            problems = check_drift(db)
        if problems:
            print("rbac seed --check: DRIFT", file=sys.stderr)
            for line in problems:
                print(f"  {line}", file=sys.stderr)
            print(
                "\nRun `python -m scripts.db.seed_rbac` after `alembic upgrade head`.",
                file=sys.stderr,
            )
            return 1
        print("rbac seed --check: in sync")
        return 0

    with get_db_context() as db:
        report: SyncReport = sync_rbac_catalog(db)
        if args.dry_run:
            # ``get_db_context`` commits on a clean exit, so a dry run has to undo its own writes
            # explicitly rather than simply not committing.
            db.rollback()
            print(f"rbac seed --dry-run (nothing written): {report.summary()}")
            return 0
    print(f"rbac seed: {report.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
