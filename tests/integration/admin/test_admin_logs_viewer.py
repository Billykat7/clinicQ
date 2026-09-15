"""Integration tests for the admin logs viewer API (Issue 8 / M1-08).

Read side of the S3 logging pipeline (Issue 7): admins browse the structured log
objects written to S3 and read a single object's body. Exercised at the HTTP layer
against an in-memory database with boto3 mocked:

* ``GET /admin/logs`` — list log objects filtered by date / level / path / keyword,
  within pagination caps;
* ``GET /admin/logs/object?key=`` — fetch and return one log object's UTF-8 body.

Both endpoints are gated by the ``logs`` READ verb: unauthenticated -> 401,
under-privileged (standard ``user`` role) -> 403. Invalid or missing keys are rejected
cleanly.

Per ``docs/IDE/RULES/testing-strategy.mdc`` these assert JSON and status codes — never
HTML — and build isolated ``Settings`` (``_env_file=None``) so a developer's local
``.env`` cannot change outcomes. ``AUTH_ENABLED`` is on so RBAC is enforced; the seeded
``admin`` role holds DELETE on ``logs`` (see
:func:`src.core.rbac.default_role_permissions`). No real AWS calls are made: the S3
client and the query module's settings are pointed at test doubles.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, S3LogPath, UserRole
from src.core import s3_logs_query, security
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

_TEST_JWT_SECRET = "admin-logs-viewer-test-secret-min-32-characters"
_ADMIN_EMAIL = "admin.logs@example.com"
_MEMBER_EMAIL = "member.logs@example.com"
_TEST_BUCKET = "test-logs-bucket"
_TEST_ENV = "dev"

# A fixed calendar day so listing keys and the endpoint's date filter line up
# regardless of when the suite runs.
_YEAR, _MONTH, _DAY = 2026, 8, 3


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

    This gives the ``admin`` role DELETE on ``logs`` (which covers READ), and the
    standard ``user`` role no access to ``logs``.
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

    Returns a namespace with ``client`` and a ``session`` factory for seeding users.
    The route and the security core share one ``Settings`` so the JWT secret matches.
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

        # Token minting/decoding read ``security.get_settings`` — point it at the same
        # isolated instance the route dependency resolves so the JWT secret matches.
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


def _add_user(factory: sessionmaker[Session], *, email: str, role: str) -> None:
    """Insert a verified user with the given role into the test database."""
    with factory() as db:
        db.add(User(email=email, role=role, is_verified=True))
        db.commit()


def _bearer(email: str) -> dict[str, str]:
    """Authorization header carrying a freshly minted access JWT for ``email``."""
    return {"Authorization": f"Bearer {create_access_token(sub=email, email=email)}"}


def _admin(ctx: SimpleNamespace) -> None:
    """Seed the admin user for ``ctx``."""
    _add_user(ctx.session, email=_ADMIN_EMAIL, role=UserRole.ADMIN.value)


def _member(ctx: SimpleNamespace) -> None:
    """Seed a standard (non-admin) user for ``ctx``."""
    _add_user(ctx.session, email=_MEMBER_EMAIL, role=UserRole.USER.value)


def _log_key(level: str, path: str, name: str) -> str:
    """Build an Issue #7-layout object key for the fixed test day."""
    return f"clinicq/{_TEST_ENV}/logs/{level}/{path}/{_YEAR}/{_MONTH:02d}/{_DAY:02d}/{name}"


def _mock_s3(
    monkeypatch: pytest.MonkeyPatch,
    *,
    contents: list[dict[str, object]] | None = None,
    bodies: dict[str, bytes] | None = None,
    bucket: str = _TEST_BUCKET,
) -> MagicMock:
    """Point the query module at an isolated bucket/env and a mocked S3 client.

    ``contents`` seeds a single ``list_objects_v2`` page; ``bodies`` maps object keys to
    the bytes returned by ``get_object``. Returns the mock client for assertions.
    """
    # The listing cache is process-global; drop it so each mocked S3 configuration is honoured.
    s3_logs_query.clear_logs_cache()
    client = MagicMock()
    client.list_objects_v2.return_value = {
        "Contents": contents or [],
        "IsTruncated": False,
    }

    def _get_object(**kwargs: object) -> dict:
        payload = (bodies or {}).get(str(kwargs["Key"]), b"")
        return {"Body": SimpleNamespace(read=lambda _n=None: payload)}

    client.get_object.side_effect = _get_object
    monkeypatch.setattr(
        s3_logs_query,
        "get_settings",
        lambda: SimpleNamespace(
            aws_s3_bucket=bucket,
            project_slug="clinicq",
            s3_environment=_TEST_ENV,
            s3_prefix=lambda kind: f"clinicq/{_TEST_ENV}/{kind}/",
        ),
    )
    monkeypatch.setattr(s3_logs_query, "get_s3_logs_client", lambda: client)
    return client


