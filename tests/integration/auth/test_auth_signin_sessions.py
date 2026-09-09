"""Integration tests for sign-in, refresh rotation, logout and sessions (Issue 3 / M1-03).

Exercises the sign-in surface and session lifecycle at the HTTP layer against an
in-memory database:

* email OTP login (``/auth/otp/request`` → ``/auth/otp/verify``) issues an access,
  refresh and CSRF cookie, and is gated by ``AUTH_OTP_LOGIN_ENABLED``;
* password login (``/auth/password/login``) works when enabled and is rejected when
  disabled or on a bad credential;
* ``/auth/refresh`` rotates the refresh token (one-time use) and re-presenting a rotated
  token revokes every session for the user (theft detection);
* ``/auth/logout`` revokes the refresh row and clears the cookies;
* ``/auth/me`` returns the current profile and sessions can be listed and revoked;
* the double-submit CSRF middleware protects cookie-based ``/api/*`` writes and is
  skipped for ``Authorization: Bearer`` clients.

Per ``.cursor/rules/testing-strategy.mdc`` these assert JSON, status codes, cookies and
DB state — never HTML body content — and build isolated ``Settings`` (``_env_file=None``)
so a developer's local ``.env`` (SMTP creds, feature flags, JWT secret) cannot change
outcomes.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.api.v1.routes import auth as auth_routes
from src.commons.enums import AppEnvironment
from src.core import otp_store, refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.core.security import create_access_token, hash_password
from src.database.models import Base, RefreshToken, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "signin-sessions-test-secret-min-32-characters"
_EMAIL = "signin.user@example.com"
_PASSWORD = "sup3r-secret-pw"


def _settings(**overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with sign-in defaults.

    Both login methods are enabled and SMTP stays unset; the OTP email sender is
    monkeypatched in the fixture so no live mail server is needed.
    """
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_otp_login_enabled": True,
        "auth_password_login_enabled": True,
        "smtp_host": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_otp_state() -> Generator[None]:
    """Clear the process-global OTP + rate-limit store around every test."""
    otp_store.reset_otp_state()
    yield
    otp_store.reset_otp_state()


