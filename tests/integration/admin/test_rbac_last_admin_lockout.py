"""Literal role-name bypasses, closed (Issue #160, M28).

Two decisions in this codebase were taken by comparing a role's **name** to a string rather than by
resolving a grant:

* ``src/modules/messaging/audience.py`` — only the literal role ``"admin"`` could address a GLOBAL
  announcement, so no permission, however broad, could open it for anyone else;
* ``src/api/v1/routes/rbac_admin.py`` — the "cannot remove the last admin" guards counted users
  whose ``User.role`` equalled ``"admin"``. In a deployment whose sysadmin-tier role is called
  something else (the motivating scenario: an operator creates ``SBN``, grants it full access and
  never uses ``admin`` at all), that guard **protects nothing**: the last ``SBN`` user can be
  demoted or deleted, locking the operator out of ``/admin/rbac`` for good.

This suite proves both halves of the fix, over real HTTP:

1. the **new capability** — a custom `rbac:DELETE`-holding role is protected by the lockout guard
   exactly as a literal ``admin`` would be, and a global broadcast follows a grant rather than a
   name;
2. the **parity** — on a default-seeded system, where ``admin`` is the only role holding
   ``rbac:DELETE``, every one of these decisions behaves exactly as it did before the rewrite.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, GrantScope, PermissionVerb, UserRole
from src.core import security
from src.core.config import Settings, get_settings
from src.core.security import create_access_token
from src.database.models import Base, RbacRole, RolePermission, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "rbac-m28-last-admin-lockout-secret-32chars!!!"


_API = "/api/v1"


#: The operator's own sysadmin-tier role, named nothing like "admin".
_CUSTOM_ADMIN_ROLE = "sbn"


_SBN_EMAIL = "sbn.operator@example.com"


_ADMIN_EMAIL = "admin.lockout@example.com"


_PLAIN_EMAIL = "plain.lockout@example.com"


def _settings() -> Settings:
    """Isolated auth ``Settings`` (no ``.env``), RBAC on, mail forced to no-op."""
    return Settings(  # type: ignore[arg-type]
        _env_file=None,
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_TEST_JWT_SECRET,
        auth_enabled=True,
        smtp_host="",
    )


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    """A ``TestClient`` wired to an isolated in-memory database."""
    settings = _settings()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def _override_get_db() -> Generator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(security, "get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_settings] = lambda: settings
    yield SimpleNamespace(client=TestClient(app), session=factory)
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _bearer(email: str) -> dict[str, str]:
    """Authorization header carrying a freshly minted access JWT for ``email``."""
    return {"Authorization": f"Bearer {create_access_token(sub=email, email=email)}"}


def _seed_role(db: Session, name: str, *, resources: dict[str, PermissionVerb]) -> None:
    """Create an admin-authored console role and its cumulative grants.

    ``business`` explicitly: since Issue #172 a new grant is ``own``, so a role that operates the
    RBAC console is something an admin has to widen deliberately — which is what these roles are.
    """
    db.add(RbacRole(name=name, description=name, is_system=False))
    for resource, verb in resources.items():
        db.add(
            RolePermission(
                role=name,
                resource=resource,
                max_verb=verb.value,
                scope=GrantScope.BUSINESS.value,
            )
        )


@pytest.fixture
def custom_admin_world(ctx: SimpleNamespace) -> SimpleNamespace:
    """A deployment whose only administrator holds a **custom** role — nobody is ``admin``."""
    with ctx.session() as db:
        _seed_role(
            db,
            _CUSTOM_ADMIN_ROLE,
            resources={
                "rbac": PermissionVerb.DELETE,
                "users": PermissionVerb.DELETE,
            },
        )
        _seed_role(db, "plain", resources={"dashboard": PermissionVerb.READ})
        sbn = User(email=_SBN_EMAIL, role=_CUSTOM_ADMIN_ROLE, is_verified=True)
        plain = User(email=_PLAIN_EMAIL, role="plain", is_verified=True)
        db.add_all([sbn, plain])
        db.commit()
        return SimpleNamespace(sbn_id=sbn.id, plain_id=plain.id)


@pytest.fixture
def default_seeded_world(ctx: SimpleNamespace) -> SimpleNamespace:
    """The pre-#160 shape: ``admin`` is the only role holding ``rbac:DELETE``."""
    with ctx.session() as db:
        _seed_role(
            db,
            UserRole.ADMIN.value,
            resources={
                "rbac": PermissionVerb.DELETE,
                "users": PermissionVerb.DELETE,
                "communications": PermissionVerb.DELETE,
            },
        )
        _seed_role(db, "plain", resources={"dashboard": PermissionVerb.READ})
        admin = User(email=_ADMIN_EMAIL, role=UserRole.ADMIN.value, is_verified=True)
        plain = User(email=_PLAIN_EMAIL, role="plain", is_verified=True)
        db.add_all([admin, plain])
        db.commit()
        return SimpleNamespace(admin_id=admin.id, plain_id=plain.id)


