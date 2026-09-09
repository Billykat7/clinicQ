"""Integration tests for SECURITY_HTTP request-log lines (Issue 7 / M1-07).

The ``RequestLoggingMiddleware`` emits ``SECURITY_HTTP`` JSON lines for auth
anomalies. These exercise the middleware end-to-end, asserting the log record
(never HTML) per the testing-strategy Cursor rule. ``Settings`` are isolated
(``_env_file=None``) so a developer's local ``.env`` cannot change outcomes.
"""

import json
import logging
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment
from src.core import security
from src.core.config import Settings, get_settings
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    seeded_grant_scope,
)
from src.database.models import Base, RbacRole, RolePermission
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "security-http-test-secret-min-32-characters"
_LOGGER = "src.core.request_logging"


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
    """Seed system roles and default grants (``user`` gets no ``rbac`` access)."""
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
def make_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` wired to an isolated DB, seeded RBAC and ``Settings``."""
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


@contextmanager
def _capture_security_http(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Capture request-logging records around a request.

    ``create_app()`` runs ``setup_logging()``, which clears the root logger's handlers
    (including pytest's capture handler), so the caplog handler is re-attached here for
    the duration of the request and removed afterwards.
    """
    root = logging.getLogger()
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        root.addHandler(caplog.handler)
        try:
            yield
        finally:
            root.removeHandler(caplog.handler)


def _security_http_payloads(
    caplog: pytest.LogCaptureFixture,
) -> list[dict[str, object]]:
    """Return the parsed JSON from every SECURITY_HTTP line captured."""
    return [
        json.loads(r.getMessage().removeprefix("SECURITY_HTTP "))
        for r in caplog.records
        if r.getMessage().startswith("SECURITY_HTTP ")
    ]


def test_successful_request_emits_no_security_http_line(
    make_client: Callable[..., SimpleNamespace],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 2xx response produces no SECURITY_HTTP record."""
    ctx = make_client()
    with _capture_security_http(caplog):
        # Liveness is dependency-free, so it is a reliable 2xx without a live database
        # (readiness `/health/ready` would probe the DB and answer 503 in the test env).
        response = ctx.client.get("/health/live")

    assert response.status_code == status.HTTP_200_OK
    assert _security_http_payloads(caplog) == []
