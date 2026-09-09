"""JSON policy documents over the RBAC catalog (Issue #161, M28).

``role_permission`` has always been one decomposed policy statement per row — resource, effect,
verb-or-action, and (since Issue #156) a ``scope`` condition. ``GET``/``PUT
/admin/rbac/roles/{name}/policy`` expose exactly those rows as one JSON document so a role can be
authored, reviewed, backed up and copied wholesale instead of a matrix cell at a time.

What matters is that the document is a **projection, not a second source of truth**, which is what
this suite pins:

* a round-trip (export, re-import unchanged) changes zero rows;
* a document that differs in all three ways at once creates, updates *and* deletes exactly the rows
  that differ — and nothing else;
* an unknown resource/action/verb/effect/scope is rejected with a message naming the offender, and
  nothing is written;
* every applied change writes the same ``PermissionAuditLog`` rows a matrix-UI edit would;
* the matrix view and the JSON view always agree — edit through one, read the other.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, GrantScope, UserRole
from src.core import security
from src.core.config import Settings, get_settings
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    refresh_resource_descendants_py,
    seed_named_catalog,
    seed_resource_catalog,
    seeded_grant_scope,
)
from src.core.security import create_access_token
from src.database.models import Base, RbacRole, RolePermission, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "rbac-policy-document-test-secret-32-characters"


_ADMIN_EMAIL = "admin.policy@example.com"


_API = "/api/v1/admin/rbac"


def _settings() -> Settings:
    """Isolated auth ``Settings`` (no ``.env``) with RBAC enforcement on."""
    return Settings(  # type: ignore[arg-type]
        _env_file=None,
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_TEST_JWT_SECRET,
        auth_enabled=True,
        smtp_host="",
    )


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    """A ``TestClient`` wired to an isolated DB seeded like a deployed one."""
    settings = _settings()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        for name, description in (*default_system_roles(),):
            db.add(RbacRole(name=name, description=description, is_system=True))
        seen: set[tuple[str, str]] = set()
        for helper in (default_role_permissions,):
            for role, resource, verb in helper():
                if (role, resource) in seen:
                    continue
                seen.add((role, resource))
                db.add(
                    RolePermission(
                        role=role,
                        resource=resource,
                        max_verb=verb.value,
                        scope=seeded_grant_scope(role).value,
                        created_at=datetime.now(UTC),
                    )
                )
        db.add(User(email=_ADMIN_EMAIL, role=UserRole.ADMIN.value, is_verified=True))
        db.commit()
        seed_resource_catalog(db)
        db.commit()
        seed_named_catalog(db)
        db.commit()
        refresh_resource_descendants_py(db)
        db.commit()

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


def _auth() -> dict[str, str]:
    """Authorization header for the seeded admin."""
    return {
        "Authorization": f"Bearer {create_access_token(sub=_ADMIN_EMAIL, email=_ADMIN_EMAIL)}"
    }


def _get_policy(client: TestClient, role: str) -> dict:
    """GET a role's policy document, asserting 200."""
    resp = client.get(f"{_API}/roles/{role}/policy", headers=_auth())
    assert resp.status_code == status.HTTP_200_OK, resp.text
    return resp.json()


def test_an_unknown_role_is_404(ctx: SimpleNamespace) -> None:
    """The policy endpoints share the console's own role lookup, 404 included."""
    resp = ctx.client.get(f"{_API}/roles/nope/policy", headers=_auth())
    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_the_export_always_states_its_tier_so_a_copy_cannot_narrow_silently(
    ctx: SimpleNamespace,
) -> None:
    """Every statement carries ``scope`` (Issue #172) — a terse export would be a trap.

    Omitting the tier when it matched the role's default was safe only while that default was the
    role-derived one. With ``own`` as the import default, a terse ``admin`` export applied to a
    fresh environment would narrow every grant it describes.
    """
    document = _get_policy(ctx.client, UserRole.ADMIN.value)
    assert document["statements"], "admin holds no grants — fixture drift"
    assert all(
        statement["scope"] == GrantScope.BUSINESS.value
        for statement in document["statements"]
    )