@pytest.fixture
def make_signin_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` wired to an isolated DB and ``Settings``.

    Returns a namespace with ``client``, the ``settings`` in force, a ``session``
    factory for asserting DB state, and ``sent_otps`` — a list capturing every OTP the
    route "emailed", so a test can complete the verify step. The route, the security
    core (token minting) and the OTP sender all share one ``Settings`` so the JWT secret
    and cookie names match everywhere.
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

        # Capture the OTP the route would email instead of contacting SMTP.
        sent_otps: list[tuple[str, str]] = []

        def _capture_otp(
            email: str, code: str, *, client_ip: str | None = None
        ) -> None:
            sent_otps.append((email, code))

        # The route resolves settings via the dependency; token minting, the OTP store
        # and the OTP sender read the module-level ``get_settings`` — point them all at
        # the same isolated instance, and swap the sender for the capture.
        monkeypatch.setattr(security, "get_settings", lambda: settings)
        monkeypatch.setattr(otp_store, "get_settings", lambda: settings)
        # The refresh-token session policy (idle / absolute-cap enforcement) also reads
        # the module-level ``get_settings`` — point it at the isolated instance so the
        # sliding-window and absolute-cap settings under test take effect.
        monkeypatch.setattr(refresh_token_policy, "get_settings", lambda: settings)
        monkeypatch.setattr(auth_routes, "send_otp_email", _capture_otp)

        app = create_app()
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_settings] = lambda: settings
        client = TestClient(app)
        apps.append((app, engine))
        return SimpleNamespace(
            client=client,
            settings=settings,
            session=factory,
            sent_otps=sent_otps,
        )

    yield _make

    for app, engine in apps:
        app.dependency_overrides.clear()  # type: ignore[attr-defined]
        Base.metadata.drop_all(engine)  # type: ignore[arg-type]
        engine.dispose()  # type: ignore[attr-defined]


# --- Test data helpers --------------------------------------------------------


def _add_user(
    factory: sessionmaker[Session],
    *,
    email: str = _EMAIL,
    verified: bool = True,
    password: str | None = None,
) -> User:
    """Insert a user and return the persisted row (detached copy fields are enough)."""
    with factory() as db:
        user = User(
            email=email,
            is_verified=verified,
            password=hash_password(password) if password is not None else None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def _refresh_rows(factory: sessionmaker[Session], user_id: str) -> list[RefreshToken]:
    """All refresh-token rows for a user, newest expiry first."""
    with factory() as db:
        return list(
            db.execute(
                select(RefreshToken)
                .where(RefreshToken.user_id == user_id)
                .order_by(RefreshToken.expires_at.desc())
            )
            .scalars()
            .all()
        )


def _bearer(email: str) -> dict[str, str]:
    """Authorization header carrying a freshly minted access JWT for ``email``."""
    return {"Authorization": f"Bearer {create_access_token(sub=email, email=email)}"}


def _csrf(ctx: SimpleNamespace) -> dict[str, str]:
    """Double-submit header echoing the client's current CSRF cookie value.

    Cookie-based ``/api/*`` writes must carry this once a CSRF cookie is set; the value
    rotates on every ``/auth/refresh``, so it is read fresh from the jar at call time.
    """
    token = ctx.client.cookies.get(ctx.settings.csrf_cookie_name)
    return {"X-CSRF-Token": token} if token else {}


# --- OTP login ----------------------------------------------------------------


def test_otp_login_issues_session_cookies_and_refresh_row(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """A verified user gets a code, verifies it, and receives all three session cookies."""
    ctx = make_signin_client()
    user = _add_user(ctx.session)

    request = ctx.client.post("/api/v1/auth/otp/request", json={"email": _EMAIL})
    assert request.status_code == status.HTTP_200_OK
    assert [e for (e, _) in ctx.sent_otps] == [_EMAIL]
    code = ctx.sent_otps[0][1]

    verify = ctx.client.post(
        "/api/v1/auth/otp/verify", json={"email": _EMAIL, "code": code}
    )

    assert verify.status_code == status.HTTP_200_OK
    body = verify.json()
    assert body["email"] == _EMAIL
    jar = ctx.client.cookies
    assert jar.get(ctx.settings.access_token_cookie_name)
    assert jar.get(ctx.settings.refresh_token_cookie_name)
    assert jar.get(ctx.settings.csrf_cookie_name)
    rows = _refresh_rows(ctx.session, user.id)
    assert len(rows) == 1 and rows[0].revoked_at is None


def test_otp_verify_with_wrong_code_is_rejected(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """A bad OTP is rejected with 400 and no refresh row is created."""
    ctx = make_signin_client()
    user = _add_user(ctx.session)
    ctx.client.post("/api/v1/auth/otp/request", json={"email": _EMAIL})

    verify = ctx.client.post(
        "/api/v1/auth/otp/verify", json={"email": _EMAIL, "code": "000000"}
    )

    assert verify.status_code == status.HTTP_400_BAD_REQUEST
    assert _refresh_rows(ctx.session, user.id) == []


def test_otp_request_disabled_returns_403(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """With OTP login disabled the request endpoint answers 403 and sends nothing."""
    ctx = make_signin_client(auth_otp_login_enabled=False)
    _add_user(ctx.session)

    response = ctx.client.post("/api/v1/auth/otp/request", json={"email": _EMAIL})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert ctx.sent_otps == []


def test_otp_request_for_unverified_email_does_not_enumerate(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """An unverified account gets the generic 403 and no code is sent."""
    ctx = make_signin_client()
    _add_user(ctx.session, verified=False)

    response = ctx.client.post("/api/v1/auth/otp/request", json={"email": _EMAIL})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert ctx.sent_otps == []


# --- Password login -----------------------------------------------------------


def test_password_login_succeeds_when_enabled(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """Correct email + password returns 200 with cookies and one refresh row."""
    ctx = make_signin_client()
    user = _add_user(ctx.session, password=_PASSWORD)

    response = ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["email"] == _EMAIL
    assert ctx.client.cookies.get(ctx.settings.refresh_token_cookie_name)
    assert len(_refresh_rows(ctx.session, user.id)) == 1


def test_password_login_rejected_when_disabled(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """With password login disabled the endpoint answers 403 even for valid credentials."""
    ctx = make_signin_client(auth_password_login_enabled=False)
    _add_user(ctx.session, password=_PASSWORD)

    response = ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_password_login_wrong_password_is_401(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """A wrong password returns the generic 401 (same shape as unknown email)."""
    ctx = make_signin_client()
    _add_user(ctx.session, password=_PASSWORD)

    response = ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": "not-the-password"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# --- Refresh rotation + reuse detection ---------------------------------------


def test_refresh_rotates_token_and_revokes_the_old_row(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """``/auth/refresh`` issues a new refresh cookie and revokes the presented one."""
    ctx = make_signin_client()
    user = _add_user(ctx.session, password=_PASSWORD)
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )
    first_token = ctx.client.cookies.get(ctx.settings.refresh_token_cookie_name)

    response = ctx.client.post("/api/v1/auth/refresh", headers=_csrf(ctx))

    assert response.status_code == status.HTTP_200_OK
    second_token = ctx.client.cookies.get(ctx.settings.refresh_token_cookie_name)
    assert second_token and second_token != first_token
    rows = _refresh_rows(ctx.session, user.id)
    assert len(rows) == 2
    revoked = [r for r in rows if r.revoked_at is not None]
    active = [r for r in rows if r.revoked_at is None]
    assert len(revoked) == 1 and len(active) == 1


def test_reusing_a_rotated_refresh_token_revokes_all_sessions(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """Presenting an already-rotated refresh token revokes every session (theft signal)."""
    ctx = make_signin_client()
    user = _add_user(ctx.session, password=_PASSWORD)
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )
    stolen = ctx.client.cookies.get(ctx.settings.refresh_token_cookie_name)

    # Legitimate rotation invalidates ``stolen`` and installs a new cookie.
    ctx.client.post("/api/v1/auth/refresh", headers=_csrf(ctx))

    # Replay the old (now revoked) token from a clean client that carries only the
    # stolen refresh cookie — no CSRF cookie, so the double-submit check is skipped and
    # the request reaches the reuse-detection branch.
    attacker = TestClient(ctx.client.app)
    attacker.cookies.set(ctx.settings.refresh_token_cookie_name, stolen)
    reuse = attacker.post("/api/v1/auth/refresh")

    assert reuse.status_code == status.HTTP_401_UNAUTHORIZED
    rows = _refresh_rows(ctx.session, user.id)
    assert rows and all(r.revoked_at is not None for r in rows)


def test_refresh_without_cookie_is_401(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """A refresh call with no refresh cookie returns 401 and clears cookies."""
    ctx = make_signin_client()

    response = ctx.client.post("/api/v1/auth/refresh")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# --- Sliding idle window + absolute cap ----------------------------------------


def _backdate_sole_refresh_row(
    factory: sessionmaker[Session], user_id: str, **values: object
) -> None:
    """Overwrite fields on the user's single refresh row to simulate elapsed time."""
    with factory() as db:
        row = db.execute(
            select(RefreshToken).where(RefreshToken.user_id == user_id)
        ).scalar_one()
        for key, value in values.items():
            setattr(row, key, value)
        db.commit()


def test_refresh_rejected_after_server_idle_timeout(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """A session idle past ``SESSION_SERVER_IDLE_TIMEOUT_MINUTES`` cannot refresh."""
    ctx = make_signin_client(session_server_idle_timeout_minutes=30)
    user = _add_user(ctx.session, password=_PASSWORD)
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )
    # Last activity is older than the idle window: the row must be revoked on refresh.
    _backdate_sole_refresh_row(
        ctx.session,
        user.id,
        last_seen_at=datetime.now(UTC) - timedelta(minutes=31),
    )

    response = ctx.client.post("/api/v1/auth/refresh", headers=_csrf(ctx))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    rows = _refresh_rows(ctx.session, user.id)
    assert rows and all(r.revoked_at is not None for r in rows)


def test_refresh_within_idle_window_still_rotates(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """An active session inside the idle window refreshes normally (sliding window)."""
    ctx = make_signin_client(session_server_idle_timeout_minutes=30)
    user = _add_user(ctx.session, password=_PASSWORD)
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )
    # Recent activity, comfortably inside the 30-minute window.
    _backdate_sole_refresh_row(
        ctx.session,
        user.id,
        last_seen_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    response = ctx.client.post("/api/v1/auth/refresh", headers=_csrf(ctx))

    assert response.status_code == status.HTTP_200_OK
    active = [r for r in _refresh_rows(ctx.session, user.id) if r.revoked_at is None]
    assert len(active) == 1


def test_refresh_rejected_after_absolute_max_days(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """A session older than ``SESSION_ABSOLUTE_MAX_DAYS`` cannot refresh, even if active."""
    ctx = make_signin_client(session_absolute_max_days=7)
    user = _add_user(ctx.session, password=_PASSWORD)
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )
    # The session anchor began beyond the absolute cap; recent activity cannot save it.
    _backdate_sole_refresh_row(
        ctx.session,
        user.id,
        session_started_at=datetime.now(UTC) - timedelta(days=8),
        last_seen_at=datetime.now(UTC),
    )

    response = ctx.client.post("/api/v1/auth/refresh", headers=_csrf(ctx))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    rows = _refresh_rows(ctx.session, user.id)
    assert rows and all(r.revoked_at is not None for r in rows)


# --- Logout -------------------------------------------------------------------


def test_logout_revokes_refresh_row_and_clears_cookies(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """Logout revokes the refresh row and a subsequent refresh is rejected."""
    ctx = make_signin_client()
    user = _add_user(ctx.session, password=_PASSWORD)
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )

    logout = ctx.client.post("/api/v1/auth/logout", headers=_csrf(ctx))

    assert logout.status_code == status.HTTP_204_NO_CONTENT
    rows = _refresh_rows(ctx.session, user.id)
    assert rows and all(r.revoked_at is not None for r in rows)
    assert not ctx.client.cookies.get(ctx.settings.refresh_token_cookie_name)
    # The revoked session can no longer refresh.
    assert (
        ctx.client.post("/api/v1/auth/refresh").status_code
        == status.HTTP_401_UNAUTHORIZED
    )


# --- Current user + sessions --------------------------------------------------


def test_me_returns_profile_for_bearer_client(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """``GET /auth/me`` returns the current profile for a Bearer-authenticated caller."""
    ctx = make_signin_client()
    _add_user(ctx.session, password=_PASSWORD)

    response = ctx.client.get("/api/v1/auth/me", headers=_bearer(_EMAIL))

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["email"] == _EMAIL
    assert body["is_verified"] is True
    assert body["has_password"] is True


def test_me_without_token_is_401(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """``GET /auth/me`` requires authentication."""
    ctx = make_signin_client()

    response = ctx.client.get("/api/v1/auth/me")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_sessions_can_be_listed_and_individually_revoked(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """Two sign-ins produce two sessions; a non-current one can be revoked by id."""
    ctx = make_signin_client()
    user = _add_user(ctx.session, password=_PASSWORD)

    # A second, independent client creates a separate session for the same user.
    other = TestClient(ctx.client.app)
    other.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )
    # The client under test signs in last, so its cookie is the "current" session.
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )

    listing = ctx.client.get("/api/v1/auth/me/sessions")
    assert listing.status_code == status.HTTP_200_OK
    sessions = listing.json()["sessions"]
    assert len(sessions) == 2
    current = [s for s in sessions if s["is_current"]]
    others = [s for s in sessions if not s["is_current"]]
    assert len(current) == 1 and len(others) == 1

    revoke = ctx.client.delete(
        f"/api/v1/auth/me/sessions/{others[0]['id']}", headers=_csrf(ctx)
    )
    assert revoke.status_code == status.HTTP_204_NO_CONTENT

    after = ctx.client.get("/api/v1/auth/me/sessions").json()["sessions"]
    assert [s["id"] for s in after] == [current[0]["id"]]
    assert len(_refresh_rows(ctx.session, user.id)) == 2  # rows kept, one now revoked


def test_current_session_cannot_be_revoked_by_id(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """Revoking the current session by id is rejected; logout is the supported path."""
    ctx = make_signin_client()
    _add_user(ctx.session, password=_PASSWORD)
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )
    session_id = ctx.client.get("/api/v1/auth/me/sessions").json()["sessions"][0]["id"]

    response = ctx.client.delete(
        f"/api/v1/auth/me/sessions/{session_id}", headers=_csrf(ctx)
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_revoke_all_other_sessions_signs_out_everywhere_else(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """``DELETE /auth/me/sessions`` revokes every other session, keeping the current one."""
    ctx = make_signin_client()
    user = _add_user(ctx.session, password=_PASSWORD)

    # Two independent sign-ins create two other sessions for the same user.
    for _ in range(2):
        other = TestClient(ctx.client.app)
        other.post(
            "/api/v1/auth/password/login",
            json={"email": _EMAIL, "password": _PASSWORD},
        )
    # The client under test signs in last, so its cookie is the "current" session.
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )
    assert len(_refresh_rows(ctx.session, user.id)) == 3

    response = ctx.client.delete("/api/v1/auth/me/sessions", headers=_csrf(ctx))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    remaining = ctx.client.get("/api/v1/auth/me/sessions").json()["sessions"]
    assert len(remaining) == 1 and remaining[0]["is_current"] is True
    active = [r for r in _refresh_rows(ctx.session, user.id) if r.revoked_at is None]
    assert len(active) == 1


# --- CSRF double-submit on cookie-based /api writes ---------------------------


def test_cookie_write_without_csrf_header_is_forbidden(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """A cookie-authenticated ``/api/*`` write without the CSRF header is rejected (403)."""
    ctx = make_signin_client()
    _add_user(ctx.session, password=_PASSWORD)
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )

    # PATCH /auth/me over the cookie session, deliberately omitting X-CSRF-Token.
    response = ctx.client.patch("/api/v1/auth/me", json={"first_name": "Nope"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "csrf" in response.json()["detail"].lower()


def test_cookie_write_with_matching_csrf_header_succeeds(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """Supplying the CSRF cookie value in the header lets the cookie write through."""
    ctx = make_signin_client()
    _add_user(ctx.session, password=_PASSWORD)
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _PASSWORD},
    )
    csrf = ctx.client.cookies.get(ctx.settings.csrf_cookie_name)

    response = ctx.client.patch(
        "/api/v1/auth/me",
        json={"first_name": "Yes"},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["first_name"] == "Yes"


def test_bearer_write_skips_csrf_check(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """A Bearer-authenticated write is exempt from CSRF (no cookie session in play)."""
    ctx = make_signin_client()
    _add_user(ctx.session, password=_PASSWORD)

    response = ctx.client.patch(
        "/api/v1/auth/me",
        json={"first_name": "Api"},
        headers=_bearer(_EMAIL),
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["first_name"] == "Api"


# --- Public config ------------------------------------------------------------


def test_auth_config_reports_enabled_flags(
    make_signin_client: Callable[..., SimpleNamespace],
) -> None:
    """``GET /auth/config`` echoes the effective sign-in feature flags."""
    ctx = make_signin_client(auth_password_login_enabled=False)

    response = ctx.client.get("/api/v1/auth/config")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["otp_login_enabled"] is True
    assert body["password_login_enabled"] is False
