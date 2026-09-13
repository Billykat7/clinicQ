"""Integration tests for password sign-in rate limiting (``/auth/password/login``, Issue #101).

Unlike OTP login, the password fallback authenticates on a single request, so without a
brake it is directly brute-forceable. These tests drive the HTTP endpoint against an
in-memory database and assert that:

* exhausting the per-email budget returns ``429`` (blunts guessing one account's password);
* exhausting the wider per-IP budget returns ``429`` (blunts spraying one password across
  many accounts) even when each individual email stays under its own budget;
* a throttled attempt is refused *before* credentials are checked — a correct password is
  still rejected with ``429`` once the window is exhausted.

Per ``docs/IDE/RULES/testing-strategy.mdc`` these assert JSON and status codes (never HTML)
and build isolated ``Settings`` (``_env_file=None``) so a developer's local ``.env`` cannot
change outcomes. The process-global limiter is reset around every test by the root
``conftest`` autouse fixture, so counts never leak between tests.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment
from src.core.config import Settings, get_settings
from src.core.security import hash_password
from src.database.models import Base, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "password-login-rate-limit-secret-min-32-chars"
_PASSWORD = "correct-horse-battery-01"
_LOGIN_PATH = "/api/v1/auth/password/login"


def _settings(**overrides: object) -> Settings:
    """Isolated auth ``Settings`` (no ``.env``); password login on, tight rate limits."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_password_login_enabled": True,
        "smtp_host": "",
        "password_login_rate_limit_per_email": 3,
        "password_login_rate_limit_per_ip": 5,
        "password_login_rate_limit_window_seconds": 900,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def make_client() -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` on an isolated in-memory DB with a verified user."""
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


def _add_user(factory: sessionmaker[Session], email: str) -> None:
    """Insert a verified user with the shared test password."""
    with factory() as db:
        db.add(User(email=email, is_verified=True, password=hash_password(_PASSWORD)))
        db.commit()


def test_per_email_budget_throttles_wrong_password_guesses(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """After the per-email budget is spent, the next attempt is 429 (not 401)."""
    ctx = make_client()
    email = "brute.target@example.com"
    _add_user(ctx.session, email)

    # Three wrong guesses are allowed (per-email budget = 3), each a plain 401.
    for _ in range(3):
        resp = ctx.client.post(
            _LOGIN_PATH, json={"email": email, "password": "wrong-guess-xx"}
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    # The fourth is throttled — and stays throttled even for the *correct* password,
    # proving the limiter runs before credentials are verified.
    throttled = ctx.client.post(
        _LOGIN_PATH, json={"email": email, "password": _PASSWORD}
    )
    assert throttled.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_per_ip_budget_throttles_spraying_across_accounts(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """One IP spraying distinct emails trips the wider per-IP budget."""
    ctx = make_client()
    # Five distinct emails (each under its own per-email budget of 3) still exhaust the
    # per-IP budget of 5, so the sixth attempt from this IP is throttled.
    for i in range(5):
        email = f"spray{i}@example.com"
        resp = ctx.client.post(
            _LOGIN_PATH, json={"email": email, "password": "wrong-guess-xx"}
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    over = ctx.client.post(
        _LOGIN_PATH,
        json={"email": "spray-last@example.com", "password": "wrong-guess-xx"},
    )
    assert over.status_code == status.HTTP_429_TOO_MANY_REQUESTS
