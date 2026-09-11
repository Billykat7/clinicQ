"""Unit tests for the verb-based RBAC core (Issue #5).

Cover the pure permission logic in :mod:`src.core.rbac` — verb ordering, the cumulative
"granted verb implies lower verbs" rule, parent->child inheritance in the resource tree,
and the ``ensure_permission_key`` gate (no-op when auth is off, 401 unauthenticated, 403
under-privileged). Also pin the seed helpers the integration suite builds from, so seed data
and enforcement can never drift apart.

Resources are plain string keys throughout (Issue #154 deleted the ``PermissionResource`` enum
these tests were originally written against); the resource *tree* they inherit through comes from
the registered module manifests, via :func:`~src.core.rbac.load_resource_parent_map`.

These are essential, isolated rules (per ``.cursor/rules/testing-strategy.mdc`` unit tests
are reserved for exactly this); the allow/deny behaviour over real HTTP is exercised by the
admin and ``/auth/me`` integration tests.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette import status

from src.commons.enums import GrantScope, PermissionEffect, PermissionVerb, UserRole
from src.core import rbac
from src.core.rbac import (
    _cache_effective_grant_keys_for_roles as cache_grants_for_roles,
)
from src.core.rbac import _effective_cache_populated as effective_cache_populated
from src.core.rbac import _walk_effective_grant_keys_for_roles as walk_grants_for_roles
from src.core.rbac import (
    active_roles_for_user,
    default_role_permissions,
    default_system_roles,
    effective_verb_over_keys,
    ensure_permission_key,
    get_effective_verb_for_role,
    get_max_verb_for_role,
    granted_covers_required,
    permission_verb_rank,
    raise_forbidden,
    refresh_effective_role_permissions_py,
    resource_permissions_for_role,
    role_closure,
    role_inherits_cycle,
    seeded_grant_scope,
)
from src.core.rbac_manifest_registry import manifest_parent_map, manifest_resource_keys
from src.core.scope import resolve_scope_tier
from src.database.models import (
    EffectiveRolePermission,
    RbacRole,
    RoleHierarchy,
    RolePermission,
    User,
    UserRoleAssignment,
)

_ADMIN = UserRole.ADMIN.value


_USER = UserRole.USER.value


_ADMIN_EMAIL = "admin@example.com"


_MEMBER_EMAIL = "member@example.com"


def _grant(db: Session, role: str, resource: str, verb: PermissionVerb) -> None:
    """Insert one ``(role, resource, max_verb)`` grant."""
    db.add(
        RolePermission(
            role=role,
            resource=resource,
            max_verb=verb.value,
            created_at=datetime.now(UTC),
            scope=seeded_grant_scope(role).value,
        )
    )


def _add_user(db: Session, email: str, role: str) -> None:
    """Insert a user row (RBAC resolves the role from this table by email)."""
    db.add(User(email=email, role=role, is_verified=True))


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Generator[Session]:
    """A single session seeded with the default system roles + permissions.

    Mirrors what migration ``0004`` installs so the fixtures exercise real seed data.
    """
    with session_factory() as session:
        for role, resource, verb in default_role_permissions():
            _grant(session, role, resource, verb)
        _add_user(session, _ADMIN_EMAIL, _ADMIN)
        _add_user(session, _MEMBER_EMAIL, _USER)
        session.commit()
        yield session


def test_verb_rank_is_strictly_increasing() -> None:
    """``READ < CREATE < UPDATE < DELETE`` by rank."""
    ranks = [
        permission_verb_rank(v)
        for v in (
            PermissionVerb.READ,
            PermissionVerb.CREATE,
            PermissionVerb.UPDATE,
            PermissionVerb.DELETE,
        )
    ]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)


@pytest.mark.parametrize(
    ("granted", "required", "covered"),
    [
        (PermissionVerb.DELETE, PermissionVerb.READ, True),
        (PermissionVerb.DELETE, PermissionVerb.DELETE, True),
        (PermissionVerb.UPDATE, PermissionVerb.CREATE, True),
        (PermissionVerb.READ, PermissionVerb.READ, True),
        (PermissionVerb.READ, PermissionVerb.CREATE, False),
        (PermissionVerb.CREATE, PermissionVerb.DELETE, False),
        (None, PermissionVerb.READ, False),
    ],
)
def test_granted_covers_required_is_cumulative(
    granted: PermissionVerb | None, required: PermissionVerb, covered: bool
) -> None:
    """A higher granted verb satisfies every lower required verb; ``None`` satisfies none."""
    assert granted_covers_required(granted, required) is covered


def test_get_max_verb_reads_the_explicit_grant(db: Session) -> None:
    """The direct ``(role, resource)`` row is returned verbatim."""
    assert get_max_verb_for_role(db, _ADMIN, "users") == (PermissionVerb.DELETE)
    # No explicit row for user->users.
    assert get_max_verb_for_role(db, _USER, "users") is None


def test_parent_grant_cascades_to_children(db: Session) -> None:
    """A grant on the ``reports`` hub is inherited by ``users`` and ``logs`` children."""
    _grant(db, _USER, "reports", PermissionVerb.UPDATE)
    db.commit()

    # No explicit child rows, yet the parent grant resolves for each child.
    assert get_max_verb_for_role(db, _USER, "logs") is None
    assert get_effective_verb_for_role(db, _USER, "logs") == (PermissionVerb.UPDATE)
    assert get_effective_verb_for_role(db, _USER, "users") == (PermissionVerb.UPDATE)


def test_child_grant_wins_when_higher_than_parent(db: Session) -> None:
    """The highest verb across a resource and its ancestors wins."""
    _grant(db, _USER, "reports", PermissionVerb.READ)
    _grant(db, _USER, "users", PermissionVerb.DELETE)
    db.commit()

    assert get_effective_verb_for_role(db, _USER, "users") == (PermissionVerb.DELETE)
    # The sibling with only the parent grant still resolves to the parent's verb.
    assert get_effective_verb_for_role(db, _USER, "logs") == (PermissionVerb.READ)


def test_no_grant_anywhere_resolves_to_none(db: Session) -> None:
    """A role with no grant on a resource or its ancestors has no effective verb."""
    assert get_effective_verb_for_role(db, _USER, "rbac") is None


def test_resource_permissions_snapshot_for_seeded_roles(db: Session) -> None:
    """The per-resource map exposed on ``/auth/me`` matches the seeded grants."""
    admin_perms = resource_permissions_for_role(db, _ADMIN)
    assert admin_perms == {
        "dashboard": PermissionVerb.DELETE.value,
        "users": PermissionVerb.DELETE.value,
        "logs": PermissionVerb.DELETE.value,
        "rbac": PermissionVerb.DELETE.value,
    }
    # A standard user only reaches the dashboard; resources with no grant are omitted.
    assert resource_permissions_for_role(db, _USER) == {
        "dashboard": PermissionVerb.READ.value
    }


def test_resource_permissions_include_inherited_children(db: Session) -> None:
    """Inherited child resources appear in the snapshot with the parent's verb."""
    _grant(db, _USER, "reports", PermissionVerb.READ)
    db.commit()

    perms = resource_permissions_for_role(db, _USER)
    assert perms["reports"] == PermissionVerb.READ.value
    assert perms["users"] == PermissionVerb.READ.value
    assert perms["logs"] == PermissionVerb.READ.value


