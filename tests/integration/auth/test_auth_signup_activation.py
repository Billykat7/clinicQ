"""Integration tests for signup + email activation (Issue 2 / M1-02).

Exercises the self-registration flow at the HTTP layer against an in-memory
database: signup creates an unverified user and (with SMTP unset, the dev
fallback) sends nothing; the activation link verifies the account; expired and
invalid tokens are rejected; ``SIGNUP_ENABLED`` gates the endpoint; and
duplicate-email signup returns the same generic response without leaking whether
the account already exists.

Per ``docs/IDE/RULES/testing-strategy.mdc`` these assert JSON, status codes,
redirects and DB state — never HTML body content — and build isolated
``Settings`` (``_env_file=None``) so a developer's local ``.env`` (SMTP creds,
feature flags) cannot change outcomes.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, TokenType
from src.core import email_send, security
from src.core.config import Settings, get_settings
from src.database.models import Base, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "signup-activation-test-secret-min-32-characters"
_TEST_EMAIL = "new.user@example.com"


def _settings(**overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with signup-flow defaults.

    SMTP stays unset so :func:`send_activation_email` takes the dev fallback and
    sends nothing; the flow succeeds without a live mail server.
    """
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "signup_enabled": True,
        "jwt_secret": _TEST_JWT_SECRET,
        "smtp_host": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def make_signup_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` wired to an isolated DB and ``Settings``.

    Returns a namespace with ``client``, the ``settings`` in force, and a
    ``session`` context manager for asserting DB state. The route, the security
    core (token minting) and the email sender all share one ``Settings`` so the
    JWT secret matches everywhere.
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

        def _override_get_db() -> Generator[Session]:
            db = factory()
            try:
                yield db
            finally:
                db.close()

        # The route resolves settings via the dependency; token minting and the
        # email sender read the module-level ``get_settings`` — point all three
        # at the same isolated instance.
        monkeypatch.setattr(security, "get_settings", lambda: settings)
        monkeypatch.setattr(email_send, "get_settings", lambda: settings)

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


def _get_user(factory: sessionmaker[Session], email: str) -> User | None:
    """Read a user by email from the test database."""
    with factory() as db:
        return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def _count_users(factory: sessionmaker[Session]) -> int:
    """Count the users currently in the test database."""
    with factory() as db:
        return len(db.execute(select(User)).scalars().all())


# --- Signup → activate happy path ---------------------------------------------


def test_signup_creates_unverified_user(
    make_signup_client: Callable[..., SimpleNamespace],
) -> None:
    """Signup returns the generic message and stores an unverified user."""
    ctx = make_signup_client()

    response = ctx.client.post("/api/v1/auth/signup", json={"email": _TEST_EMAIL})

    assert response.status_code == status.HTTP_200_OK
    assert "activate" in response.json()["message"].lower()
    user = _get_user(ctx.session, _TEST_EMAIL)
    assert user is not None
    assert user.is_verified is False


def test_activation_link_verifies_account(
    make_signup_client: Callable[..., SimpleNamespace],
) -> None:
    """The activation link flips ``is_verified`` and redirects with success."""
    ctx = make_signup_client()
    ctx.client.post("/api/v1/auth/signup", json={"email": _TEST_EMAIL})
    user = _get_user(ctx.session, _TEST_EMAIL)
    assert user is not None

    # Mint the same token the emailed link carries (shared JWT secret).
    token = security.create_activation_token(str(user.id), _TEST_EMAIL)
    response = ctx.client.get(
        "/api/v1/auth/activate", params={"token": token}, follow_redirects=False
    )

    assert response.status_code == status.HTTP_302_FOUND
    assert "activated=success" in response.headers["location"]
    refreshed = _get_user(ctx.session, _TEST_EMAIL)
    assert refreshed is not None
    assert refreshed.is_verified is True


# --- Token rejection: expired / invalid ---------------------------------------


def test_expired_activation_token_is_rejected(
    make_signup_client: Callable[..., SimpleNamespace],
) -> None:
    """An expired activation token redirects to ``activated=invalid`` and does not verify."""
    ctx = make_signup_client()
    ctx.client.post("/api/v1/auth/signup", json={"email": _TEST_EMAIL})
    user = _get_user(ctx.session, _TEST_EMAIL)
    assert user is not None

    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "email": _TEST_EMAIL,
            "type": TokenType.ACTIVATION.value,
            "iat": int((now - timedelta(hours=48)).timestamp()),
            "exp": int((now - timedelta(hours=24)).timestamp()),
        },
        ctx.settings.jwt_secret,
        algorithm=ctx.settings.jwt_algorithm,
    )
    response = ctx.client.get(
        "/api/v1/auth/activate", params={"token": expired}, follow_redirects=False
    )

    assert response.status_code == status.HTTP_302_FOUND
    assert "activated=invalid" in response.headers["location"]
    refreshed = _get_user(ctx.session, _TEST_EMAIL)
    assert refreshed is not None
    assert refreshed.is_verified is False


def test_garbage_activation_token_is_rejected(
    make_signup_client: Callable[..., SimpleNamespace],
) -> None:
    """A non-JWT token is rejected with ``activated=invalid`` rather than a 500."""
    ctx = make_signup_client()

    response = ctx.client.get(
        "/api/v1/auth/activate", params={"token": "not-a-jwt"}, follow_redirects=False
    )

    assert response.status_code == status.HTTP_302_FOUND
    assert "activated=invalid" in response.headers["location"]


# --- Feature flag + enumeration guard -----------------------------------------


def test_signup_blocked_when_disabled(
    make_signup_client: Callable[..., SimpleNamespace],
) -> None:
    """With ``SIGNUP_ENABLED=false`` the endpoint answers 403 and stores nothing."""
    ctx = make_signup_client(signup_enabled=False)

    response = ctx.client.post("/api/v1/auth/signup", json={"email": _TEST_EMAIL})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert _count_users(ctx.session) == 0


def test_duplicate_signup_does_not_enumerate(
    make_signup_client: Callable[..., SimpleNamespace],
) -> None:
    """A second signup for the same email returns the identical generic response.

    No second user row is created, so the response cannot reveal that the account
    already exists.
    """
    ctx = make_signup_client()

    first = ctx.client.post("/api/v1/auth/signup", json={"email": _TEST_EMAIL})
    second = ctx.client.post("/api/v1/auth/signup", json={"email": _TEST_EMAIL})

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert first.json() == second.json()
    assert _count_users(ctx.session) == 1
