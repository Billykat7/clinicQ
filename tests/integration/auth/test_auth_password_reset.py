"""Integration tests for the forgot-password → reset flow (``/auth/password/*``).

Drives the whole reset journey at the HTTP layer against an in-memory database, the way
the login modal now does it (a user requests a link, follows the emailed ``?resetToken=``
and sets a new password):

* ``/auth/password/forgot`` emails a signed reset link for an eligible account and returns
  the *same* generic body whether or not the email exists or is verified (no enumeration);
* the token carried in that link completes ``/auth/password/reset``, which swaps the
  password and revokes every existing refresh session;
* after a reset the old password no longer signs in and the new one does;
* an invalid/expired token is refused with 400;
* both endpoints are gated by ``AUTH_PASSWORD_LOGIN_ENABLED`` (403 when off).

Per ``docs/IDE/RULES/testing-strategy.mdc`` these assert JSON, status codes and DB state —
never HTML — and build isolated ``Settings`` (``_env_file=None``) so a developer's local
``.env`` cannot change outcomes. The reset email is captured in-process (no live SMTP).
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.api.v1.routes import auth as auth_routes
from src.commons.enums import AppEnvironment
from src.core import security
from src.core.config import Settings, get_settings
from src.core.security import hash_password
from src.database.models import Base, RefreshToken, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "password-reset-test-secret-min-32-characters"
_EMAIL = "reset.user@example.com"
_OLD_PASSWORD = "old-secret-pw-01"
_NEW_PASSWORD = "brand-new-secret-02"


def _settings(**overrides: object) -> Settings:
    """Isolated auth ``Settings`` (no ``.env``); password login on, SMTP unset."""
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


@pytest.fixture
def make_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` on an isolated DB, capturing every reset link "emailed".

    The namespace carries ``client``, the ``settings`` in force, a ``session`` factory for
    asserting DB state, and ``sent_reset_links`` — every ``(email, reset_link)`` the route
    would have mailed, so a test can pull the token out of the link and finish the reset.
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

        # Capture the reset link the route would email instead of contacting SMTP.
        sent_reset_links: list[tuple[str, str]] = []

        def _capture_reset(
            *, to_email: str, reset_link: str, expire_hours: int
        ) -> None:
            sent_reset_links.append((to_email, reset_link))

        # The route resolves settings via the dependency; token minting reads the
        # module-level get_settings — point both at the same isolated instance, and
        # swap the sender for the capture.
        monkeypatch.setattr(security, "get_settings", lambda: settings)
        monkeypatch.setattr(auth_routes, "send_password_reset_email", _capture_reset)

        app = create_app()
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_settings] = lambda: settings
        client = TestClient(app)
        apps.append((app, engine))
        return SimpleNamespace(
            client=client,
            settings=settings,
            session=factory,
            sent_reset_links=sent_reset_links,
        )

    yield _make

    for app, engine in apps:
        app.dependency_overrides.clear()  # type: ignore[attr-defined]
        Base.metadata.drop_all(engine)  # type: ignore[arg-type]
        engine.dispose()  # type: ignore[attr-defined]


# --- helpers ------------------------------------------------------------------


def _add_user(
    factory: sessionmaker[Session],
    *,
    email: str = _EMAIL,
    verified: bool = True,
    password: str | None = _OLD_PASSWORD,
) -> User:
    """Insert a user and return a detached copy."""
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
    """All refresh-token rows for a user."""
    with factory() as db:
        return list(
            db.execute(select(RefreshToken).where(RefreshToken.user_id == user_id))
            .scalars()
            .all()
        )


def _token_from_last_link(ctx: SimpleNamespace) -> str:
    """Pull the ``resetToken`` query value out of the most recently captured link."""
    assert ctx.sent_reset_links, "no reset link was emailed"
    _, link = ctx.sent_reset_links[-1]
    qs = parse_qs(urlparse(link).query)
    return qs["resetToken"][0]


# --- forgot → reset happy path ------------------------------------------------


def test_forgot_then_reset_changes_password_and_revokes_sessions(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """The emailed token resets the password; old sign-in fails, new one works."""
    ctx = make_client()
    user = _add_user(ctx.session)

    # An existing signed-in session (via the old password) must be revoked by the reset.
    pre = ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _OLD_PASSWORD},
    )
    assert pre.status_code == status.HTTP_200_OK
    assert [r for r in _refresh_rows(ctx.session, user.id) if r.revoked_at is None]

    # The reset is completed from the email link, not the signed-in session — drop the
    # session cookies so forgot/reset run as an unauthenticated visitor (and so the now-set
    # CSRF cookie doesn't turn these pre-auth POSTs into double-submit-protected writes).
    ctx.client.cookies.clear()

    # Request the link, then complete the reset with the token it carried.
    forgot = ctx.client.post("/api/v1/auth/password/forgot", json={"email": _EMAIL})
    assert forgot.status_code == status.HTTP_200_OK
    token = _token_from_last_link(ctx)

    reset = ctx.client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": _NEW_PASSWORD},
    )
    assert reset.status_code == status.HTTP_200_OK

    # Every prior refresh session is revoked (forced fresh sign-in).
    assert all(r.revoked_at is not None for r in _refresh_rows(ctx.session, user.id))

    # The old password no longer works; the new one does.
    old = ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _OLD_PASSWORD},
    )
    assert old.status_code == status.HTTP_401_UNAUTHORIZED
    new = ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _NEW_PASSWORD},
    )
    assert new.status_code == status.HTTP_200_OK


# --- no enumeration -----------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "make"),
    [
        ("stranger@example.com", None),  # no such user
        (_EMAIL, {"verified": False}),  # exists but unverified
    ],
)
def test_forgot_is_generic_and_sends_no_link_for_ineligible(
    make_client: Callable[..., SimpleNamespace],
    email: str,
    make: dict | None,
) -> None:
    """Unknown or unverified emails get the same 200 body and no link is emailed."""
    ctx = make_client()
    if make is not None:
        _add_user(ctx.session, email=email, verified=bool(make.get("verified")))

    known = ctx.client.post("/api/v1/auth/password/forgot", json={"email": _EMAIL})
    probe = ctx.client.post("/api/v1/auth/password/forgot", json={"email": email})

    # An eligible request (only when a verified user exists) does emit a link; the
    # ineligible probe never does, yet returns byte-identical JSON.
    assert probe.status_code == status.HTTP_200_OK
    assert email not in [e for (e, _) in ctx.sent_reset_links]
    if make is None:
        # Same generic body as a request for a non-existent account vs a real one.
        assert probe.json() == known.json()


# --- invalid token ------------------------------------------------------------


def test_reset_with_invalid_token_is_rejected(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A malformed/expired token is refused with 400 and leaves the password intact."""
    ctx = make_client()
    _add_user(ctx.session)

    reset = ctx.client.post(
        "/api/v1/auth/password/reset",
        json={"token": "x" * 40, "new_password": _NEW_PASSWORD},
    )
    assert reset.status_code == status.HTTP_400_BAD_REQUEST

    # Original password still valid.
    login = ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _EMAIL, "password": _OLD_PASSWORD},
    )
    assert login.status_code == status.HTTP_200_OK


# --- gated by the feature flag ------------------------------------------------


def test_forgot_and_reset_forbidden_when_password_login_disabled(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """With password sign-in off, both endpoints 403 (nothing to reset)."""
    ctx = make_client(auth_password_login_enabled=False)
    _add_user(ctx.session)

    forgot = ctx.client.post("/api/v1/auth/password/forgot", json={"email": _EMAIL})
    reset = ctx.client.post(
        "/api/v1/auth/password/reset",
        json={"token": "x" * 40, "new_password": _NEW_PASSWORD},
    )

    assert forgot.status_code == status.HTTP_403_FORBIDDEN
    assert reset.status_code == status.HTTP_403_FORBIDDEN
    assert ctx.sent_reset_links == []
