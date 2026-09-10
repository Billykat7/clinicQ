"""Account security flow tests (Issue 59 / M9): password change + re-verified email change.

Exercises the two sensitive account actions the security and profile pages drive, through the
real HTTP stack against an in-memory database:

    - POST   /api/v1/auth/me/password        change password (current password required)
    - POST   /api/v1/auth/me/email           start a re-verified email change
    - GET    /api/v1/auth/email/change/confirm  apply the change once the new address is proven

The acceptance criteria under test:
  * a password change requires the current password and revokes the user's *other* sessions
    while keeping the one making the change;
  * an email change only takes effect after the link sent to the new address is confirmed —
    the account keeps its old address until then.

Per ``.cursor/rules/testing-strategy.mdc`` the assertions are JSON, status codes and DB state —
never HTML — and ``Settings`` are built with ``_env_file=None`` so a developer's local ``.env``
cannot change the outcome. ``smtp_host`` is blanked so the confirmation mail takes the
"logged, not sent" path — no network, no SMTP.
"""

from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment
from src.core import security
from src.core.config import Settings, get_settings
from src.core.security import (
    create_access_token,
    create_email_change_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from src.database.models import Base, RefreshToken, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "account-security-flow-test-secret-min-32-chars"
_AUTH_URL = "/api/v1/auth"

_USER_EMAIL = "alex.secure@example.com"
_CURRENT_PASSWORD = "current-pass-123"
_NEW_PASSWORD = "brand-new-pass-456"
_NEW_EMAIL = "alex.new@example.com"


def _settings(**overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with auth on and no SMTP."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_enabled": True,
        "smtp_host": "",  # force the "logged, not sent" mail path — no network/SMTP
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def make_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` wired to an isolated DB and isolated ``Settings``."""
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


def _seed_user(
    factory: sessionmaker[Session],
    *,
    password: str | None = _CURRENT_PASSWORD,
) -> str:
    """Seed a verified user (optionally with a password) and return its id."""
    with factory() as db:
        user = User(
            email=_USER_EMAIL,
            is_verified=True,
            password=hash_password(password) if password is not None else None,
        )
        db.add(user)
        db.commit()
        return str(user.id)


def _seed_session(
    factory: sessionmaker[Session], user_id: str, *, user_agent: str
) -> str:
    """Insert an active refresh session for ``user_id`` and return its raw token."""
    raw = f"raw-{user_agent}-{user_id}"
    now = datetime.now(UTC)
    with factory() as db:
        db.add(
            RefreshToken(
                user_id=user_id,
                token_hash=hash_refresh_token(raw),
                expires_at=now + timedelta(days=7),
                last_seen_at=now,
                session_started_at=now,
                user_agent=user_agent,
            )
        )
        db.commit()
    return raw


# --------------------------------------------------------------------------------------
# Password change
# --------------------------------------------------------------------------------------


def test_password_change_requires_correct_current_password(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A wrong current password is a 400 and leaves the stored hash unchanged."""
    ctx = make_client()
    _seed_user(ctx.session)

    resp = ctx.client.post(
        f"{_AUTH_URL}/me/password",
        headers=_bearer(_USER_EMAIL),
        json={"current_password": "wrong-password", "new_password": _NEW_PASSWORD},
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    with ctx.session() as db:
        user = db.execute(select(User).where(User.email == _USER_EMAIL)).scalar_one()
        assert user.password is not None
        assert verify_password(_CURRENT_PASSWORD, user.password)


def test_password_change_rejects_passwordless_account(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """An OTP/OAuth-only account (no password) cannot change one here — it is directed to reset."""
    ctx = make_client()
    _seed_user(ctx.session, password=None)

    resp = ctx.client.post(
        f"{_AUTH_URL}/me/password",
        headers=_bearer(_USER_EMAIL),
        json={"current_password": "anything", "new_password": _NEW_PASSWORD},
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST


def test_password_change_updates_hash_and_revokes_other_sessions(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A correct change stores the new hash, revokes other sessions, and keeps the current one."""
    ctx = make_client()
    user_id = _seed_user(ctx.session)
    current_raw = _seed_session(ctx.session, user_id, user_agent="this-device")
    other_raw = _seed_session(ctx.session, user_id, user_agent="other-device")

    # The request carries the current session's refresh cookie so it is the one kept.
    ctx.client.cookies.set(ctx.settings.refresh_token_cookie_name, current_raw)
    resp = ctx.client.post(
        f"{_AUTH_URL}/me/password",
        headers=_bearer(_USER_EMAIL),
        json={"current_password": _CURRENT_PASSWORD, "new_password": _NEW_PASSWORD},
    )

    assert resp.status_code == status.HTTP_200_OK
    with ctx.session() as db:
        user = db.execute(select(User).where(User.email == _USER_EMAIL)).scalar_one()
        assert verify_password(_NEW_PASSWORD, user.password or "")

        current = db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(current_raw)
            )
        ).scalar_one()
        other = db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(other_raw)
            )
        ).scalar_one()
        assert (
            current.revoked_at is None
        )  # the device making the change stays signed in
        assert other.revoked_at is not None  # every other session is signed out


def test_password_change_rejects_reusing_the_same_password(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """Setting the new password equal to the current one is a 400."""
    ctx = make_client()
    _seed_user(ctx.session)

    resp = ctx.client.post(
        f"{_AUTH_URL}/me/password",
        headers=_bearer(_USER_EMAIL),
        json={"current_password": _CURRENT_PASSWORD, "new_password": _CURRENT_PASSWORD},
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST


# --------------------------------------------------------------------------------------
# Email change (request + confirm)
# --------------------------------------------------------------------------------------


def test_email_change_request_does_not_change_the_address(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """Requesting a change returns 200 but the account keeps its current email until confirmed."""
    ctx = make_client()
    _seed_user(ctx.session)

    resp = ctx.client.post(
        f"{_AUTH_URL}/me/email",
        headers=_bearer(_USER_EMAIL),
        json={"current_password": _CURRENT_PASSWORD, "new_email": _NEW_EMAIL},
    )

    assert resp.status_code == status.HTTP_200_OK
    with ctx.session() as db:
        # Still the old address; the new one does not exist yet.
        assert (
            db.execute(
                select(User).where(User.email == _USER_EMAIL)
            ).scalar_one_or_none()
            is not None
        )
        assert (
            db.execute(
                select(User).where(User.email == _NEW_EMAIL)
            ).scalar_one_or_none()
            is None
        )


def test_email_change_request_requires_current_password(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A wrong current password blocks the email change (sensitive action)."""
    ctx = make_client()
    _seed_user(ctx.session)

    resp = ctx.client.post(
        f"{_AUTH_URL}/me/email",
        headers=_bearer(_USER_EMAIL),
        json={"current_password": "wrong-password", "new_email": _NEW_EMAIL},
    )

    assert resp.status_code == status.HTTP_400_BAD_REQUEST


def test_email_change_request_rejects_an_address_already_in_use(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """Moving to an address another account already holds is a 409."""
    ctx = make_client()
    _seed_user(ctx.session)
    with ctx.session() as db:
        db.add(User(email=_NEW_EMAIL, is_verified=True))
        db.commit()

    resp = ctx.client.post(
        f"{_AUTH_URL}/me/email",
        headers=_bearer(_USER_EMAIL),
        json={"current_password": _CURRENT_PASSWORD, "new_email": _NEW_EMAIL},
    )

    assert resp.status_code == status.HTTP_409_CONFLICT


def test_email_change_confirm_applies_the_new_address(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A valid confirmation link moves the account to the new address and redirects to success."""
    ctx = make_client()
    user_id = _seed_user(ctx.session)
    token = create_email_change_token(user_id=user_id, new_email=_NEW_EMAIL)

    resp = ctx.client.get(
        f"{_AUTH_URL}/email/change/confirm",
        params={"token": token},
        follow_redirects=False,
    )

    assert resp.status_code == status.HTTP_302_FOUND
    assert "emailChanged=success" in resp.headers["location"]
    with ctx.session() as db:
        assert (
            db.execute(
                select(User).where(User.email == _USER_EMAIL)
            ).scalar_one_or_none()
            is None
        )
        moved = db.execute(select(User).where(User.email == _NEW_EMAIL)).scalar_one()
        assert moved.is_verified is True


def test_email_change_confirm_rejects_an_invalid_token(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A garbage/expired token redirects with an invalid status and changes nothing."""
    ctx = make_client()
    _seed_user(ctx.session)

    resp = ctx.client.get(
        f"{_AUTH_URL}/email/change/confirm",
        params={"token": "not-a-real-token"},
        follow_redirects=False,
    )

    assert resp.status_code == status.HTTP_302_FOUND
    assert "emailChanged=invalid" in resp.headers["location"]
    with ctx.session() as db:
        assert (
            db.execute(
                select(User).where(User.email == _USER_EMAIL)
            ).scalar_one_or_none()
            is not None
        )


def test_email_change_confirm_rejects_a_now_taken_address(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """If the new address was claimed between request and confirm, the change is refused."""
    ctx = make_client()
    user_id = _seed_user(ctx.session)
    token = create_email_change_token(user_id=user_id, new_email=_NEW_EMAIL)
    # Someone else grabs the target address before the link is opened.
    with ctx.session() as db:
        db.add(User(email=_NEW_EMAIL, is_verified=True))
        db.commit()

    resp = ctx.client.get(
        f"{_AUTH_URL}/email/change/confirm",
        params={"token": token},
        follow_redirects=False,
    )

    assert resp.status_code == status.HTTP_302_FOUND
    assert "emailChanged=invalid" in resp.headers["location"]
    with ctx.session() as db:
        # The original account keeps its address.
        assert (
            db.execute(
                select(User).where(User.email == _USER_EMAIL)
            ).scalar_one_or_none()
            is not None
        )
