"""Integration tests for the admin RBAC roles/permissions API (Issue 6 / M1-06).

Exercises the management surface that lets admins define roles, set their per-resource
permission matrix, and assign roles to users, at the HTTP layer against an in-memory
database:

* ``/admin/rbac/roles`` — list/search, create a custom (non-system) role, read, patch
  its description, and delete it; system roles (``user``, ``admin``) are protected from
  deletion and a role still assigned to users cannot be deleted;
* ``/admin/rbac/roles/{name}/permissions`` — read the full matrix (one row per resource)
  and replace it (upsert; ``null`` revokes);
* ``/admin/rbac/users/{id}/roles`` — read and change a user's role; the change is
  reflected in the effective ``permissions`` map on their next ``GET /auth/me``.

Every endpoint is gated by verb-based RBAC on the ``rbac`` resource (READ for reads,
CREATE/UPDATE for writes, DELETE for role deletion): unauthenticated -> 401,
under-privileged -> 403.

Per ``.cursor/rules/testing-strategy.mdc`` these assert JSON, status codes and DB state
— never HTML — and build isolated ``Settings`` (``_env_file=None``) so a developer's
local ``.env`` cannot change outcomes. ``AUTH_ENABLED`` is on so RBAC is enforced; the
seeded ``admin`` role holds DELETE on ``rbac`` (see
:func:`src.core.rbac.default_role_permissions`).
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, GrantScope, PermissionVerb, UserRole
from src.core import security
from src.core.config import Settings, get_settings
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    seeded_grant_scope,
)
from src.core.security import create_access_token
from src.database.models import Base, PermissionAuditLog, RbacRole, RolePermission, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "admin-rbac-roles-test-secret-min-32-characters"


_ADMIN_EMAIL = "admin.roles@example.com"


_MEMBER_EMAIL = "member.roles@example.com"


_CUSTOM_ROLE = "editor"


def _settings(**overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with RBAC enforcement on."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_enabled": True,
        "auth_password_login_enabled": True,
        "smtp_host": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _seed_rbac(factory: sessionmaker[Session]) -> None:
    """Seed the system roles and default grants, mirroring the Alembic migration.

    This gives the ``admin`` role DELETE on ``rbac`` (which covers every lower verb),
    and the standard ``user`` role only READ on ``dashboard``.
    """
    with factory() as db:
        for name, description in default_system_roles():
            db.add(RbacRole(name=name, description=description, is_system=True))
        for role, resource, verb in default_role_permissions():
            db.add(
                RolePermission(
                    role=role,
                    resource=resource,
                    max_verb=verb.value,
                    created_at=datetime.now(UTC),
                    scope=seeded_grant_scope(role).value,
                )
            )
        db.commit()


@pytest.fixture
def make_admin_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` wired to an isolated DB, seeded RBAC and ``Settings``.

    Returns a namespace with ``client``, the ``settings`` in force, and a ``session``
    factory for asserting DB state. The route and the security core (token
    minting/decoding) share one ``Settings`` so the JWT secret matches everywhere.
    """
    apps: list[tuple[object, object]] = []

    def _make(**settings_overrides: object) -> SimpleNamespace:
        settings = _settings(**settings_overrides)
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        ).execution_options(schema_translate_map=sqlite_schema_translate_map())
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        _seed_rbac(factory)

        def _override_get_db() -> Generator[Session]:
            db = factory()
            try:
                yield db
            finally:
                db.close()

        # Token minting/decoding read the module-level ``get_settings`` — point it at the
        # same isolated instance the route dependency resolves so the JWT secret matches.
        monkeypatch.setattr(security, "get_settings", lambda: settings)

        app = create_app()
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_settings] = lambda: settings
        client = TestClient(app)
        apps.append((app, engine))
        return SimpleNamespace(client=client, settings=settings, session=factory)

    yield _make

    for app, engine in apps:
        app.dependency_overrides.clear()  # type: ignore[attr-defined]
        Base.metadata.drop_all(engine)  # type: ignore[arg-type]
        engine.dispose()  # type: ignore[attr-defined]


def _add_user(
    factory: sessionmaker[Session],
    *,
    email: str,
    role: str = UserRole.USER.value,
) -> User:
    """Insert a verified user and return a detached row (its field values are enough)."""
    with factory() as db:
        user = User(email=email, role=role, is_verified=True)
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def _add_role(factory: sessionmaker[Session], name: str) -> None:
    """Insert a custom (non-system) role directly into the test database."""
    with factory() as db:
        db.add(RbacRole(name=name, description="Seeded custom role", is_system=False))
        db.commit()


def _get_role(factory: sessionmaker[Session], name: str) -> RbacRole | None:
    """Read a role by name from the test database."""
    with factory() as db:
        return db.execute(
            select(RbacRole).where(RbacRole.name == name)
        ).scalar_one_or_none()