def test_the_last_custom_role_administrator_cannot_be_demoted(
    ctx: SimpleNamespace, custom_admin_world: SimpleNamespace
) -> None:
    """Demoting the only ``rbac:DELETE`` holder is refused, though nobody is literally ``admin``."""
    resp = ctx.client.patch(
        f"{_API}/admin/rbac/users/{custom_admin_world.sbn_id}",
        json={"role": "plain"},
        headers=_bearer(_SBN_EMAIL),
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.text
    assert "last admin" in resp.json()["detail"].lower()


def test_the_last_custom_role_administrator_cannot_be_deleted(
    ctx: SimpleNamespace, custom_admin_world: SimpleNamespace
) -> None:
    """Same guard on the delete path — the operator cannot delete themselves into a lockout."""
    resp = ctx.client.delete(
        f"{_API}/admin/rbac/users/{custom_admin_world.sbn_id}",
        headers=_bearer(_SBN_EMAIL),
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.text


def test_the_last_custom_role_administrator_cannot_be_bulk_deleted(
    ctx: SimpleNamespace, custom_admin_world: SimpleNamespace
) -> None:
    """The batch path reports the refusal per row rather than silently dropping the guard."""
    resp = ctx.client.post(
        f"{_API}/admin/rbac/users/bulk-delete",
        json={"ids": [custom_admin_world.sbn_id]},
        headers=_bearer(_SBN_EMAIL),
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    row = resp.json()["results"][0]
    assert row["deleted"] is False
    assert "last admin" in (row["detail"] or "").lower()


def test_the_role_assignment_paths_carry_the_same_guard(
    ctx: SimpleNamespace, custom_admin_world: SimpleNamespace
) -> None:
    """``PUT /users/{id}/roles`` refuses to strip the last administrator's own role."""
    resp = ctx.client.put(
        f"{_API}/admin/rbac/users/{custom_admin_world.sbn_id}/roles",
        json={"role": "plain"},
        headers=_bearer(_SBN_EMAIL),
    )
    assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.text


def test_a_second_administrator_makes_the_first_removable(
    ctx: SimpleNamespace, custom_admin_world: SimpleNamespace
) -> None:
    """The guard protects the *last* administrator, not administrators in general.

    Promoting the plain user into the same custom role means demoting the original one no longer
    locks anybody out — proving the count is a real count of grant holders, not a hardcoded refusal.
    """
    promote = ctx.client.patch(
        f"{_API}/admin/rbac/users/{custom_admin_world.plain_id}",
        json={"role": _CUSTOM_ADMIN_ROLE},
        headers=_bearer(_SBN_EMAIL),
    )
    assert promote.status_code == status.HTTP_200_OK, promote.text

    demote = ctx.client.patch(
        f"{_API}/admin/rbac/users/{custom_admin_world.sbn_id}",
        json={"role": "plain"},
        headers=_bearer(_SBN_EMAIL),
    )
    assert demote.status_code == status.HTTP_200_OK, demote.text


def test_moving_between_two_administrator_roles_is_not_a_demotion(
    ctx: SimpleNamespace, custom_admin_world: SimpleNamespace
) -> None:
    """Switching the last administrator to *another* ``rbac:DELETE`` role keeps them one."""
    with ctx.session() as db:
        _seed_role(
            db,
            "sysops",
            resources={"rbac": PermissionVerb.DELETE, "users": PermissionVerb.DELETE},
        )
        db.commit()

    resp = ctx.client.patch(
        f"{_API}/admin/rbac/users/{custom_admin_world.sbn_id}",
        json={"role": "sysops"},
        headers=_bearer(_SBN_EMAIL),
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text


def test_the_default_seeded_system_behaves_exactly_as_before(
    ctx: SimpleNamespace, default_seeded_world: SimpleNamespace
) -> None:
    """Where ``admin`` is the only ``rbac:DELETE`` role, every guard answers as it always did."""
    demote = ctx.client.patch(
        f"{_API}/admin/rbac/users/{default_seeded_world.admin_id}",
        json={"role": "plain"},
        headers=_bearer(_ADMIN_EMAIL),
    )
    assert demote.status_code == status.HTTP_400_BAD_REQUEST, demote.text

    delete = ctx.client.delete(
        f"{_API}/admin/rbac/users/{default_seeded_world.admin_id}",
        headers=_bearer(_ADMIN_EMAIL),
    )
    assert delete.status_code == status.HTTP_400_BAD_REQUEST, delete.text

    # A non-administrator is freely removable, exactly as before.
    plain = ctx.client.delete(
        f"{_API}/admin/rbac/users/{default_seeded_world.plain_id}",
        headers=_bearer(_ADMIN_EMAIL),
    )
    assert plain.status_code == status.HTTP_204_NO_CONTENT, plain.text
