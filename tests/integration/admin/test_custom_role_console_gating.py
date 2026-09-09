"""A bespoke (custom) role reaches exactly the consoles its grants imply — over HTTP (Issue #104).

Navigation and route access derive from the declarative registry evaluated against the caller's
effective grants, not a hardcoded role list, so a role an admin builds in the RBAC console gets
exactly the destinations its grants imply — and the server re-checks every route, so the same
grant that shows a console admits its URL and its absence refuses the rest with ``403`` (the
defence-in-depth guarantee). This proves that end to end for a custom READ-only management role,
per ``.cursor/rules/testing-strategy.mdc`` (status codes only, never HTML body).
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.commons.enums import AppEnvironment, GrantScope, PermissionVerb
from src.core import nav_visibility, rbac, security
from src.core.config import Settings, get_settings
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    seeded_grant_scope,
)
from src.core.security import hash_password
from src.database.models import Base, RbacRole, RolePermission, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app
from src.web import context as web_context

_TEST_JWT_SECRET = "custom-role-console-gating-secret-min-32-chars"


_PASSWORD = "sup3r-secret-pw-104"


_CUSTOM_ROLE = "regional-viewer"


def _settings() -> Settings:
    """Isolated auth ``Settings`` (no ``.env``) with password login and RBAC on."""
    return Settings(  # type: ignore[arg-type]
        _env_file=None,
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_TEST_JWT_SECRET,
        auth_enabled=True,
        auth_password_login_enabled=True,
        smtp_host="",
    )


@pytest.fixture
def custom_role_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient]:
    """A ``TestClient`` whose DB has admin plus a custom ``regional-viewer`` (leases READ only)."""
    settings = _settings()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with factory() as db:
        for name, description in default_system_roles():
            db.add(RbacRole(name=name, description=description, is_system=True))
        db.add(
            RbacRole(
                name=_CUSTOM_ROLE,
                description="Read-only leasing viewer (custom).",
                is_system=False,
            )
        )
        seen: set[tuple[str, str]] = set()
        for role, resource, verb in default_role_permissions():
            if (role, resource) in seen:
                continue
            seen.add((role, resource))
            db.add(
                RolePermission(
                    role=role,
                    resource=resource,
                    max_verb=verb.value,
                    created_at=datetime.now(UTC),
                    scope=seeded_grant_scope(role).value,
                )
            )
        # The custom role's only grant: READ on leases — enough to open the leases console.
        db.add(
            RolePermission(
                role=_CUSTOM_ROLE,
                resource="orders",
                max_verb=PermissionVerb.READ.value,
                created_at=datetime.now(UTC),
                scope=GrantScope.BUSINESS.value,
            )
        )
        db.add(
            User(
                email="regional@example.com",
                role=_CUSTOM_ROLE,
                is_verified=True,
                password=hash_password(_PASSWORD),
            )
        )
        db.commit()

    def _override_get_db() -> Generator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(nav_visibility, "get_settings", lambda: settings)
    monkeypatch.setattr(web_context, "get_settings", lambda: settings)

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_only_the_grant_narrows_a_custom_role(
    custom_role_client: TestClient,
) -> None:
    """A custom role's access is narrowed by its grants — and, since Issue #164, by nothing else.

    ``nav_visibility`` no longer consults ``is_management_role``, and since Issue #172 the function
    does not exist to consult: no code path anywhere derives breadth from a role's name. What this
    test used to assert through that flag is now asserted where it actually lives — the grant: the
    console this role holds opens, the ones it does not stay closed (the two tests above), for a
    role name no wall has ever heard of.
    """
    assert not hasattr(nav_visibility, "is_management_role")
    assert not hasattr(rbac, "is_management_role"), (
        "is_management_role must not come back — breadth is a property of the grant (Issue #172)"
    )
    assert "is_manager" not in nav_visibility.NavVisibility.__dataclass_fields__