def _bearer(email: str) -> dict[str, str]:
    """Authorization header carrying a freshly minted access JWT for ``email``."""
    return {"Authorization": f"Bearer {create_access_token(sub=email, email=email)}"}


def _admin(ctx: SimpleNamespace) -> User:
    """Seed an admin user for ``ctx`` and return it."""
    return _add_user(ctx.session, email=_ADMIN_EMAIL, role=UserRole.ADMIN.value)


def test_admin_can_create_list_get_patch_and_delete_a_custom_role(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Full role lifecycle as an admin: create, list, read, patch, delete."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)

    created = ctx.client.post(
        "/api/v1/admin/rbac/roles",
        json={"name": _CUSTOM_ROLE, "description": "Content editors"},
        headers=hdr,
    )
    assert created.status_code == status.HTTP_201_CREATED
    body = created.json()
    assert body["name"] == _CUSTOM_ROLE
    assert body["is_system"] is False
    assert body["user_count"] == 0

    listing = ctx.client.get("/api/v1/admin/rbac/roles", headers=hdr)
    assert listing.status_code == status.HTTP_200_OK
    names = {row["name"] for row in listing.json()["items"]}
    assert {UserRole.USER.value, UserRole.ADMIN.value, _CUSTOM_ROLE} <= names

    detail = ctx.client.get(f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}", headers=hdr)
    assert detail.status_code == status.HTTP_200_OK
    assert detail.json()["description"] == "Content editors"

    patched = ctx.client.patch(
        f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}",
        json={"description": "Updated blurb"},
        headers=hdr,
    )
    assert patched.status_code == status.HTTP_200_OK
    assert patched.json()["description"] == "Updated blurb"

    deleted = ctx.client.delete(f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}", headers=hdr)
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert _get_role(ctx.session, _CUSTOM_ROLE) is None