# --- listing ------------------------------------------------------------------


def test_admin_lists_log_objects_for_the_day(
    make_admin_client: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An admin lists the day's log objects with parsed level/path/date fields."""
    ctx = make_admin_client()
    _admin(ctx)
    lm = datetime(_YEAR, _MONTH, _DAY, 12, 0, 0, tzinfo=UTC)
    key = _log_key("error", "api", "clinicq-20260803-120000.json")
    _mock_s3(
        monkeypatch,
        contents=[{"Key": key, "Size": 512, "LastModified": lm}],
    )

    resp = ctx.client.get(
        "/api/v1/admin/logs",
        params={"year": _YEAR, "month": _MONTH, "day": _DAY},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["scope"] == "logs"
    assert [it["key"] for it in body["items"]] == [key]
    item = body["items"][0]
    assert item["log_level"] == "error"
    assert item["path_segment"] == S3LogPath.API.value
    assert item["calendar_date"] == "2026-08-03"
    assert item["size"] == 512


def test_admin_list_filters_by_path_segment(
    make_admin_client: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``path`` filter drops objects whose key is under a different segment."""
    ctx = make_admin_client()
    _admin(ctx)
    lm = datetime(_YEAR, _MONTH, _DAY, 9, 0, 0, tzinfo=UTC)
    api_key = _log_key("info", "api", "clinicq-20260803-090000.json")
    web_key = _log_key("info", "web", "clinicq-20260803-090500.json")
    _mock_s3(
        monkeypatch,
        contents=[
            {"Key": api_key, "Size": 10, "LastModified": lm},
            {"Key": web_key, "Size": 20, "LastModified": lm},
        ],
    )

    resp = ctx.client.get(
        "/api/v1/admin/logs",
        params={
            "year": _YEAR,
            "month": _MONTH,
            "day": _DAY,
            "path": S3LogPath.WEB.value,
        },
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert resp.status_code == status.HTTP_200_OK
    assert [it["key"] for it in resp.json()["items"]] == [web_key]


def test_admin_list_reports_message_when_bucket_unconfigured(
    make_admin_client: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no bucket configured, listing returns 200 with an explanatory message."""
    ctx = make_admin_client()
    _admin(ctx)
    _mock_s3(monkeypatch, bucket="")

    resp = ctx.client.get(
        "/api/v1/admin/logs",
        params={"year": _YEAR, "month": _MONTH, "day": _DAY},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["items"] == []
    assert body["message"]


# --- single object read -------------------------------------------------------


def test_admin_reads_single_log_object_body(
    make_admin_client: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The object endpoint returns the UTF-8 NDJSON body for a valid key."""
    ctx = make_admin_client()
    _admin(ctx)
    key = _log_key("error", "api", "clinicq-20260803-120000.json")
    payload = b'{"message": "boom"}\n{"message": "again"}\n'
    _mock_s3(monkeypatch, bodies={key: payload})

    resp = ctx.client.get(
        "/api/v1/admin/logs/object",
        params={"key": key},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["key"] == key
    assert "boom" in body["content"]
    assert body["truncated"] is False


def test_admin_object_read_rejects_key_outside_logs_prefix(
    make_admin_client: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key outside ``{slug}/{env}/logs/`` is rejected with 400 and never hits S3."""
    ctx = make_admin_client()
    _admin(ctx)
    client = _mock_s3(monkeypatch)

    resp = ctx.client.get(
        "/api/v1/admin/logs/object",
        params={"key": "dev/secrets/passwords.json"},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    client.get_object.assert_not_called()


# --- RBAC gating --------------------------------------------------------------


def test_list_requires_authentication(
    make_admin_client: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unauthenticated request to the listing endpoint is rejected with 401."""
    ctx = make_admin_client()
    _admin(ctx)
    _mock_s3(monkeypatch)

    resp = ctx.client.get("/api/v1/admin/logs")

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_list_forbidden_for_standard_user(
    make_admin_client: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A standard user without ``logs`` READ is rejected with 403."""
    ctx = make_admin_client()
    _member(ctx)
    _mock_s3(monkeypatch)

    resp = ctx.client.get(
        "/api/v1/admin/logs",
        params={"year": _YEAR, "month": _MONTH, "day": _DAY},
        headers=_bearer(_MEMBER_EMAIL),
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_object_read_forbidden_for_standard_user(
    make_admin_client: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A standard user cannot read a single log object body (403)."""
    ctx = make_admin_client()
    _member(ctx)
    _mock_s3(monkeypatch)

    resp = ctx.client.get(
        "/api/v1/admin/logs/object",
        params={"key": _log_key("info", "api", "x.json")},
        headers=_bearer(_MEMBER_EMAIL),
    )

    assert resp.status_code == status.HTTP_403_FORBIDDEN
