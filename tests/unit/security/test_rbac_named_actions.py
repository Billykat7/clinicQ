"""Named non-cumulative actions & `(resource, action)` resolution (Issue #140, M25).

The cumulative ladder (`READ < CREATE < UPDATE < DELETE`) cannot express an action that is not
"more than" another. These tests pin the named-action resolver: a `sign`/`approve` grant is
independent of `delete` (non-cumulative), deny-beats-allow holds, and the opt-in
`applies_to_descendants` cascade reaches descendants (and only when flagged), with a child DENY
overriding a cascaded parent ALLOW. The pure resolver (`named_action_allowed`) is the oracle; the DB
path (`role_has_named_action`) is checked to agree with it over real `role_permission` rows and the
role-inheritance closure.

Essential, isolated RBAC invariants (per `.cursor/rules/testing-strategy.mdc`); the end-to-end route
enforcement is covered by the leases signature integration suite.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Generator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import PermissionAction, PermissionEffect, is_cumulative_action
from src.core.rbac import (
    catalog_resource_closure,
    load_resource_descendants,
    named_action_allowed,
    role_has_named_action,
    seeded_grant_scope,
)
from src.database.models import RbacRole, RoleHierarchy, RolePermission

_SIGN = PermissionAction.SIGN.value


_APPROVE = PermissionAction.APPROVE.value


_LEASE_SIG = "order.signature"


_LEASES = "orders"


def _grant(
    resource: str,
    action: str,
    effect: PermissionEffect = PermissionEffect.ALLOW,
    cascade: bool = False,
) -> tuple[str, str, PermissionEffect, bool]:
    """Build one named grant tuple for the pure resolver."""
    return (resource, action, effect, cascade)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Generator[Session]:
    """A bare session (the resolver falls back to the enum closure when no catalog is seeded)."""
    with session_factory() as session:
        yield session


def _add_named_grant(
    db: Session,
    role: str,
    resource: str,
    action: str,
    *,
    effect: PermissionEffect = PermissionEffect.ALLOW,
    cascade: bool = False,
) -> None:
    """Insert one named-action ``role_permission`` row."""
    db.add(
        RolePermission(
            role=role,
            resource=resource,
            action=action,
            max_verb=None,
            effect=effect.value,
            applies_to_descendants=cascade,
            scope=seeded_grant_scope(role).value,
        )
    )


def test_sign_and_approve_are_not_cumulative_verbs() -> None:
    """``sign``/``approve`` are named actions, not CRUD verbs — the discriminator holds."""
    assert not is_cumulative_action(_SIGN)
    assert not is_cumulative_action(_APPROVE)
    assert is_cumulative_action("delete")


def test_direct_allow_grants_the_action() -> None:
    """A direct ALLOW on ``(lease.signature, sign)`` allows it."""
    grants = [_grant(_LEASE_SIG, _SIGN)]
    assert named_action_allowed(grants, set(), _LEASE_SIG, _SIGN)


def test_no_grant_denies_by_default() -> None:
    """Default-deny: with no matching grant the action is refused."""
    assert not named_action_allowed([], set(), _LEASE_SIG, _SIGN)
    assert not named_action_allowed(
        [_grant(_LEASE_SIG, _APPROVE)], set(), _LEASE_SIG, _SIGN
    )


def test_deny_beats_allow_on_the_same_action() -> None:
    """An ALLOW and a DENY on the same ``(resource, action)`` resolve to denied."""
    grants = [
        _grant(_LEASE_SIG, _SIGN, PermissionEffect.ALLOW),
        _grant(_LEASE_SIG, _SIGN, PermissionEffect.DENY),
    ]
    assert not named_action_allowed(grants, set(), _LEASE_SIG, _SIGN)


def test_unflagged_grant_does_not_cascade() -> None:
    """An unflagged parent grant stays scoped — it does not reach the descendant."""
    closure = catalog_resource_closure()
    grants = [_grant(_LEASES, _SIGN, cascade=False)]
    assert not named_action_allowed(grants, closure, _LEASE_SIG, _SIGN)


def test_child_deny_overrides_cascaded_parent_allow() -> None:
    """A flagged parent ALLOW is overridden by a narrower child DENY (deny-beats-allow)."""
    closure = catalog_resource_closure()
    grants = [
        _grant(_LEASES, _SIGN, PermissionEffect.ALLOW, cascade=True),
        _grant(_LEASE_SIG, _SIGN, PermissionEffect.DENY),
    ]
    assert not named_action_allowed(grants, closure, _LEASE_SIG, _SIGN)


def test_role_with_the_action_is_admitted(db: Session) -> None:
    """A role holding only the ``sign`` grant (no ``delete``) is admitted for ``sign``."""
    _add_named_grant(db, "signer", _LEASE_SIG, _SIGN)
    db.commit()
    assert role_has_named_action(db, ["signer"], _LEASE_SIG, _SIGN)


def test_role_without_the_action_is_refused_even_with_delete(db: Session) -> None:
    """A role with cumulative ``delete`` on ``leases`` but no ``sign`` grant is refused ``sign``.

    Proves non-cumulativity end-to-end at the resolver: ``delete`` (cumulative) does not imply the
    named ``sign`` action, even though ``lease.signature`` is a descendant of ``leases``.
    """
    db.add(
        RolePermission(
            role="deleter",
            resource=_LEASES,
            max_verb="delete",
            effect=PermissionEffect.ALLOW.value,
            scope=seeded_grant_scope("deleter").value,
        )
    )
    db.commit()
    assert not role_has_named_action(db, ["deleter"], _LEASE_SIG, _SIGN)


def test_named_action_inherited_through_role_hierarchy(db: Session) -> None:
    """A role inherits a parent role's named-action grant through the ``role_hierarchy`` closure."""
    db.add_all(
        [
            RbacRole(name="child", is_system=False),
            RbacRole(name="parent", is_system=False),
            RoleHierarchy(role="child", inherits_role="parent"),
        ]
    )
    _add_named_grant(db, "parent", _LEASE_SIG, _SIGN)
    db.commit()
    assert role_has_named_action(db, ["child"], _LEASE_SIG, _SIGN)


def test_load_resource_descendants_falls_back_to_the_declared_closure(
    db: Session,
) -> None:
    """With no ``resource_descendants`` rows, the loader returns the manifest-derived closure."""
    assert load_resource_descendants(db) == catalog_resource_closure()
