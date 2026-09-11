"""Portal role & permission matrix tests (Issue #58 / M9).

Pins the four portal roles and their permission matrix — the single source of truth in
:data:`src.core.rbac.PORTAL_ROLE_MATRIX` that migration ``0026`` seeds and
``docs/architecture/rbac-matrix.md`` documents — so the seed, the code and the doc can never
drift. Each role's *effective* verb (after parent->child inheritance) is asserted resource by
resource against a database seeded exactly as ``alembic upgrade head`` leaves it.

Since Issue #154 (M27) two things changed shape here, though no expectation did. Resources are
plain string keys and the tree they inherit through comes from the registered module manifests
(``permission_resource_ancestors`` is re-derived locally as :func:`_ancestors`, since the enum that
answered it is deleted). And ``matrix_resources()`` now covers every manifest-declared resource,
not the 38 the enum happened to hold — so the expectations merge ``PORTAL_ROLE_MATRIX`` with each
module's own grant seed (:func:`~src.core.rbac.default_reporting_role_permissions` today), which is
what a deployed database actually holds.

These are essential, isolated RBAC rules (per ``.cursor/rules/testing-strategy.mdc`` unit tests
are reserved for exactly this); allow/deny over real HTTP is covered by the admin and
``/auth/me`` integration suites.
"""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import AssignmentScopeType, GrantScope, PermissionVerb, UserRole
from src.core import rbac_matrix
from src.core.nav_visibility import nav_visibility_for_role
from src.core.rbac import ensure_permission_key, ensure_roles_hold_permission
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.core.scope import resolve_scope_tier
from src.database.models import User, UserRoleAssignment

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.


def test_the_surface_matrix_covers_every_shipped_surface() -> None:
    """All 38, so a surface added later cannot be quietly missing from the document."""
    from scripts.generate_surface_matrix import render_block
    from src.core.nav_registry import NAV_DESTINATIONS
    from src.core.rbac_manifest import iter_resources
    from src.core.rbac_manifest_registry import ALL_MANIFESTS

    block = render_block()
    for dest in NAV_DESTINATIONS:
        assert f"| `{dest.key}` |" in block, (
            f"{dest.key} missing from the surface matrix"
        )
    for manifest in ALL_MANIFESTS:
        for node in iter_resources(manifest):
            if node.nav is None:
                continue
            assert f"| `{node.full_key}` |" in block, (
                f"{node.full_key} missing from the surface matrix"
            )


# ── Issue 18: ClinicQ's five roles ────────────────────────────────────────────────────────
#
# Each test seeds an in-memory database through ``sync_rbac_catalog``, the entrypoint
# ``make seed-rbac`` and the deploy sequence run, so what is asserted is what a deployment decides.

_REPO = Path(__file__).resolve().parents[1]
_SITE = "0199b0c0-0000-7000-8000-00000000000a"


@pytest.fixture
def seeded(session_factory: sessionmaker[Session]) -> Generator[Session]:
    """A database seeded exactly as ``make seed-rbac`` seeds one."""
    with session_factory() as db:
        sync_rbac_catalog(db)
        db.commit()
        yield db


def _staff(db: Session, role: UserRole) -> dict[str, str]:
    """A staff member holding ``role`` at one site; returns the token claims a request would carry."""
    user = User(
        email=f"{role.value}@clinicq.example", role=role.value, is_verified=True
    )
    db.add(user)
    db.flush()
    db.add(
        UserRoleAssignment(
            user_id=user.id,
            role=role.value,
            scope_type=AssignmentScopeType.SITE.value,
            scope_id=_SITE,
        )
    )
    db.add(UserRoleAssignment(user_id=user.id, role=role.value))
    db.commit()
    return {"sub": user.email, "email": user.email, "uid": user.id}


def _allowed(
    db: Session, claims: dict[str, str], resource: str, verb: PermissionVerb
) -> bool:
    """Whether the RBAC gate every protected route uses lets this caller through."""
    try:
        ensure_permission_key(db, claims, resource, verb.value)
    except HTTPException as refused:
        assert refused.status_code == 403
        return False
    return True


