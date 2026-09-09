"""Unit tests for the security core (Issue 1): hashing, JWTs, deps, prod guard.

Everything here runs offline with no database. A developer's local ``.env`` must
not change outcomes, so every ``Settings`` is built with ``_env_file=None`` and
explicit fields, and the token/dep tests point ``security.get_settings`` at an
isolated instance (see the testing-strategy Cursor rule).
"""

import hashlib
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.commons.enums import AppEnvironment, TokenType
from src.core import security
from src.core.config import _DEFAULT_JWT_SECRET, Settings

_TEST_JWT_SECRET = "unit-test-jwt-secret-min-32-characters-long"
_COOKIE_NAME = "bk_clinicq_access_token"


def _settings(**overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with test defaults."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "auth_enabled": True,
        "jwt_secret": _TEST_JWT_SECRET,
        "jwt_algorithm": "HS256",
        "jwt_access_expire_minutes": 15,
        "access_token_cookie_name": _COOKIE_NAME,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def auth_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Point the security module at isolated ``Settings`` for one test."""
    settings = _settings()
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    return settings


def _protected_app() -> FastAPI:
    """Minimal app exposing the required and optional current-user dependencies."""
    app = FastAPI()

    @app.get("/me")
    async def me(user: dict = Depends(security.get_current_user)) -> dict:
        """Echo the resolved token claims (401 when unauthenticated)."""
        return user

    @app.get("/maybe")
    async def maybe(
        user: dict | None = Depends(security.get_current_user_optional),
    ) -> dict:
        """Echo the claims when present, else ``None`` (never 401)."""
        return {"user": user}

    return app


# --- Password hashing (bcrypt, cost 12) ---------------------------------------


def test_hash_password_roundtrips() -> None:
    """A hashed password is a bcrypt hash that verifies against its plaintext.

    The cost factor is not asserted here: the test process deliberately hashes at a reduced
    bcrypt cost for speed (see the conftest override). The production work factor is pinned by
    :func:`test_default_bcrypt_cost_is_12` instead.
    """
    hashed = security.hash_password("Sup3r-Secret!")
    assert hashed != "Sup3r-Secret!"
    assert hashed.startswith("$2b$")  # a bcrypt hash
    assert security.verify_password("Sup3r-Secret!", hashed)
    assert not security.verify_password("wrong-password", hashed)


def test_default_bcrypt_cost_is_12() -> None:
    """The production work factor stays 12 — the default that ships in Settings.

    Independent of the test-only speed override, so a regression that lowered the real cost
    (or the production guard) is caught.
    """
    from pydantic import ValidationError

    from src.commons.enums import AppEnvironment
    from src.core.config import Settings

    assert Settings(_env_file=None).bcrypt_rounds == 12
    # The guard refuses a weakened cost outside development.
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment=AppEnvironment.PRODUCTION,
            jwt_secret="a-secure-production-secret-min-32-characters",
            bcrypt_rounds=4,
        )


def test_verify_password_rejects_malformed_hash() -> None:
    """A non-bcrypt string is rejected rather than raising."""
    assert security.verify_password("anything", "not-a-bcrypt-hash") is False


# --- Refresh-token hashing (SHA-256) ------------------------------------------


def test_hash_refresh_token_is_sha256_hex() -> None:
    """The stored value is the plain SHA-256 hex digest of the raw token."""
    token = "opaque-url-safe-refresh-secret"
    digest = security.hash_refresh_token(token)
    assert digest == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert len(digest) == 64


# --- Access token encode/decode -----------------------------------------------


def test_create_access_token_carries_expected_claims(auth_settings: Settings) -> None:
    """The access token decodes to ``sub``, ``email``, ``iat`` and ``exp``."""
    token = security.create_access_token("user-123", email="alice@example.com")
    payload = security.decode_token(token)
    assert payload is not None
    assert payload["sub"] == "user-123"
    assert payload["email"] == "alice@example.com"
    assert "iat" in payload
    assert "exp" in payload
    assert payload["exp"] > payload["iat"]


def test_access_token_is_signed_with_hs256(auth_settings: Settings) -> None:
    """The JWT header advertises the configured HS256 algorithm."""
    token = security.create_access_token("user-123")
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_create_access_token_omits_email_when_absent(auth_settings: Settings) -> None:
    """No ``email`` claim is written when the caller passes none."""
    payload = security.decode_token(security.create_access_token("user-123"))
    assert payload is not None
    assert "email" not in payload


def test_create_access_token_carries_uid_for_audit_attribution(
    auth_settings: Settings,
) -> None:
    """``uid`` (the ``user.id``) rides as its own claim so audit attribution has the FK value."""
    payload = security.decode_token(
        security.create_access_token(
            "alice@example.com", email="alice@example.com", uid="user-123"
        )
    )
    assert payload is not None
    assert payload["sub"] == "alice@example.com"
    assert payload["uid"] == "user-123"


def test_create_access_token_omits_uid_when_absent(auth_settings: Settings) -> None:
    """No ``uid`` claim is written when the caller passes none."""
    payload = security.decode_token(security.create_access_token("user-123"))
    assert payload is not None
    assert "uid" not in payload


def test_decode_token_rejects_expired(auth_settings: Settings) -> None:
    """An already-expired token decodes to ``None``."""
    now = datetime.now(UTC)
    expired = jwt.encode(
        {
            "sub": "user-123",
            "iat": int((now - timedelta(minutes=30)).timestamp()),
            "exp": int((now - timedelta(minutes=15)).timestamp()),
        },
        _TEST_JWT_SECRET,
        algorithm="HS256",
    )
    assert security.decode_token(expired) is None


def test_decode_token_rejects_wrong_signature(auth_settings: Settings) -> None:
    """A token signed with a different secret is rejected."""
    forged = jwt.encode(
        {"sub": "user-123"},
        "a-different-secret-at-least-32-characters",
        algorithm="HS256",
    )
    assert security.decode_token(forged) is None


# --- Typed activation / password-reset tokens ---------------------------------


def test_activation_token_is_typed_and_lowercases_email(
    auth_settings: Settings,
) -> None:
    """Activation tokens carry the ACTIVATION type and a lower-cased email."""
    token = security.create_activation_token("user-1", "Alice@Example.com")
    payload = security.decode_activation_token(token)
    assert payload is not None
    assert payload["type"] == TokenType.ACTIVATION.value
    assert payload["email"] == "alice@example.com"
    # A reset decoder must not accept an activation token.
    assert security.decode_password_reset_token(token) is None


def test_password_reset_token_is_typed(auth_settings: Settings) -> None:
    """Reset tokens carry the PASSWORD_RESET type and reject the other decoder."""
    token = security.create_password_reset_token("user-1", "alice@example.com")
    payload = security.decode_password_reset_token(token)
    assert payload is not None
    assert payload["type"] == TokenType.PASSWORD_RESET.value
    assert security.decode_activation_token(token) is None


# --- get_current_user dependency (Bearer or access cookie) --------------------


def test_get_current_user_accepts_bearer_token(auth_settings: Settings) -> None:
    """A valid ``Authorization: Bearer`` token resolves the user."""
    token = security.create_access_token("user-9", email="u@x.com")
    resp = TestClient(_protected_app()).get(
        "/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["sub"] == "user-9"


def test_get_current_user_accepts_access_cookie(auth_settings: Settings) -> None:
    """A valid access cookie resolves the user with no Authorization header."""
    token = security.create_access_token("user-9", email="u@x.com")
    client = TestClient(_protected_app())
    client.cookies.set(_COOKIE_NAME, token)
    resp = client.get("/me")
    assert resp.status_code == 200
    assert resp.json()["sub"] == "user-9"


def test_get_current_user_rejects_missing_token(auth_settings: Settings) -> None:
    """No credentials yield 401 when auth is enabled."""
    resp = TestClient(_protected_app()).get("/me")
    assert resp.status_code == 401


def test_get_current_user_rejects_invalid_token(auth_settings: Settings) -> None:
    """A malformed bearer token yields 401."""
    resp = TestClient(_protected_app()).get(
        "/me", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert resp.status_code == 401


def test_get_current_user_optional_returns_none_without_token(
    auth_settings: Settings,
) -> None:
    """The optional dependency returns ``None`` rather than raising."""
    resp = TestClient(_protected_app()).get("/maybe")
    assert resp.status_code == 200
    assert resp.json()["user"] is None


# --- Production guard on the default JWT secret --------------------------------


def test_settings_reject_default_jwt_secret_in_production() -> None:
    """The app refuses to boot in production with the shipped default secret."""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment=AppEnvironment.PRODUCTION,
            jwt_secret=_DEFAULT_JWT_SECRET,
        )


def test_settings_allow_default_jwt_secret_in_development() -> None:
    """Development may keep the convenient default secret."""
    settings = Settings(
        _env_file=None,
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_DEFAULT_JWT_SECRET,
    )
    assert settings.jwt_secret == _DEFAULT_JWT_SECRET
    assert settings.is_development


def test_settings_accept_custom_secret_in_production() -> None:
    """A non-default secret boots cleanly in production."""
    settings = Settings(
        _env_file=None,
        environment=AppEnvironment.PRODUCTION,
        jwt_secret=_TEST_JWT_SECRET,
    )
    assert settings.is_production
