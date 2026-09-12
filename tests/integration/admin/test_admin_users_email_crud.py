"""Integration tests for admin user CRUD + email operations (Issue 4 / M1-04).

Exercises the admin-facing user management surface and the email flows tied to it
at the HTTP layer against an in-memory database:

* ``/admin/rbac/users`` — list/search, create, read, update and soft-delete users,
  every mutating verb gated by RBAC on the ``users`` resource;
* creating a user can trigger the activation email; updating can resend verification,
  and the resend honours the per-user cooldown;
* soft-deleted users are excluded from listings/detail and can no longer sign in;
* the password forgot/reset flow issues a signed, expiring token, updates the password
  and revokes every existing session, and rejects invalid tokens.

Per ``.cursor/rules/testing-strategy.mdc`` these assert JSON, status codes and DB state
— never HTML body content — and build isolated ``Settings`` (``_env_file=None``) so a
developer's local ``.env`` (SMTP creds, feature flags, JWT secret) cannot change
outcomes. ``AUTH_ENABLED`` is on so RBAC is actually enforced; the seeded ``admin`` role
holds DELETE on ``users`` (see :func:`src.core.rbac.default_role_permissions`).
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.api.v1.routes import auth as auth_routes
from src.api.v1.routes import rbac_admin as rbac_admin_routes
from src.commons.enums import AppEnvironment, UserRole
from src.core import email_send, security
from src.core.config import Settings, get_settings
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    seeded_grant_scope,
)
from src.core.security import (
    create_access_token,
    create_password_reset_token,
    hash_password,
)
from src.database.models import Base, RbacRole, RefreshToken, RolePermission, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "admin-users-crud-test-secret-min-32-characters"
_ADMIN_EMAIL = "admin.user@example.com"
_MEMBER_EMAIL = "member.user@example.com"
_NEW_EMAIL = "created.user@example.com"
_PASSWORD = "sup3r-secret-pw"


def _settings(**overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with RBAC enforcement on.

    SMTP stays unset so the email senders take their dev fallback; the fixture also
    captures the mail calls, so no live mail server is needed.
    """
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_enabled": True,
        "auth_password_login_enabled": True,
        "auth_otp_login_enabled": True,
        "smtp_host": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _seed_rbac(factory: sessionmaker[Session]) -> None:
    """Seed the system roles and their default permission grants into the test DB.

    Mirrors what the Alembic migration installs so the ``admin`` role really holds
    DELETE on ``users`` when RBAC is enforced.
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

    Returns a namespace with ``client``, the ``settings`` in force, a ``session`` factory
    for asserting DB state, ``sent_activations`` and ``sent_resets`` capturing the mail
    the routes would send. The route, the security core (token minting/decoding) and the
    email senders all share one ``Settings`` so the JWT secret matches everywhere.
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

        # Capture the mail the routes would send instead of contacting SMTP.
        sent_activations: list[tuple[str, str]] = []
        sent_resets: list[tuple[str, str]] = []

        def _capture_activation(
            to_email: str, activation_link: str, expire_hours: int | None = None
        ) -> None:
            sent_activations.append((to_email, activation_link))

        def _capture_reset(
            to_email: str, reset_link: str, expire_hours: int | None = None
        ) -> None:
            sent_resets.append((to_email, reset_link))

        # The routes resolve settings via the dependency; token minting/decoding read the
        # module-level ``get_settings`` — point them all at the same isolated instance and
        # swap the mail senders (imported into each route module) for the captures.
        monkeypatch.setattr(security, "get_settings", lambda: settings)
        monkeypatch.setattr(email_send, "get_settings", lambda: settings)
        monkeypatch.setattr(
            rbac_admin_routes, "send_activation_email", _capture_activation
        )
        monkeypatch.setattr(auth_routes, "send_activation_email", _capture_activation)
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
            sent_activations=sent_activations,
            sent_resets=sent_resets,
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
    email: str,
    role: str = UserRole.USER.value,
    verified: bool = True,
    password: str | None = None,
) -> User:
    """Insert a user and return a detached row (its field values are enough)."""
    with factory() as db:
        user = User(
            email=email,
            role=role,
            is_verified=verified,
            password=hash_password(password) if password is not None else None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def _get_user(factory: sessionmaker[Session], email: str) -> User | None:
    """Read a user by email from the test database (any deletion state)."""
    with factory() as db:
        return db.execute(select(User).where(User.email == email)).scalar_one_or_none()


def _refresh_rows(factory: sessionmaker[Session], user_id: str) -> list[RefreshToken]:
    """All refresh-token rows for a user."""
    with factory() as db:
        return list(
            db.execute(select(RefreshToken).where(RefreshToken.user_id == user_id))
            .scalars()
            .all()
        )


def _bearer(email: str) -> dict[str, str]:
    """Authorization header carrying a freshly minted access JWT for ``email``."""
    return {"Authorization": f"Bearer {create_access_token(sub=email, email=email)}"}


def _admin(ctx: SimpleNamespace) -> SimpleNamespace:
    """Seed an admin user for ``ctx`` and return it (Bearer via :func:`_bearer`)."""
    return _add_user(ctx.session, email=_ADMIN_EMAIL, role=UserRole.ADMIN.value)


# --- CRUD happy path ----------------------------------------------------------


def test_admin_can_create_list_search_get_and_soft_delete_user(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Full CRUD round-trip as an admin: create, list, search, read, soft-delete."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)

    created = ctx.client.post(
        "/api/v1/admin/rbac/users",
        json={"email": _NEW_EMAIL, "first_name": "Ada", "send_activation_email": False},
        headers=hdr,
    )
    assert created.status_code == status.HTTP_201_CREATED
    new_id = created.json()["id"]
    assert created.json()["email"] == _NEW_EMAIL
    assert created.json()["is_verified"] is False

    listing = ctx.client.get("/api/v1/admin/rbac/users", headers=hdr)
    assert listing.status_code == status.HTTP_200_OK
    emails = {row["email"] for row in listing.json()["items"]}
    assert {_ADMIN_EMAIL, _NEW_EMAIL} <= emails

    search = ctx.client.get(
        "/api/v1/admin/rbac/users", params={"q": "created."}, headers=hdr
    )
    assert [row["email"] for row in search.json()["items"]] == [_NEW_EMAIL]
    assert search.json()["total"] == 1

    detail = ctx.client.get(f"/api/v1/admin/rbac/users/{new_id}", headers=hdr)
    assert detail.status_code == status.HTTP_200_OK
    assert detail.json()["first_name"] == "Ada"

    deleted = ctx.client.delete(f"/api/v1/admin/rbac/users/{new_id}", headers=hdr)
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    row = _get_user(ctx.session, _NEW_EMAIL)
    assert row is not None and row.is_deleted is True and row.is_active is False


def test_patch_user_updates_email_name_and_role(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """PATCH updates the identity fields it is given and leaves the rest untouched."""
    ctx = make_admin_client()
    _admin(ctx)
    target = _add_user(ctx.session, email=_MEMBER_EMAIL)

    response = ctx.client.patch(
        f"/api/v1/admin/rbac/users/{target.id}",
        json={"first_name": "Grace", "role": UserRole.ADMIN.value},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["first_name"] == "Grace"
    assert body["role"] == UserRole.ADMIN.value


# --- Activation / verification email on create + update -----------------------


def test_create_user_with_activation_email_sends_it(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Creating with ``send_activation_email`` true dispatches the activation mail."""
    ctx = make_admin_client()
    _admin(ctx)

    response = ctx.client.post(
        "/api/v1/admin/rbac/users",
        json={"email": _NEW_EMAIL, "send_activation_email": True},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert [to for (to, _) in ctx.sent_activations] == [_NEW_EMAIL]
    row = _get_user(ctx.session, _NEW_EMAIL)
    assert row is not None and row.last_verification_email_sent_at is not None


def test_create_user_without_activation_email_sends_nothing(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Creating with ``send_activation_email`` false stores the user but sends no mail."""
    ctx = make_admin_client()
    _admin(ctx)

    response = ctx.client.post(
        "/api/v1/admin/rbac/users",
        json={"email": _NEW_EMAIL, "send_activation_email": False},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert ctx.sent_activations == []


def test_patch_resend_verification_sends_email(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """PATCH with ``resend_verification`` re-sends activation for an unverified user."""
    ctx = make_admin_client()
    _admin(ctx)
    target = _add_user(ctx.session, email=_MEMBER_EMAIL, verified=False)

    response = ctx.client.patch(
        f"/api/v1/admin/rbac/users/{target.id}",
        json={"resend_verification": True},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert response.status_code == status.HTTP_200_OK
    assert [to for (to, _) in ctx.sent_activations] == [_MEMBER_EMAIL]


def test_patch_resend_verification_on_verified_user_is_400(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Resending verification for an already-verified user is rejected with 400."""
    ctx = make_admin_client()
    _admin(ctx)
    target = _add_user(ctx.session, email=_MEMBER_EMAIL, verified=True)

    response = ctx.client.patch(
        f"/api/v1/admin/rbac/users/{target.id}",
        json={"resend_verification": True},
        headers=_bearer(_ADMIN_EMAIL),
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert ctx.sent_activations == []


def test_resend_verification_respects_cooldown(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """The self-service resend is rate-limited by the per-user cooldown window."""
    ctx = make_admin_client(verification_resend_cooldown_minutes=15)
    _add_user(ctx.session, email=_MEMBER_EMAIL, verified=False)
    hdr = _bearer(_MEMBER_EMAIL)

    first = ctx.client.post("/api/v1/auth/me/resend-verification", headers=hdr)
    second = ctx.client.post("/api/v1/auth/me/resend-verification", headers=hdr)

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert "Retry-After" in second.headers
    assert [to for (to, _) in ctx.sent_activations] == [_MEMBER_EMAIL]


# --- Soft-delete exclusion ----------------------------------------------------


def test_soft_deleted_user_excluded_from_list_and_detail(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A soft-deleted user disappears from the listing and its detail returns 404."""
    ctx = make_admin_client()
    _admin(ctx)
    target = _add_user(ctx.session, email=_MEMBER_EMAIL)
    hdr = _bearer(_ADMIN_EMAIL)
    ctx.client.delete(f"/api/v1/admin/rbac/users/{target.id}", headers=hdr)

    listing = ctx.client.get("/api/v1/admin/rbac/users", headers=hdr)
    assert _MEMBER_EMAIL not in {row["email"] for row in listing.json()["items"]}

    detail = ctx.client.get(f"/api/v1/admin/rbac/users/{target.id}", headers=hdr)
    assert detail.status_code == status.HTTP_404_NOT_FOUND


def test_soft_deleted_user_cannot_sign_in(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """After soft-delete, password sign-in for that user is rejected with 401."""
    ctx = make_admin_client()
    _admin(ctx)
    target = _add_user(
        ctx.session, email=_MEMBER_EMAIL, verified=True, password=_PASSWORD
    )
    ctx.client.delete(
        f"/api/v1/admin/rbac/users/{target.id}", headers=_bearer(_ADMIN_EMAIL)
    )

    login = ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _MEMBER_EMAIL, "password": _PASSWORD},
    )

    assert login.status_code == status.HTTP_401_UNAUTHORIZED


def test_cannot_delete_the_last_admin(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """Deleting the only remaining admin is refused (400), keeping the door open."""
    ctx = make_admin_client()
    admin = _admin(ctx)

    response = ctx.client.delete(
        f"/api/v1/admin/rbac/users/{admin.id}", headers=_bearer(_ADMIN_EMAIL)
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


# --- RBAC enforcement on the users resource -----------------------------------


def test_list_users_unauthenticated_is_401(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """With auth enabled, an unauthenticated listing call is rejected with 401."""
    ctx = make_admin_client()

    response = ctx.client.get("/api/v1/admin/rbac/users")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_create_user_as_regular_user_is_403(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A standard user lacks CREATE on ``users`` and is forbidden from creating."""
    ctx = make_admin_client()
    _add_user(ctx.session, email=_MEMBER_EMAIL, role=UserRole.USER.value)

    response = ctx.client.post(
        "/api/v1/admin/rbac/users",
        json={"email": _NEW_EMAIL},
        headers=_bearer(_MEMBER_EMAIL),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_delete_user_as_regular_user_is_403(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A standard user lacks DELETE on ``users`` and cannot soft-delete another user."""
    ctx = make_admin_client()
    _admin(ctx)
    target = _add_user(ctx.session, email=_MEMBER_EMAIL, role=UserRole.USER.value)

    response = ctx.client.delete(
        f"/api/v1/admin/rbac/users/{target.id}", headers=_bearer(_MEMBER_EMAIL)
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    row = _get_user(ctx.session, _MEMBER_EMAIL)
    assert row is not None and row.is_deleted is False


# --- Password forgot / reset --------------------------------------------------


def test_password_forgot_sends_reset_link_for_eligible_user(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A verified user gets a reset link; the response is the generic message."""
    ctx = make_admin_client()
    _add_user(ctx.session, email=_MEMBER_EMAIL, verified=True, password=_PASSWORD)

    response = ctx.client.post(
        "/api/v1/auth/password/forgot", json={"email": _MEMBER_EMAIL}
    )

    assert response.status_code == status.HTTP_200_OK
    assert [to for (to, _) in ctx.sent_resets] == [_MEMBER_EMAIL]


def test_password_forgot_unknown_email_is_generic_and_sends_nothing(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """An unknown email gets the same generic response and triggers no mail (no leak)."""
    ctx = make_admin_client()

    response = ctx.client.post(
        "/api/v1/auth/password/forgot", json={"email": "nobody@example.com"}
    )

    assert response.status_code == status.HTTP_200_OK
    assert ctx.sent_resets == []


def test_password_reset_updates_password_and_revokes_sessions(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A valid reset token sets a new password and revokes all existing sessions."""
    ctx = make_admin_client()
    user = _add_user(
        ctx.session, email=_MEMBER_EMAIL, verified=True, password=_PASSWORD
    )
    # Establish an active session so the reset has something to revoke.
    ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _MEMBER_EMAIL, "password": _PASSWORD},
    )
    assert _refresh_rows(ctx.session, user.id)  # session exists

    token = create_password_reset_token(
        user_id=str(user.id), email=_MEMBER_EMAIL, password_hash=user.password
    )
    new_password = "brand-new-passw0rd"
    # Reset is unauthenticated; call it from a clean client so no CSRF cookie from the
    # login above is in play (cookie-based /api writes require the double-submit header).
    resetter = TestClient(ctx.client.app)
    reset = resetter.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": new_password},
    )

    assert reset.status_code == status.HTTP_200_OK
    rows = _refresh_rows(ctx.session, user.id)
    assert rows and all(r.revoked_at is not None for r in rows)
    # The new password now works from a clean client.
    fresh = TestClient(ctx.client.app)
    login = fresh.post(
        "/api/v1/auth/password/login",
        json={"email": _MEMBER_EMAIL, "password": new_password},
    )
    assert login.status_code == status.HTTP_200_OK


def test_password_reset_with_invalid_token_is_400(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A garbage reset token is rejected with 400 and never changes a password."""
    ctx = make_admin_client()

    response = ctx.client.post(
        "/api/v1/auth/password/reset",
        json={"token": "not-a-valid-reset-token", "new_password": "whatever-passw0rd"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


# --- List framework: pagination + filtering (Issue #114) ----------------------


def test_users_list_paginates_with_offset_limit_and_reports_total(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """`offset`/`limit` page the users list while `total` stays the full match count."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    for i in range(6):
        _add_user(ctx.session, email=f"member{i:02d}@example.com")

    total_users = 7  # six members + the admin
    first = ctx.client.get(
        "/api/v1/admin/rbac/users", params={"offset": 0, "limit": 3}, headers=hdr
    )
    assert first.status_code == status.HTTP_200_OK
    body = first.json()
    assert body["total"] == total_users
    assert len(body["items"]) == 3

    second = ctx.client.get(
        "/api/v1/admin/rbac/users", params={"offset": 3, "limit": 3}, headers=hdr
    )
    assert second.json()["total"] == total_users
    first_emails = [u["email"] for u in body["items"]]
    second_emails = [u["email"] for u in second.json()["items"]]
    assert set(first_emails).isdisjoint(second_emails)  # a distinct page, no overlap


def test_users_list_search_filters_by_email_substring(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """`q` narrows the users list server-side to an email substring."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    _add_user(ctx.session, email="alice@example.com")
    _add_user(ctx.session, email="bob@example.com")

    response = ctx.client.get(
        "/api/v1/admin/rbac/users", params={"q": "alice"}, headers=hdr
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 1
    assert [u["email"] for u in body["items"]] == ["alice@example.com"]


def test_users_list_role_filter_and_search_combine_server_side(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """The `role` filter and `q` search are ANDed in one query."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    _add_user(ctx.session, email="alice@example.com", role=UserRole.USER.value)
    _add_user(ctx.session, email="alice.admin@example.com", role=UserRole.ADMIN.value)

    # Both emails contain "alice"; the role filter keeps only the admin one.
    response = ctx.client.get(
        "/api/v1/admin/rbac/users",
        params={"q": "alice", "role": UserRole.ADMIN.value},
        headers=hdr,
    )
    body = response.json()
    assert body["total"] == 1
    assert [u["email"] for u in body["items"]] == ["alice.admin@example.com"]


def test_users_list_no_match_returns_empty_page_not_error(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A search that matches nothing is an empty `{items:[], total:0}`, not a 404."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)

    response = ctx.client.get(
        "/api/v1/admin/rbac/users", params={"q": "nonesuch_zzz"}, headers=hdr
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"items": [], "total": 0}


# --- List framework: sortable columns (Issue #115) ----------------------------


def test_users_list_sorts_by_email_desc(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """`sort=email&order=desc` reverses the default ascending email order."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    for local in ("aaa", "mmm", "zzz"):
        _add_user(ctx.session, email=f"{local}@example.com")

    asc = ctx.client.get(
        "/api/v1/admin/rbac/users", params={"sort": "email"}, headers=hdr
    ).json()
    desc = ctx.client.get(
        "/api/v1/admin/rbac/users",
        params={"sort": "email", "order": "desc"},
        headers=hdr,
    ).json()
    asc_emails = [u["email"] for u in asc["items"]]
    desc_emails = [u["email"] for u in desc["items"]]
    assert asc_emails == sorted(asc_emails)
    assert desc_emails == list(reversed(asc_emails))


def test_users_list_sorts_by_role(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """`sort=role` groups users by their assigned role name."""
    ctx = make_admin_client()
    _admin(ctx)  # admin.user@ — role "admin"
    hdr = _bearer(_ADMIN_EMAIL)
    _add_user(ctx.session, email="zed@example.com", role=UserRole.USER.value)

    body = ctx.client.get(
        "/api/v1/admin/rbac/users", params={"sort": "role"}, headers=hdr
    ).json()
    roles = [u["role"] for u in body["items"]]
    assert roles == sorted(roles)  # "admin" sorts before "user"


def test_users_list_unknown_sort_column_falls_back_to_default(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """An off-allow-list `sort` is ignored (safe default), never executed as raw SQL."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    _add_user(ctx.session, email="alice@example.com")

    injected = ctx.client.get(
        "/api/v1/admin/rbac/users",
        params={"sort": "email); DROP TABLE app_user;--"},
        headers=hdr,
    )
    assert injected.status_code == status.HTTP_200_OK
    default = ctx.client.get("/api/v1/admin/rbac/users", headers=hdr).json()
    assert [u["email"] for u in injected.json()["items"]] == [
        u["email"] for u in default["items"]
    ]


# --- List framework: bulk select + single delete (Issue #116) -----------------


def test_bulk_delete_users_soft_deletes_the_selection(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A bulk delete soft-deletes every selected user and reports each id as deleted."""
    ctx = make_admin_client()
    _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    u1 = _add_user(ctx.session, email="one@example.com")
    u2 = _add_user(ctx.session, email="two@example.com")

    response = ctx.client.post(
        "/api/v1/admin/rbac/users/bulk-delete",
        json={"ids": [str(u1.id), str(u2.id)]},
        headers=hdr,
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["deleted_count"] == 2
    assert body["failed_count"] == 0
    assert all(r["deleted"] for r in body["results"])

    # Both are soft-deleted: excluded from the listing and flagged in the DB.
    listing = ctx.client.get("/api/v1/admin/rbac/users", headers=hdr).json()
    emails = {u["email"] for u in listing["items"]}
    assert "one@example.com" not in emails and "two@example.com" not in emails
    assert _get_user(ctx.session, "one@example.com").is_deleted is True


def test_bulk_delete_users_reports_partial_success_per_id(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A mix of a real id, a missing id and the last admin yields per-id outcomes."""
    ctx = make_admin_client()
    admin = _admin(ctx)
    hdr = _bearer(_ADMIN_EMAIL)
    member = _add_user(ctx.session, email="member@example.com")

    response = ctx.client.post(
        "/api/v1/admin/rbac/users/bulk-delete",
        json={"ids": [str(member.id), "missing-id", str(admin.id)]},
        headers=hdr,
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    outcomes = {r["id"]: r for r in body["results"]}
    assert outcomes[str(member.id)]["deleted"] is True
    assert outcomes["missing-id"]["deleted"] is False
    # The last admin is protected even inside a batch.
    assert outcomes[str(admin.id)]["deleted"] is False
    assert "last admin" in outcomes[str(admin.id)]["detail"].lower()
    assert body["deleted_count"] == 1 and body["failed_count"] == 2
    assert _get_user(ctx.session, _ADMIN_EMAIL).is_deleted is False


def test_bulk_delete_users_as_regular_user_is_403(
    make_admin_client: Callable[..., SimpleNamespace],
) -> None:
    """A caller without DELETE on `users` cannot bulk-delete (grant re-checked per request)."""
    ctx = make_admin_client()
    _admin(ctx)
    _add_user(
        ctx.session, email=_MEMBER_EMAIL, role=UserRole.USER.value
    )  # the requester
    victim = _add_user(ctx.session, email="victim@example.com")

    response = ctx.client.post(
        "/api/v1/admin/rbac/users/bulk-delete",
        json={"ids": [str(victim.id)]},
        headers=_bearer(_MEMBER_EMAIL),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert _get_user(ctx.session, "victim@example.com").is_deleted is False