def test_a_receptionist_cannot_change_the_display_mode_or_the_site_settings(
    seeded: Session,
) -> None:
    """The gate refuses the receptionist; the clinic manager passes; reading the mode is allowed."""
    receptionist = _staff(seeded, UserRole.RECEPTIONIST)
    manager = _staff(seeded, UserRole.CLINIC_MANAGER)
    assert not _allowed(seeded, receptionist, "sites.display", PermissionVerb.UPDATE)
    assert not _allowed(seeded, receptionist, "sites.settings", PermissionVerb.UPDATE)
    assert not _allowed(seeded, receptionist, "sites.settings", PermissionVerb.READ)
    assert _allowed(seeded, receptionist, "sites.display", PermissionVerb.READ)
    assert _allowed(seeded, manager, "sites.display", PermissionVerb.UPDATE)
    assert _allowed(seeded, manager, "sites.settings", PermissionVerb.UPDATE)


def test_the_ui_helper_hides_what_the_gate_refuses(seeded: Session) -> None:
    """``can()`` in a template gives the same answer as the route's gate (defence in depth)."""
    receptionist = nav_visibility_for_role(seeded, UserRole.RECEPTIONIST.value)
    manager = nav_visibility_for_role(seeded, UserRole.CLINIC_MANAGER.value)
    assert not receptionist.can("sites.display", "update")
    assert not receptionist.can("sites.settings", "update")
    assert receptionist.can("sites.display", "read")
    assert manager.can("sites.display", "update")


def test_a_nurse_holds_call_next_only_for_the_queues_assigned_to_them(
    seeded: Session,
) -> None:
    """The grant half of "a nurse cannot call next on a queue they are not assigned to".

    Receptionist and nurse both hold ``update`` on ``queues.call``; the difference is the grant's
    tier: ``assigned`` (every queue at their sites) for the receptionist, ``own`` (only the queues
    assigned to them) for the nurse. Issue 19's guard turns the tier into rows and is where the
    unassigned queue answers 404.
    """
    nurse = _staff(seeded, UserRole.NURSE_DOCTOR)
    receptionist = _staff(seeded, UserRole.RECEPTIONIST)
    assert _allowed(seeded, nurse, "queues.call", PermissionVerb.UPDATE)
    assert _allowed(seeded, receptionist, "queues.call", PermissionVerb.UPDATE)
    nurse_user = seeded.get(User, nurse["uid"])
    receptionist_user = seeded.get(User, receptionist["uid"])
    assert resolve_scope_tier(seeded, nurse_user, "queues.call") is GrantScope.OWN
    assert (
        resolve_scope_tier(seeded, receptionist_user, "queues.call")
        is GrantScope.ASSIGNED
    )
    assert not GrantScope.OWN.satisfies(GrantScope.ASSIGNED)


def test_the_patient_role_reaches_its_own_record_and_nothing_else(
    seeded: Session,
) -> None:
    """The one grant ``require_patient`` checks, and not a verb anywhere else."""
    ensure_roles_hold_permission(
        seeded, [UserRole.PATIENT.value], "patients.self", "update"
    )
    for resource in ("patients", "sites", "queues", "queues.call", "dashboard", "logs"):
        with pytest.raises(HTTPException):
            ensure_roles_hold_permission(
                seeded, [UserRole.PATIENT.value], resource, "read"
            )


def test_the_committed_rbac_matrix_is_what_the_seeded_database_decides() -> None:
    """``docs/architecture/rbac-matrix.md`` against a fresh seed: regenerate with ``make rbac-matrix``."""
    committed = rbac_matrix.committed_block(_REPO / rbac_matrix.MATRIX_DOC)
    assert committed, "the matrix block is missing: run `make rbac-matrix`"
    assert committed == rbac_matrix.build_block(), (
        "docs/architecture/rbac-matrix.md is stale: run `make rbac-matrix` and review the diff"
    )


def test_no_retired_portal_role_survives_anywhere() -> None:
    """``tenant``, ``owner``, ``manager`` and ``vendor`` are gone: not a member, a grant or a line."""
    retired = {"tenant", "owner", "manager", "vendor"}
    assert not retired & {role.value for role in UserRole}
    snapshot = (_REPO / "tests" / "snapshots" / "rbac_decisions.txt").read_text()
    assert not [
        line
        for line in snapshot.splitlines()
        if any(f"role={r} " in line for r in retired)
    ]
    matrix = rbac_matrix.committed_block(_REPO / rbac_matrix.MATRIX_DOC)
    assert not [r for r in retired if f"`{r}`" in matrix]
