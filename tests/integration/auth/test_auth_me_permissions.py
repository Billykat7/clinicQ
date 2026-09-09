"""Integration tests for effective RBAC permissions on ``GET /auth/me`` (Issue #5).

Verifies the access-control model end to end at the HTTP layer against an in-memory
database seeded with the same system roles and grants as the Alembic migration: the
``permissions`` map returned by ``/auth/me`` carries each resource the caller's role can
reach, resolved to its effective maximum verb (after parent->child cascade).

Per ``.cursor/rules/testing-strategy.mdc`` these assert JSON and status codes only and
build isolated ``Settings`` (``_env_file=None``) with ``AUTH_ENABLED`` on so RBAC is
actually enforced.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Callable, Generator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, PermissionVerb, UserRole
from src.core import security
from src.core.config import Settings, get_settings
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    seeded_grant_scope,
)
from src.core.security import create_access_token
from src.database.models import Base, RbacRole, RolePermission, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "auth-me-permissions-test-secret-min-32-chars"


_ADMIN_EMAIL = "admin.me@example.com"


_MEMBER_EMAIL = "member.me@example.com"


def _settings(**overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with RBAC enforcement on."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_enabled": True,
        "smtp_host": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _seed_rbac(factory: sessionmaker[Session]) -> None:
    """Seed the system roles and default grants, mirroring Alembic migration ``0004``."""
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


def _add_user(factory: sessionmaker[Session], email: str, role: str) -> None:
    """Insert a verified user with the given role."""
    with factory() as db:
        db.add(User(email=email, role=role, is_verified=True))
        db.commit()


def _bearer(email: str) -> dict[str, str]:
    """Authorization header carrying a freshly minted access JWT for ``email``."""
    return {"Authorization": f"Bearer {create_access_token(sub=email, email=email)}"}


@pytest.fixture
def me_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` wired to an isolated DB seeded with roles + grants."""
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

        # Token decoding reads the module-level ``get_settings`` — point it at the same
        # isolated instance the route dependency uses so the JWT secret matches.
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


def test_me_exposes_admin_effective_permissions(
    me_client: Callable[..., SimpleNamespace],
) -> None:
    """An admin sees DELETE on every seeded resource in the ``permissions`` map."""
    ctx = me_client()
    _add_user(ctx.session, _ADMIN_EMAIL, UserRole.ADMIN.value)

    response = ctx.client.get("/api/v1/auth/me", headers=_bearer(_ADMIN_EMAIL))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["role"] == UserRole.ADMIN.value
    assert body["permissions"] == {
        "dashboard": PermissionVerb.DELETE.value,
        "users": PermissionVerb.DELETE.value,
        "logs": PermissionVerb.DELETE.value,
        "rbac": PermissionVerb.DELETE.value,
    }


def test_me_exposes_standard_user_effective_permissions(
    me_client: Callable[..., SimpleNamespace],
) -> None:
    """A standard user only reaches the dashboard (READ); other resources are omitted."""
    ctx = me_client()
    _add_user(ctx.session, _MEMBER_EMAIL, UserRole.USER.value)

    response = ctx.client.get("/api/v1/auth/me", headers=_bearer(_MEMBER_EMAIL))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["role"] == UserRole.USER.value
    assert body["permissions"] == {"dashboard": PermissionVerb.READ.value}


def test_me_reflects_inherited_child_permissions(
    me_client: Callable[..., SimpleNamespace],
) -> None:
    """A grant on the ``reports`` hub cascades to its ``users``/``logs`` children on /me."""
    ctx = me_client()
    _add_user(ctx.session, _MEMBER_EMAIL, UserRole.USER.value)
    with ctx.session() as db:
        db.add(
            RolePermission(
                role=UserRole.USER.value,
                resource="reports",
                max_verb=PermissionVerb.UPDATE.value,
                created_at=datetime.now(UTC),
                scope=seeded_grant_scope(UserRole.USER.value).value,
            )
        )
        db.commit()

    response = ctx.client.get("/api/v1/auth/me", headers=_bearer(_MEMBER_EMAIL))

    assert response.status_code == status.HTTP_200_OK
    perms = response.json()["permissions"]
    # Parent grant is inherited by both children (no explicit child rows exist).
    assert perms["reports"] == PermissionVerb.UPDATE.value
    assert perms["users"] == PermissionVerb.UPDATE.value
    assert perms["logs"] == PermissionVerb.UPDATE.value


def test_me_without_token_is_401(
    me_client: Callable[..., SimpleNamespace],
) -> None:
    """``GET /auth/me`` requires authentication when RBAC/auth is enabled."""
    ctx = me_client()

    response = ctx.client.get("/api/v1/auth/me")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
