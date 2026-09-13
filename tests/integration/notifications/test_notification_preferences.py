"""Integration tests for notification preferences (Issue 72 / M11).

Real HTTP against an in-memory database, asserting JSON / status codes / DB state (never HTML),
with isolated ``Settings`` (``_env_file=None``) so a developer's local ``.env`` cannot change
outcomes (per ``docs/IDE/RULES/testing-strategy.mdc``):

* ``GET/PUT /api/v1/notifications/preferences`` — the signed-in user reads and updates their
  preferences; an essential category set off is coerced to email; an unknown timezone is a 422; an
  unauthenticated caller is 401.
* ``POST /notifications/unsubscribe`` — login-free, token-authenticated one-click unsubscribe turns
  a non-essential category off; an essential category is refused; an invalid token is a generic
  200 that reveals nothing and changes nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import (
    AppEnvironment,
    NotificationCategory,
    NotificationChannelPreference,
    UserRole,
)
from src.core import security
from src.core.config import Settings, get_settings
from src.core.security import create_access_token, create_unsubscribe_token
from src.database.models import Base, User
from src.database.models.notification_preference import NotificationPreference
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "notification-prefs-test-secret-min-32-characters"
_USER_EMAIL = "resident.prefs@example.com"
_PREFS = "/api/v1/notifications/preferences"
_UNSUBSCRIBE = "/notifications/unsubscribe"


def _settings(**overrides: object) -> Settings:
    """Isolated auth Settings (no .env), RBAC on, no SMTP, a public base URL for unsubscribe links."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_enabled": True,
        "smtp_host": "",
        "public_base_url": "https://properties.example.com",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def make_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` wired to an isolated DB with one seeded user and pinned Settings."""
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
        with factory() as db:
            db.add(User(email=_USER_EMAIL, role=UserRole.USER.value, is_verified=True))
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
        client = TestClient(app)
        apps.append((app, engine))
        return SimpleNamespace(client=client, settings=settings, session=factory)

    yield _make

    for app, engine in apps:
        app.dependency_overrides.clear()  # type: ignore[attr-defined]
        Base.metadata.drop_all(engine)  # type: ignore[arg-type]
        engine.dispose()  # type: ignore[attr-defined]


def _bearer(email: str) -> dict[str, str]:
    """Authorization header carrying a freshly minted access JWT for ``email``."""
    return {"Authorization": f"Bearer {create_access_token(sub=email, email=email)}"}


def _stored_channel(factory: sessionmaker[Session], category: str) -> str | None:
    """Return the stored channel for ``category`` on the seeded user (None when unset)."""
    with factory() as db:
        pref = db.execute(select(NotificationPreference)).scalar_one_or_none()
        if pref is None:
            return None
        return pref.channel_by_category.get(category)


# --- Preferences API ----------------------------------------------------------


def test_get_preferences_returns_defaults(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A signed-in user with no preference row gets every category defaulting to email."""
    ctx = make_client()
    resp = ctx.client.get(_PREFS, headers=_bearer(_USER_EMAIL))
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    categories = {c["category"]: c for c in body["categories"]}
    assert categories[NotificationCategory.MAINTENANCE.value]["channel"] == "email"
    assert categories[NotificationCategory.ACCOUNT.value]["essential"] is True
    assert categories[NotificationCategory.MARKETING.value]["essential"] is False


def test_get_preferences_requires_auth(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An unauthenticated caller cannot read preferences (401)."""
    ctx = make_client()
    assert ctx.client.get(_PREFS).status_code == status.HTTP_401_UNAUTHORIZED


def test_put_updates_channel(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """Turning a non-essential category off is persisted and reflected back."""
    ctx = make_client()
    resp = ctx.client.put(
        _PREFS,
        headers=_bearer(_USER_EMAIL),
        json={"channels": {NotificationCategory.MAINTENANCE.value: "off"}},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert (
        _stored_channel(ctx.session, NotificationCategory.MAINTENANCE.value)
        == NotificationChannelPreference.OFF.value
    )


def test_put_coerces_essential_off_to_email(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An essential category cannot be disabled — an 'off' is stored as email."""
    ctx = make_client()
    resp = ctx.client.put(
        _PREFS,
        headers=_bearer(_USER_EMAIL),
        json={"channels": {NotificationCategory.FINANCIAL.value: "off"}},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert (
        _stored_channel(ctx.session, NotificationCategory.FINANCIAL.value)
        == NotificationChannelPreference.EMAIL.value
    )


def test_put_rejects_unknown_timezone(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An unknown IANA timezone is a 422."""
    ctx = make_client()
    resp = ctx.client.put(
        _PREFS,
        headers=_bearer(_USER_EMAIL),
        json={"timezone": "Mars/Olympus_Mons"},
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# --- Login-free unsubscribe ---------------------------------------------------


def test_unsubscribe_turns_category_off(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A one-click unsubscribe POST with a valid token turns the category off (no login)."""
    ctx = make_client()
    token = create_unsubscribe_token(_USER_EMAIL, NotificationCategory.MARKETING.value)
    resp = ctx.client.post(_UNSUBSCRIBE, data={"token": token})
    assert resp.status_code == status.HTTP_200_OK
    assert (
        _stored_channel(ctx.session, NotificationCategory.MARKETING.value)
        == NotificationChannelPreference.OFF.value
    )


def test_unsubscribe_essential_refused_changes_nothing(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An essential category cannot be unsubscribed — the page loads but nothing changes."""
    ctx = make_client()
    token = create_unsubscribe_token(_USER_EMAIL, NotificationCategory.FINANCIAL.value)
    resp = ctx.client.post(_UNSUBSCRIBE, data={"token": token})
    assert resp.status_code == status.HTTP_200_OK
    assert _stored_channel(ctx.session, NotificationCategory.FINANCIAL.value) is None


def test_unsubscribe_invalid_token_changes_nothing(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A bad token renders a generic page (200) and never creates a preference row."""
    ctx = make_client()
    resp = ctx.client.post(_UNSUBSCRIBE, data={"token": "not-a-real-token"})
    assert resp.status_code == status.HTTP_200_OK
    with ctx.session() as db:
        assert db.execute(select(NotificationPreference)).scalar_one_or_none() is None


def test_unsubscribe_get_renders(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """The GET confirmation page renders for a valid token without touching the database."""
    ctx = make_client()
    token = create_unsubscribe_token(_USER_EMAIL, NotificationCategory.MARKETING.value)
    resp = ctx.client.get(_UNSUBSCRIBE, params={"token": token})
    assert resp.status_code == status.HTTP_200_OK
    with ctx.session() as db:
        assert db.execute(select(NotificationPreference)).scalar_one_or_none() is None