@pytest.fixture
def auth_on(monkeypatch: pytest.MonkeyPatch) -> Callable[[bool], None]:
    """Return a switch that forces ``settings.auth_enabled`` on or off for RBAC."""

    def _set(enabled: bool) -> None:
        monkeypatch.setattr(
            rbac, "get_settings", lambda: type("S", (), {"auth_enabled": enabled})()
        )

    return _set


def test_ensure_permission_noops_when_auth_disabled(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """With auth disabled the gate returns without touching the database or raising."""
    auth_on(False)
    # An anonymous principal would otherwise 401; disabled auth short-circuits first.
    ensure_permission_key(db, {"sub": "anonymous"}, "users", PermissionVerb.DELETE)


def test_ensure_permission_allows_sufficient_grant(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """An admin holding DELETE on ``users`` passes a READ check (cumulative)."""
    auth_on(True)
    ensure_permission_key(
        db,
        {"email": _ADMIN_EMAIL},
        "users",
        PermissionVerb.READ,
    )


def test_ensure_permission_forbids_under_privileged(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """A standard user lacking CREATE on ``users`` is rejected with 403."""
    auth_on(True)
    with pytest.raises(HTTPException) as exc:
        ensure_permission_key(
            db,
            {"email": _MEMBER_EMAIL},
            "users",
            PermissionVerb.CREATE,
        )
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["code"] == rbac.RBAC_FORBIDDEN_CODE


def test_ensure_permission_401_for_anonymous(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """An anonymous principal is unauthenticated (401), not merely forbidden."""
    auth_on(True)
    with pytest.raises(HTTPException) as exc:
        ensure_permission_key(db, {"sub": "anonymous"}, "users", PermissionVerb.READ)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_ensure_permission_401_for_unknown_user(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """A token whose email has no user row is treated as unauthenticated (401)."""
    auth_on(True)
    with pytest.raises(HTTPException) as exc:
        ensure_permission_key(
            db,
            {"email": "ghost@example.com"},
            "users",
            PermissionVerb.READ,
        )
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_the_seeded_roles_are_the_kernels_two_and_clinicqs_five() -> None:
    """``user`` and ``admin``, and ClinicQ's five (Issue 18): no portal role survives."""
    names = {name for name, _ in default_system_roles()}
    assert names == {role.value for role in UserRole}
    assert names == {
        _USER,
        _ADMIN,
        "patient",
        "receptionist",
        "nurse_doctor",
        "clinic_manager",
        "platform_admin",
    }


def test_default_role_permissions_grant_admin_delete_everywhere() -> None:
    """Admin holds DELETE on users/logs/rbac/dashboard; user holds READ on dashboard."""
    grants = {(role, res, verb) for role, res, verb in default_role_permissions()}
    assert (_ADMIN, "users", PermissionVerb.DELETE) in grants
    assert (_ADMIN, "logs", PermissionVerb.DELETE) in grants
    assert (_ADMIN, "rbac", PermissionVerb.DELETE) in grants
    assert (_ADMIN, "dashboard", PermissionVerb.DELETE) in grants
    assert (_USER, "dashboard", PermissionVerb.READ) in grants


def test_raise_forbidden_carries_structured_detail() -> None:
    """The 403 detail names the resource and required verb for the client."""
    with pytest.raises(HTTPException) as exc:
        raise_forbidden("rbac", PermissionVerb.DELETE)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["resource"] == "rbac"
    assert exc.value.detail["required_verb"] == PermissionVerb.DELETE.value


_ALLOW = PermissionEffect.ALLOW


_DENY = PermissionEffect.DENY


_ROOT = "orders"


_CHILD = "order.details"


def _grants(
    *rows: tuple[str, PermissionVerb, PermissionEffect],
) -> list[tuple[str, str, str]]:
    """Build a flat keyed grant list for the pure resolver.

    ``PermissionVerb``/``PermissionEffect`` are ``StrEnum``s, so their members are already the
    string values :func:`effective_verb_over_keys` compares against.
    """
    return [(resource, verb.value, effect.value) for resource, verb, effect in rows]


def _resolve(grants: list[tuple[str, str, str]], resource_key: str) -> str | None:
    """Resolve ``resource_key`` over ``grants`` using the manifest-declared resource tree.

    The pure resolver needs a parent map; before Issue #154 it walked the enum's own
    ``PERMISSION_RESOURCE_PARENT`` internally. The manifests are that map's successor.
    """
    return effective_verb_over_keys(grants, manifest_parent_map(), resource_key)


def _grant_effect(
    db: Session,
    role: str,
    resource: str,
    verb: PermissionVerb,
    effect: PermissionEffect,
) -> None:
    """Insert one grant row carrying an explicit ALLOW/DENY effect."""
    db.add(
        RolePermission(
            role=role,
            resource=resource,
            max_verb=verb.value,
            effect=effect.value,
            created_at=datetime.now(UTC),
            scope=seeded_grant_scope(role).value,
        )
    )


def test_deny_at_read_removes_access_entirely() -> None:
    """A DENY at READ (below every ALLOW on the chain) resolves to no access at all."""
    grants = _grants(
        (_ROOT, PermissionVerb.DELETE, _ALLOW),
        (_CHILD, PermissionVerb.READ, _DENY),
    )
    assert _resolve(grants, _CHILD) is None


def test_lone_deny_grants_nothing() -> None:
    """A DENY with no ALLOW anywhere on the chain grants nothing (deny subtracts)."""
    grants = _grants((_CHILD, PermissionVerb.UPDATE, _DENY))
    assert _resolve(grants, _CHILD) is None


def _add_role(db: Session, name: str) -> None:
    """Insert a custom (non-system) role definition."""
    db.add(RbacRole(name=name, description=None, is_system=False))


def _inherit(db: Session, role: str, inherits_role: str) -> None:
    """Insert a ``role -> inherits_role`` inheritance edge."""
    db.add(RoleHierarchy(role=role, inherits_role=inherits_role))


def test_role_closure_is_itself_when_no_edges(db: Session) -> None:
    """With no inheritance edges a role's closure is just itself (flat roles unchanged)."""
    assert role_closure(db, _ADMIN) == {_ADMIN}


def test_single_parent_inheritance_unions_grants(db: Session) -> None:
    """A child inherits its parent's grants (union over the closure)."""
    _add_role(db, "base")
    _add_role(db, "child")
    _grant(db, "base", "orders", PermissionVerb.UPDATE)
    _inherit(db, "child", "base")
    db.commit()
    assert role_closure(db, "child") == {"child", "base"}
    assert get_effective_verb_for_role(db, "child", "orders") == PermissionVerb.UPDATE


def test_multi_parent_dag_unions_both(db: Session) -> None:
    """A role inheriting two parents gains grants from both (multi-parent DAG)."""
    _add_role(db, "a")
    _add_role(db, "b")
    _add_role(db, "child")
    _grant(db, "a", "orders", PermissionVerb.UPDATE)
    _grant(db, "b", "invoices", PermissionVerb.READ)
    _inherit(db, "child", "a")
    _inherit(db, "child", "b")
    db.commit()
    assert get_effective_verb_for_role(db, "child", "orders") == PermissionVerb.UPDATE
    assert get_effective_verb_for_role(db, "child", "invoices") == PermissionVerb.READ


def test_diamond_closure_resolves_once(db: Session) -> None:
    """A diamond (child->left,right; left,right->top) resolves top's grant, no infinite work."""
    _add_role(db, "top")
    _add_role(db, "left")
    _add_role(db, "right")
    _add_role(db, "child")
    _grant(db, "top", "crates", PermissionVerb.DELETE)
    _inherit(db, "left", "top")
    _inherit(db, "right", "top")
    _inherit(db, "child", "left")
    _inherit(db, "child", "right")
    db.commit()
    assert role_closure(db, "child") == {"child", "left", "right", "top"}
    assert get_effective_verb_for_role(db, "child", "crates") == PermissionVerb.DELETE


def test_deny_anywhere_in_closure_wins(db: Session) -> None:
    """A DENY inherited from any role in the closure beats an ALLOW from another."""
    _add_role(db, "broad")
    _add_role(db, "carveout")
    _add_role(db, "child")
    _grant(db, "broad", "orders", PermissionVerb.DELETE)
    _grant_effect(db, "carveout", "orders", PermissionVerb.UPDATE, _DENY)
    _inherit(db, "child", "broad")
    _inherit(db, "child", "carveout")
    db.commit()
    # ALLOW DELETE from ``broad`` capped below DENY UPDATE from ``carveout`` → CREATE.
    assert get_effective_verb_for_role(db, "child", "orders") == PermissionVerb.CREATE


def test_closure_walk_terminates_on_a_cycle(db: Session) -> None:
    """A malformed cycle (a<->b) terminates rather than looping, returning both roles."""
    _add_role(db, "a")
    _add_role(db, "b")
    _inherit(db, "a", "b")
    _inherit(db, "b", "a")
    db.commit()
    assert role_closure(db, "a") == {"a", "b"}


def test_role_inherits_cycle_detects_a_loop(db: Session) -> None:
    """``role_inherits_cycle`` flags a self-edge and an edge that would close a loop."""
    _add_role(db, "a")
    _add_role(db, "b")
    _inherit(db, "a", "b")
    db.commit()
    assert role_inherits_cycle(db, "a", "a") is True  # self-edge
    assert role_inherits_cycle(db, "b", "a") is True  # b->a would close a->b->a
    assert (
        role_inherits_cycle(db, "a", "b") is False
    )  # already present, not a new cycle


def test_inheriting_a_management_role_carries_its_tier_too(db: Session) -> None:
    """Inheritance moves *grants*, tiers included — there is no name-based wall left to hold.

    Issue #103's portal boundary was ``is_management_role``, decided by the role's name, so no
    inheritance edge could turn a tenant/owner/vendor into a management-capable role. Issues
    #156–#172 replaced that with the tier on the grant and deleted the function, which changes what
    this guards: an owner that inherits a ``business``-tier role now *does* reach the business,
    because an admin wrote that edge. What must not happen is breadth appearing from anywhere other
    than a grant — so the un-inherited case stays narrow.
    """
    _add_role(db, "mgmt")
    db.add(
        RolePermission(
            role="mgmt",
            resource="orders",
            max_verb=PermissionVerb.READ.value,
            scope=GrantScope.BUSINESS.value,
        )
    )
    db.commit()
    owner = _make_user(
        db, "owner.inherit@example.com", role=UserRole.NURSE_DOCTOR.value
    )
    assert resolve_scope_tier(db, owner, "orders") is GrantScope.OWN

    _inherit(db, UserRole.NURSE_DOCTOR.value, "mgmt")
    db.commit()
    assert resolve_scope_tier(db, owner, "orders") is GrantScope.BUSINESS


def _make_user(db: Session, email: str, role: str = _USER) -> User:
    """Insert a verified user and return the flushed row (its id is populated)."""
    user = User(email=email, role=role, is_verified=True)
    db.add(user)
    db.flush()
    return user


def _assign(
    db: Session,
    user: User,
    role: str,
    *,
    scope_type: str | None = None,
    scope_id: str | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Insert a ``user_roles`` assignment row for ``user``."""
    db.add(
        UserRoleAssignment(
            user_id=str(user.id),
            role=role,
            scope_type=scope_type,
            scope_id=scope_id,
            expires_at=expires_at,
        )
    )


def test_unscoped_assignments_union(db: Session) -> None:
    """A user's active unscoped assignments union across roles."""
    user = _make_user(db, "multi@example.com")
    _assign(db, user, "a")
    _assign(db, user, "b")
    db.commit()
    assert active_roles_for_user(db, user) == {"a", "b"}


def test_no_assignment_rows_falls_back_to_user_role(db: Session) -> None:
    """A user with no ``user_roles`` rows resolves to the ``User.role`` mirror (compat)."""
    user = _make_user(db, "compat@example.com", role=_ADMIN)
    db.commit()
    assert active_roles_for_user(db, user) == {_ADMIN}


def test_expired_assignment_grants_nothing(db: Session) -> None:
    """An assignment past its ``expires_at`` is inactive; the user resolves to no roles."""
    user = _make_user(db, "expired@example.com")
    _assign(
        db,
        user,
        "a",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.commit()
    # A row exists (so no mirror fallback), but it is expired → empty set.
    assert active_roles_for_user(db, user) == set()


def test_future_expiry_is_active(db: Session) -> None:
    """An assignment expiring in the future is still active."""
    user = _make_user(db, "future@example.com")
    _assign(db, user, "a", expires_at=datetime.now(UTC) + timedelta(days=1))
    db.commit()
    assert active_roles_for_user(db, user) == {"a"}


def test_scoped_assignment_applies_only_within_scope(db: Session) -> None:
    """A scoped assignment applies only when the check presents a matching scope."""
    user = _make_user(db, "scoped@example.com")
    _assign(db, user, "a", scope_type="property", scope_id="P1")
    db.commit()
    # No scope on the check → the scoped role does not apply.
    assert active_roles_for_user(db, user) == set()
    # Matching scope → it applies.
    assert active_roles_for_user(db, user, scope_type="property", scope_id="P1") == {
        "a"
    }
    # Different instance of the same type → it does not apply.
    assert (
        active_roles_for_user(db, user, scope_type="property", scope_id="P2") == set()
    )


def test_ensure_permission_honours_scope_and_expiry(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """The gate admits a scoped role only within scope, and never an expired one."""
    auth_on(True)
    _add_role(db, "scoped_editor")
    _grant(db, "scoped_editor", "users", PermissionVerb.UPDATE)
    user = _make_user(db, "gate@example.com")
    _assign(db, user, "scoped_editor", scope_type="property", scope_id="P1")
    db.commit()
    principal = {"email": "gate@example.com"}
    # In scope → allowed.
    ensure_permission_key(
        db,
        principal,
        "users",
        PermissionVerb.READ,
        scope_type="property",
        scope_id="P1",
    )
    # No scope presented → denied (the scoped role does not apply globally).
    with pytest.raises(HTTPException) as exc:
        ensure_permission_key(db, principal, "users", PermissionVerb.READ)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


def _seed_cache_fixture(db: Session) -> None:
    """Seed roles + a DAG with a DENY carve-out for the cache/oracle parity tests."""
    for name in ("base", "mgr", "carve", "child"):
        _add_role(db, name)
    _grant(db, "base", "users", PermissionVerb.READ)
    _grant(db, "mgr", "orders", PermissionVerb.DELETE)
    _grant_effect(db, "carve", "orders", PermissionVerb.UPDATE, _DENY)
    _inherit(db, "child", "mgr")
    _inherit(db, "child", "carve")
    _inherit(db, "child", "base")
    db.commit()


def test_cache_is_empty_until_refreshed(db: Session) -> None:
    """The cache starts empty (SQLite has no triggers), so callers fall back to the walk."""
    _seed_cache_fixture(db)
    assert effective_cache_populated(db) is False


def test_refresh_py_populates_and_matches_oracle_for_every_role(db: Session) -> None:
    """After the rebuild, the cache agrees with the Python oracle for every seeded role."""
    _seed_cache_fixture(db)
    refresh_effective_role_permissions_py(db)
    db.commit()
    assert effective_cache_populated(db) is True

    roles = [r for (r,) in db.execute(select(RbacRole.name)).all()]
    roles += ["base", "mgr", "carve", "child"]
    for role in set(roles):
        cache_grants = cache_grants_for_roles(db, [role])
        walk_grants = walk_grants_for_roles(db, [role])
        for res in manifest_resource_keys():
            assert _resolve(cache_grants, res) == _resolve(walk_grants, res), (
                role,
                res,
            )


def test_cache_resolves_deny_wins_over_the_closure(db: Session) -> None:
    """``child`` (ALLOW DELETE from mgr, DENY UPDATE from carve) resolves LEASES to CREATE."""
    _seed_cache_fixture(db)
    refresh_effective_role_permissions_py(db)
    db.commit()
    cache_grants = cache_grants_for_roles(db, ["child"])
    assert _resolve(cache_grants, "orders") == PermissionVerb.CREATE


def test_request_path_reads_cache_when_populated(db: Session) -> None:
    """With the cache populated, resolution reads it rather than re-walking the graph.

    Proven by staleness: a new grant added *without* a rebuild is not reflected until the cache
    is refreshed — demonstrating the cache is authoritative on the request path when present.
    """
    _seed_cache_fixture(db)
    refresh_effective_role_permissions_py(db)
    db.commit()
    # Add a fresh grant to ``base`` but do NOT rebuild the cache.
    _grant(db, "base", "suppliers", PermissionVerb.DELETE)
    db.commit()
    # The stale cache still resolves vendors to nothing for ``child``.
    assert get_effective_verb_for_role(db, "child", "suppliers") is None
    # After a rebuild the new grant is visible.
    refresh_effective_role_permissions_py(db)
    db.commit()
    assert (
        get_effective_verb_for_role(db, "child", "suppliers") == PermissionVerb.DELETE
    )


def test_refresh_is_idempotent(db: Session) -> None:
    """Rebuilding twice yields the same rows (teardown/rebuild is safe to re-run)."""
    _seed_cache_fixture(db)
    refresh_effective_role_permissions_py(db)
    db.commit()
    first = db.execute(
        select(func.count()).select_from(EffectiveRolePermission)
    ).scalar_one()
    refresh_effective_role_permissions_py(db)
    db.commit()
    second = db.execute(
        select(func.count()).select_from(EffectiveRolePermission)
    ).scalar_one()
    assert first == second and first > 0