def test_list_roles_custom_filter_excludes_system_roles(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """The ``custom`` system filter returns only non-system roles."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)

    response = ctx.client.get(
        "/api/v1/admin/rbac/roles",
        params={"system_filter": "custom"},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert response.status_code == status.HTTP_200_OK
    names = {row["name"] for row in response.json()["items"]}
    assert names == {_CUSTOM_ROLE}


def test_create_duplicate_role_is_409(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Creating a role whose name already exists is rejected with 409."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)

    response = ctx.client.post(
        "/api/v1/admin/rbac/roles",
        json={"name": _CUSTOM_ROLE},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert response.status_code == status.HTTP_409_CONFLICT


def test_cannot_delete_a_system_role(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """The built-in ``admin`` and ``user`` roles are protected from deletion (400)."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)

    for role in (UserRole.USER.value, UserRole.ADMIN.value):
        response = ctx.client.delete(f"/api/v1/admin/rbac/roles/{role}", headers=hdr)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert _get_role(ctx.session, role) is not None


def test_cannot_delete_a_role_still_assigned_to_users(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A custom role that a user still holds cannot be deleted (400)."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    _add_user(ctx.session, email=_MEMBER_EMAIL, role=_CUSTOM_ROLE)

    response = ctx.client.delete(
        f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}", headers=_bearer(_ADMIN_EMAIL)
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert _get_role(ctx.session, _CUSTOM_ROLE) is not None


def test_set_and_read_role_permission_matrix(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """PUT sets per-resource verbs; GET returns the full matrix with those grants."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    hdr = _bearer(_ADMIN_EMAIL)

    put = ctx.client.put(
        f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}/permissions",
        json={
            "permissions": {
                "dashboard": PermissionVerb.READ.value,
                "users": PermissionVerb.UPDATE.value,
            }
        },
        headers=hdr,
    )
    assert put.status_code == status.HTTP_200_OK

    get = ctx.client.get(
        f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}/permissions", headers=hdr
    )
    assert get.status_code == status.HTTP_200_OK
    by_res = {row["resource"]: row for row in get.json()["permissions"]}
    assert by_res["dashboard"]["max_verb"] == (PermissionVerb.READ.value)
    assert by_res["users"]["max_verb"] == (PermissionVerb.UPDATE.value)
    # A resource with no explicit grant reports a null verb.
    assert by_res["rbac"]["max_verb"] is None


def test_put_permissions_null_verb_revokes_the_grant(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Sending ``null`` for a resource removes its existing grant."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    hdr = _bearer(_ADMIN_EMAIL)
    ctx.client.put(
        f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}/permissions",
        json={"permissions": {"users": PermissionVerb.READ.value}},
        headers=hdr,
    )

    revoked = ctx.client.put(
        f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}/permissions",
        json={"permissions": {"users": None}},
        headers=hdr,
    )

    assert revoked.status_code == status.HTTP_200_OK
    by_res = {row["resource"]: row for row in revoked.json()["permissions"]}
    assert by_res["users"]["max_verb"] is None


def test_role_inheritance_rejects_a_cycle(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """An edge that would close an inheritance cycle is rejected with 400."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, "a")
    _add_role(ctx.session, "b")
    hdr = _bearer(_ADMIN_EMAIL)

    ctx.client.post(
        "/api/v1/admin/rbac/roles/a/inherits",
        json={"inherits_role": "b"},
        headers=hdr,
    )
    # b inheriting a would close a -> b -> a.
    cycle = ctx.client.post(
        "/api/v1/admin/rbac/roles/b/inherits",
        json={"inherits_role": "a"},
        headers=hdr,
    )
    assert cycle.status_code == status.HTTP_400_BAD_REQUEST

    # A self-edge is likewise rejected.
    self_edge = ctx.client.post(
        "/api/v1/admin/rbac/roles/a/inherits",
        json={"inherits_role": "a"},
        headers=hdr,
    )
    assert self_edge.status_code == status.HTTP_400_BAD_REQUEST


def test_add_list_and_remove_a_scoped_assignment(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A scoped assignment can be added, is listed, and can be removed."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, "inspector")
    member = _add_user(ctx.session, email=_MEMBER_EMAIL)
    hdr = _bearer(_ADMIN_EMAIL)

    added = ctx.client.post(
        f"/api/v1/admin/rbac/users/{member.id}/assignments",
        json={
            "role": "inspector",
            "scope_type": "property",
            "scope_id": "prop-1",
        },
        headers=hdr,
    )
    assert added.status_code == status.HTTP_201_CREATED
    scoped = [a for a in added.json()["assignments"] if a["scope_type"] == "property"]
    assert len(scoped) == 1
    assert scoped[0]["scope_id"] == "prop-1"
    assert scoped[0]["active"] is True
    assignment_id = scoped[0]["id"]

    removed = ctx.client.delete(
        f"/api/v1/admin/rbac/users/{member.id}/assignments/{assignment_id}",
        headers=hdr,
    )
    assert removed.status_code == status.HTTP_200_OK
    assert all(a["scope_type"] != "property" for a in removed.json()["assignments"])


def test_the_first_scoped_assignment_preserves_general_access_via_a_baseline_row(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Issue #147: adding a member's *first* assignment scoped narrows, never silently strips.

    ``active_roles_for_user``'s fallback to the ``User.role`` mirror only fires when a user has
    *zero* ``user_roles`` rows at all (Issue #136) — so a bare scoped row, with nothing else, would
    make their general (unscoped) access resolve to nothing everywhere the new scope doesn't apply.
    The endpoint must materialize an unscoped baseline row for their pre-existing role alongside it.
    """
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, "regional-manager")
    member = _add_user(
        ctx.session, email=_MEMBER_EMAIL, role="regional-manager"
    )  # zero user_roles rows yet — mirror-only, per Issue #136
    hdr = _bearer(_ADMIN_EMAIL)

    added = ctx.client.post(
        f"/api/v1/admin/rbac/users/{member.id}/assignments",
        json={
            "role": "regional-manager",
            "scope_type": "property",
            "scope_id": "prop-1",
        },
        headers=hdr,
    )
    assert added.status_code == status.HTTP_201_CREATED
    assignments = added.json()["assignments"]
    unscoped = [a for a in assignments if a["scope_type"] is None]
    assert len(unscoped) == 1
    assert unscoped[0]["role"] == "regional-manager"
    scoped = [a for a in assignments if a["scope_type"] == "property"]
    assert len(scoped) == 1

    # A second scoped assignment for the same user does not duplicate the baseline row.
    added_again = ctx.client.post(
        f"/api/v1/admin/rbac/users/{member.id}/assignments",
        json={
            "role": "regional-manager",
            "scope_type": "property",
            "scope_id": "prop-2",
        },
        headers=hdr,
    )
    assert added_again.status_code == status.HTTP_201_CREATED
    still_unscoped = [
        a for a in added_again.json()["assignments"] if a["scope_type"] is None
    ]
    assert len(still_unscoped) == 1


def test_expired_assignment_is_listed_inactive(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """An assignment with a past ``expires_at`` is reported as inactive."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, "inspector")
    member = _add_user(ctx.session, email=_MEMBER_EMAIL)
    hdr = _bearer(_ADMIN_EMAIL)

    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    added = ctx.client.post(
        f"/api/v1/admin/rbac/users/{member.id}/assignments",
        json={
            "role": "inspector",
            "scope_type": "lease",
            "scope_id": "lease-9",
            "expires_at": past,
        },
        headers=hdr,
    )
    assert added.status_code == status.HTTP_201_CREATED
    scoped = [a for a in added.json()["assignments"] if a["scope_type"] == "lease"]
    assert scoped[0]["active"] is False


def test_half_specified_scope_is_rejected(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Supplying scope_type without scope_id (or vice versa) is a 400."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, "inspector")
    member = _add_user(ctx.session, email=_MEMBER_EMAIL)
    hdr = _bearer(_ADMIN_EMAIL)

    resp = ctx.client.post(
        f"/api/v1/admin/rbac/users/{member.id}/assignments",
        json={"role": "inspector", "scope_type": "property"},
        headers=hdr,
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST


def test_set_role_replaces_the_unscoped_assignment(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """The plain set-role path replaces the single unscoped assignment and mirrors User.role."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, "manager2")
    member = _add_user(ctx.session, email=_MEMBER_EMAIL)
    hdr = _bearer(_ADMIN_EMAIL)

    ctx.client.put(
        f"/api/v1/admin/rbac/users/{member.id}/roles",
        json={"role": "manager2"},
        headers=hdr,
    )
    listing = ctx.client.get(
        f"/api/v1/admin/rbac/users/{member.id}/assignments", headers=hdr
    ).json()
    unscoped = [a for a in listing["assignments"] if a["scope_type"] is None]
    assert len(unscoped) == 1
    assert unscoped[0]["role"] == "manager2"


def test_matrix_grant_writes_a_grant_audit_row(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Setting a matrix cell writes one GRANT audit row with before/after JSON."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    hdr = _bearer(_ADMIN_EMAIL)

    ctx.client.put(
        f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}/permissions",
        json={"permissions": {"users": PermissionVerb.UPDATE.value}},
        headers=hdr,
    )

    with ctx.session() as db:
        rows = (
            db.execute(
                select(PermissionAuditLog).where(
                    PermissionAuditLog.target_type == "role_permission"
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "grant"
    assert row.actor == _ADMIN_EMAIL
    assert row.target_id == f"{_CUSTOM_ROLE}:{'users'}"
    assert row.before is None
    # All three fields of the grant since Issue #173 — a tier change is as consequential as a
    # verb change, so the trail records the tier whether or not this edit moved it.
    assert row.after == {
        "max_verb": PermissionVerb.UPDATE.value,
        "effect": "allow",
        "scope": GrantScope.OWN.value,
    }


def test_matrix_revoke_writes_a_revoke_audit_row(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Revoking a matrix cell (null) writes one REVOKE audit row capturing the prior state."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    hdr = _bearer(_ADMIN_EMAIL)
    ctx.client.put(
        f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}/permissions",
        json={"permissions": {"users": PermissionVerb.READ.value}},
        headers=hdr,
    )
    ctx.client.put(
        f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}/permissions",
        json={"permissions": {"users": None}},
        headers=hdr,
    )

    audit = ctx.client.get("/api/v1/admin/rbac/audit?action=revoke", headers=hdr).json()
    revokes = [r for r in audit["items"] if r["target_type"] == "role_permission"]
    assert len(revokes) == 1
    assert revokes[0]["before"] == {
        "max_verb": PermissionVerb.READ.value,
        "effect": "allow",
        "scope": GrantScope.OWN.value,
    }
    assert revokes[0]["after"] is None


def test_rolled_back_mutation_writes_no_audit_row(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A rejected mutation (unknown resource -> 422) leaves no audit row (same transaction)."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    hdr = _bearer(_ADMIN_EMAIL)

    resp = ctx.client.put(
        f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}/permissions",
        json={"permissions": {"not_a_real_resource": PermissionVerb.READ.value}},
        headers=hdr,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    with ctx.session() as db:
        count = db.execute(
            select(func.count()).select_from(PermissionAuditLog)
        ).scalar_one()
    assert count == 0


def test_audit_endpoint_is_rbac_gated_for_a_portal_role(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A portal role (no ``rbac`` grant) is forbidden from the audit trail."""
    ctx = make_admin_client()
    _admin(ctx)
    # An owner (portal-only) role with no rbac grant.
    with ctx.session() as db:
        db.add(RbacRole(name="owner", description=None, is_system=False))
        db.commit()
    _add_user(ctx.session, email="owner.audit@example.com", role="owner")

    resp = ctx.client.get(
        "/api/v1/admin/rbac/audit", headers=_bearer("owner.audit@example.com")
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_get_user_roles_returns_current_role(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Reading a user's role assignment returns the role currently on the row."""
    ctx = make_admin_client()
    _admin(ctx)
    member = _add_user(ctx.session, email=_MEMBER_EMAIL, role=UserRole.USER.value)

    response = ctx.client.get(
        f"/api/v1/admin/rbac/users/{member.id}/roles", headers=_bearer(_ADMIN_EMAIL)
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["role"] == UserRole.USER.value


def test_assigning_a_role_takes_effect_on_next_auth_me(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Changing a user's role changes the effective ``permissions`` on ``/auth/me``."""
    ctx = make_admin_client()
    _admin(ctx)
    member = _add_user(ctx.session, email=_MEMBER_EMAIL, role=UserRole.USER.value)
    member_hdr = _bearer(_MEMBER_EMAIL)

    # Baseline: a standard user only reaches the dashboard.
    before = ctx.client.get("/api/v1/auth/me", headers=member_hdr)
    assert before.json()["role"] == UserRole.USER.value
    assert "rbac" not in before.json()["permissions"]

    assigned = ctx.client.put(
        f"/api/v1/admin/rbac/users/{member.id}/roles",
        json={"role": UserRole.ADMIN.value},
        headers=_bearer(_ADMIN_EMAIL),
    )
    assert assigned.status_code == status.HTTP_200_OK
    assert assigned.json()["role"] == UserRole.ADMIN.value

    # The change is reflected immediately on the member's next /auth/me.
    after = ctx.client.get("/api/v1/auth/me", headers=member_hdr)
    assert after.json()["role"] == UserRole.ADMIN.value
    assert after.json()["permissions"]["rbac"] == (PermissionVerb.DELETE.value)


def test_assigning_an_unknown_role_is_400(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Assigning a role that is not defined is rejected with 400."""
    ctx = make_admin_client()
    _admin(ctx)
    member = _add_user(ctx.session, email=_MEMBER_EMAIL, role=UserRole.USER.value)

    response = ctx.client.put(
        f"/api/v1/admin/rbac/users/{member.id}/roles",
        json={"role": "nonexistent"},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_cannot_demote_the_last_admin_via_role_assignment(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Reassigning the only admin away from ``admin`` is refused (400)."""
    ctx = make_admin_client()
    admin = _admin(ctx)

    response = ctx.client.put(
        f"/api/v1/admin/rbac/users/{admin.id}/roles",
        json={"role": UserRole.USER.value},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_list_roles_unauthenticated_is_401(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """With auth enabled, an unauthenticated roles listing is rejected with 401."""
    ctx = make_admin_client()

    response = ctx.client.get("/api/v1/admin/rbac/roles")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_create_role_as_regular_user_is_403(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A standard user lacks CREATE on ``rbac`` and cannot create a role."""
    ctx = make_admin_client()
    _add_user(ctx.session, email=_MEMBER_EMAIL, role=UserRole.USER.value)

    response = ctx.client.post(
        "/api/v1/admin/rbac/roles",
        json={"name": _CUSTOM_ROLE},
        headers=_bearer(_MEMBER_EMAIL),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert _get_role(ctx.session, _CUSTOM_ROLE) is None


def test_assign_role_as_regular_user_is_403(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A standard user lacks UPDATE on ``rbac`` and cannot reassign roles."""
    ctx = make_admin_client()
    _admin(ctx)
    member = _add_user(ctx.session, email=_MEMBER_EMAIL, role=UserRole.USER.value)

    response = ctx.client.put(
        f"/api/v1/admin/rbac/users/{member.id}/roles",
        json={"role": UserRole.ADMIN.value},
        headers=_bearer(_MEMBER_EMAIL),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_roles_list_paginates_with_offset_limit_and_reports_total(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """`offset`/`limit` page the roles list while `total` stays the full match count."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    # Two system roles are seeded; add eight custom ones for ten total.
    for i in range(8):
        _add_role(ctx.session, f"role_{i:02d}")

    first = ctx.client.get(
        "/api/v1/admin/rbac/roles", params={"offset": 0, "limit": 3}, headers=hdr
    )
    assert first.status_code == status.HTTP_200_OK
    body = first.json()
    assert body["total"] == 10
    assert len(body["items"]) == 3

    second = ctx.client.get(
        "/api/v1/admin/rbac/roles", params={"offset": 3, "limit": 3}, headers=hdr
    )
    assert second.json()["total"] == 10
    first_names = [r["name"] for r in body["items"]]
    second_names = [r["name"] for r in second.json()["items"]]
    assert set(first_names).isdisjoint(second_names)  # a distinct page, no overlap


def test_roles_list_search_filters_by_name_substring(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """`q` narrows the roles list server-side to a name/description substring."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    _add_role(ctx.session, "editor")
    _add_role(ctx.session, "auditor")

    response = ctx.client.get(
        "/api/v1/admin/rbac/roles", params={"q": "edit"}, headers=hdr
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 1
    assert [r["name"] for r in body["items"]] == ["editor"]


def test_roles_list_system_filter_partitions_system_and_custom(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """`system_filter` returns only built-in or only custom roles."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    _add_role(ctx.session, "editor")

    system = ctx.client.get(
        "/api/v1/admin/rbac/roles", params={"system_filter": "system"}, headers=hdr
    ).json()
    assert all(r["is_system"] for r in system["items"])
    assert {UserRole.USER.value, UserRole.ADMIN.value} <= {
        r["name"] for r in system["items"]
    }

    custom = ctx.client.get(
        "/api/v1/admin/rbac/roles", params={"system_filter": "custom"}, headers=hdr
    ).json()
    assert [r["name"] for r in custom["items"]] == ["editor"]
    assert all(not r["is_system"] for r in custom["items"])


def test_roles_list_search_and_filter_combine_server_side(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A search term and the system filter are ANDed in one query."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    _add_role(ctx.session, "editor")

    # "editor" matches the term but is custom, so the system partition excludes it.
    combined = ctx.client.get(
        "/api/v1/admin/rbac/roles",
        params={"q": "editor", "system_filter": "system"},
        headers=hdr,
    ).json()
    assert combined["total"] == 0
    assert combined["items"] == []


def test_roles_list_no_match_returns_empty_page_not_error(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A filter that matches nothing is an empty `{items:[], total:0}`, not a 404."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)

    response = ctx.client.get(
        "/api/v1/admin/rbac/roles", params={"q": "nonesuch_zzz"}, headers=hdr
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"items": [], "total": 0}


def test_roles_list_sorts_by_name_desc(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """`sort=name&order=desc` reverses the default ascending name order."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    for name in ("aaa", "mmm", "zzz"):
        _add_role(ctx.session, name)

    asc = ctx.client.get(
        "/api/v1/admin/rbac/roles", params={"sort": "name"}, headers=hdr
    ).json()
    desc = ctx.client.get(
        "/api/v1/admin/rbac/roles",
        params={"sort": "name", "order": "desc"},
        headers=hdr,
    ).json()
    asc_names = [r["name"] for r in asc["items"]]
    desc_names = [r["name"] for r in desc["items"]]
    assert asc_names == sorted(asc_names)
    assert desc_names == list(reversed(asc_names))


def test_roles_list_sorts_by_user_count(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """`sort=users&order=desc` orders roles by how many users hold them."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    _add_role(ctx.session, "popular")
    _add_role(ctx.session, "empty")
    for i in range(3):
        _add_user(ctx.session, email=f"p{i}@example.com", role="popular")

    body = ctx.client.get(
        "/api/v1/admin/rbac/roles",
        params={"sort": "users", "order": "desc"},
        headers=hdr,
    ).json()
    names = [r["name"] for r in body["items"]]
    # "popular" (3 users) must sort ahead of "empty" (0 users).
    assert names.index("popular") < names.index("empty")


def test_roles_list_unknown_sort_column_falls_back_to_default(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """An off-allow-list `sort` is ignored (safe default), never executed as raw SQL."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    for name in ("aaa", "zzz"):
        _add_role(ctx.session, name)

    injected = ctx.client.get(
        "/api/v1/admin/rbac/roles",
        params={"sort": "name); DROP TABLE rbac_role;--"},
        headers=hdr,
    )
    assert injected.status_code == status.HTTP_200_OK
    default = ctx.client.get("/api/v1/admin/rbac/roles", headers=hdr).json()
    # Falls back to the default name-ascending order rather than erroring or injecting.
    assert [r["name"] for r in injected.json()["items"]] == [
        r["name"] for r in default["items"]
    ]


def test_roles_list_sort_composes_with_filter_and_paging(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Sort, the system filter and offset/limit all apply together in one query."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    for name in ("ccc", "aaa", "bbb"):
        _add_role(ctx.session, name)

    body = ctx.client.get(
        "/api/v1/admin/rbac/roles",
        params={
            "system_filter": "custom",
            "sort": "name",
            "order": "desc",
            "offset": 0,
            "limit": 2,
        },
        headers=hdr,
    ).json()
    assert body["total"] == 3  # three custom roles match the filter
    # Descending name, first page of two: ccc then bbb.
    assert [r["name"] for r in body["items"]] == ["ccc", "bbb"]


def test_bulk_delete_roles_deletes_custom_and_skips_protected(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Bulk delete removes custom roles but skips system + still-assigned roles, per id."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    _add_role(ctx.session, "editor")  # deletable custom role
    _add_role(ctx.session, "manager")  # assigned below → protected
    _add_user(ctx.session, email="mgr@example.com", role="manager")

    response = ctx.client.post(
        "/api/v1/admin/rbac/roles/bulk-delete",
        json={"ids": ["editor", "manager", UserRole.ADMIN.value, "ghost"]},
        headers=hdr,
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    outcomes = {r["id"]: r for r in body["results"]}
    assert outcomes["editor"]["deleted"] is True
    assert outcomes["manager"]["deleted"] is False  # still assigned to a user
    assert outcomes[UserRole.ADMIN.value]["deleted"] is False  # system role
    assert outcomes["ghost"]["deleted"] is False  # not found
    assert body["deleted_count"] == 1 and body["failed_count"] == 3

    # The custom role is gone; the protected ones remain.
    assert _get_role(ctx.session, "editor") is None
    assert _get_role(ctx.session, "manager") is not None
    assert _get_role(ctx.session, UserRole.ADMIN.value) is not None


def test_bulk_delete_roles_as_regular_user_is_403(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A caller without DELETE on `rbac` cannot bulk-delete roles."""
    ctx = make_admin_client()
    _add_user(ctx.session, email=_MEMBER_EMAIL, role=UserRole.USER.value)
    _add_role(ctx.session, "editor")

    response = ctx.client.post(
        "/api/v1/admin/rbac/roles/bulk-delete",
        json={"ids": ["editor"]},
        headers=_bearer(_MEMBER_EMAIL),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert _get_role(ctx.session, "editor") is not None


def _permissions_url(role: str) -> str:
    """The matrix endpoint for ``role``."""
    return f"/api/v1/admin/rbac/roles/{role}/permissions"


def _matrix_row(body: dict, resource: str) -> dict:
    """Pick one resource's row out of a matrix response."""
    return next(row for row in body["permissions"] if row["resource"] == resource)


def _stored_scope(
    factory: sessionmaker[Session], role: str, resource: str
) -> str | None:
    """The tier actually stored on ``role``'s cumulative grant for ``resource``."""
    with factory() as db:
        row = db.execute(
            select(RolePermission).where(
                RolePermission.role == role,
                RolePermission.resource == resource,
                RolePermission.action.is_(None),
            )
        ).scalar_one_or_none()
        return None if row is None else row.scope


def test_a_tier_set_from_the_grid_is_what_lands_in_the_row(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """The silent drop, fixed: the tier the grid sends is the tier stored, and the one returned."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    hdr = _bearer(_ADMIN_EMAIL)

    resp = ctx.client.put(
        _permissions_url(_CUSTOM_ROLE),
        json={
            "permissions": {
                "users": {
                    "max_verb": PermissionVerb.READ.value,
                    "effect": "allow",
                    "scope": GrantScope.BUSINESS.value,
                }
            }
        },
        headers=hdr,
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert (
        _stored_scope(ctx.session, _CUSTOM_ROLE, "users") == GrantScope.BUSINESS.value
    )

    row = _matrix_row(
        ctx.client.get(_permissions_url(_CUSTOM_ROLE), headers=hdr).json(), "users"
    )
    assert row["scope"] == GrantScope.BUSINESS.value
    assert row["effective_scope"] == GrantScope.BUSINESS.value


def test_a_cell_edit_that_does_not_mention_scope_preserves_the_stored_tier(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """The Issue #161 interaction: a grid edit must not undo a policy-authored breadth.

    Author the tier through the policy document, then touch the same cell from the grid without
    mentioning ``scope``. The verb changes; the tier does not.
    """
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    hdr = _bearer(_ADMIN_EMAIL)
    ctx.client.put(
        f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}/policy",
        json={
            "role": _CUSTOM_ROLE,
            "statements": [
                {
                    "resource": "users",
                    "verb": PermissionVerb.READ.value,
                    "scope": GrantScope.BUSINESS.value,
                }
            ],
        },
        headers=hdr,
    )
    assert (
        _stored_scope(ctx.session, _CUSTOM_ROLE, "users") == GrantScope.BUSINESS.value
    )

    resp = ctx.client.put(
        _permissions_url(_CUSTOM_ROLE),
        json={"permissions": {"users": PermissionVerb.UPDATE.value}},
        headers=hdr,
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert (
        _stored_scope(ctx.session, _CUSTOM_ROLE, "users") == GrantScope.BUSINESS.value
    )
    assert _matrix_row(resp.json(), "users")["max_verb"] == PermissionVerb.UPDATE.value


def test_an_invalid_tier_is_a_422_naming_it_and_writes_nothing(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """No field of a cell payload is silently discarded — an unknown tier names itself."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    hdr = _bearer(_ADMIN_EMAIL)

    resp = ctx.client.put(
        _permissions_url(_CUSTOM_ROLE),
        json={
            "permissions": {
                "users": {"max_verb": PermissionVerb.READ.value, "scope": "everything"}
            }
        },
        headers=hdr,
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "everything" in resp.text
    assert _stored_scope(ctx.session, _CUSTOM_ROLE, "users") is None


def test_a_tier_only_edit_is_a_change_and_is_audited(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Same verb, same effect, wider breadth — the most consequential edit this console offers."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    hdr = _bearer(_ADMIN_EMAIL)
    ctx.client.put(
        _permissions_url(_CUSTOM_ROLE),
        json={
            "permissions": {
                "users": {
                    "max_verb": PermissionVerb.READ.value,
                    "scope": GrantScope.OWN.value,
                }
            }
        },
        headers=hdr,
    )
    ctx.client.put(
        _permissions_url(_CUSTOM_ROLE),
        json={
            "permissions": {
                "users": {
                    "max_verb": PermissionVerb.READ.value,
                    "scope": GrantScope.BUSINESS.value,
                }
            }
        },
        headers=hdr,
    )

    audit = ctx.client.get("/api/v1/admin/rbac/audit?action=grant", headers=hdr).json()
    widening = [
        row
        for row in audit["items"]
        if row["target_id"] == f"{_CUSTOM_ROLE}:users"
        and row["before"]
        and row["before"]["scope"] != row["after"]["scope"]
    ]
    assert len(widening) == 1
    assert widening[0]["before"]["scope"] == GrantScope.OWN.value
    assert widening[0]["after"]["scope"] == GrantScope.BUSINESS.value
    assert widening[0]["before"]["max_verb"] == widening[0]["after"]["max_verb"]


def test_the_matrix_and_the_policy_document_round_trip_in_both_directions(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Author via the grid, export, re-import, export again: identical. And the reverse.

    The parity the two authoring paths owe each other (Issue #173). Before the tier existed on the
    grid they could not agree by construction: one wrote a field the other could not express.
    """
    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    hdr = _bearer(_ADMIN_EMAIL)
    policy_url = f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}/policy"

    # Grid → JSON → JSON.
    ctx.client.put(
        _permissions_url(_CUSTOM_ROLE),
        json={
            "permissions": {
                "users": {
                    "max_verb": PermissionVerb.READ.value,
                    "scope": GrantScope.BUSINESS.value,
                },
                "logs": {
                    "max_verb": PermissionVerb.READ.value,
                    "scope": GrantScope.OWN.value,
                },
            }
        },
        headers=hdr,
    )
    exported = ctx.client.get(policy_url, headers=hdr).json()
    assert {s["resource"]: s["scope"] for s in exported["statements"]} == {
        "users": GrantScope.BUSINESS.value,
        "logs": GrantScope.OWN.value,
    }
    reimported = ctx.client.put(policy_url, json=exported, headers=hdr).json()
    assert (reimported["created"], reimported["updated"], reimported["deleted"]) == (
        0,
        0,
        0,
    )
    assert ctx.client.get(policy_url, headers=hdr).json() == exported

    # JSON → grid → JSON: an unrelated cell edited in the grid leaves every tier alone.
    ctx.client.put(
        _permissions_url(_CUSTOM_ROLE),
        json={"permissions": {"logs": PermissionVerb.UPDATE.value}},
        headers=hdr,
    )
    after = ctx.client.get(policy_url, headers=hdr).json()
    assert {s["resource"]: s["scope"] for s in after["statements"]} == {
        "users": GrantScope.BUSINESS.value,
        "logs": GrantScope.OWN.value,
    }


def test_the_matrix_payload_carries_the_usage_window_and_threshold(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """The console must never render "never used" without saying since when it has been looking.

    A grant reads as unused on a week-old deployment for reasons that have nothing to do with the
    grant, so the collection start date and the threshold in force travel with every matrix — the
    filter's caveat is part of the payload, not a string in the page.
    """
    ctx = make_admin_client()
    _admin(ctx)
    body = ctx.client.get(
        _permissions_url(UserRole.ADMIN.value), headers=_bearer(_ADMIN_EMAIL)
    ).json()
    assert body["usage_enabled"] is True
    assert body["usage_unused_days"] == 90
    # Nothing has been flushed on this fresh database, so there is no window yet — and the payload
    # says so rather than implying collection has been running.
    assert body["usage_since"] is None
    for row in body["permissions"]:
        assert row["last_used_at"] is None
        assert row["hit_count"] == 0


def test_deleting_a_role_removes_its_recorded_usage(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Same rule at role granularity — nothing describing a grant that no longer exists survives."""
    from src.core import permission_usage
    from src.database.models import PermissionUsage

    ctx = make_admin_client()
    _admin(ctx)
    _add_role(ctx.session, _CUSTOM_ROLE)
    hdr = _bearer(_ADMIN_EMAIL)
    permission_usage.reset_buffer()
    permission_usage.record_allow([_CUSTOM_ROLE], "crates", PermissionVerb.READ.value)
    with ctx.session() as db:
        permission_usage.flush(db)
        db.commit()

    assert (
        ctx.client.delete(
            f"/api/v1/admin/rbac/roles/{_CUSTOM_ROLE}", headers=hdr
        ).status_code
        == status.HTTP_204_NO_CONTENT
    )
    with ctx.session() as db:
        remaining = (
            db.execute(
                select(PermissionUsage).where(PermissionUsage.role == _CUSTOM_ROLE)
            )
            .scalars()
            .all()
        )
    assert remaining == []
    permission_usage.reset_buffer()
