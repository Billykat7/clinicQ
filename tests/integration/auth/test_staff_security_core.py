"""The staff identity every protected route checks (Issue 15), over real HTTP.

Staff reuse the kernel's ``user`` table and its hashed ``refresh_token`` rows; there is no
``staff_users`` table, because a second identity table is how two sign-in paths drift apart. What
this file proves, each through a real request against an in-memory database:

* a sign-in mints an HS256 access JWT carrying subject, type, role, sites and a 15-minute expiry,
  and the sites come from the account's site-scoped role assignments, not from a column;
* :func:`~src.core.security.get_current_staff` accepts that token from the ``Authorization``
  header and from the httpOnly access cookie alike;
* only an ``access``-typed token is a session: the activation, reset, email-change and unsubscribe
  links are signed with the same secret, and before Issue 15 each of them opened ``/auth/me`` as a
  Bearer token (the unsubscribe one for a year);
* an account switched off or deleted is refused on its very next request, through ``/auth/me`` and
  through an RBAC-gated route alike, while its access token is still unexpired;
* a new password bcrypt cannot hash whole is a 422, where it used to be a 500.

Settings are built with ``_env_file=None`` so a local ``.env`` cannot change the outcome.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, AssignmentScopeType, TokenType, UserRole
from src.core import refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    seeded_grant_scope,
)
from src.database.models import Base, RbacRole, RolePermission, User, UserRoleAssignment
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app
from tests.factories import FACTORY_STAFF_PASSWORD, StaffFactory

_SECRET = "staff-security-core-test-secret-min-32-chars"
_SITE_A = "0199b0c0-0000-7000-8000-00000000000a"
_SITE_B = "0199b0c0-0000-7000-8000-00000000000b"


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    """An app on an isolated SQLite database with the kernel's roles and grants seeded."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_SECRET,
        auth_enabled=True,
        auth_password_login_enabled=True,
        smtp_host="",
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
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

    def _db() -> Generator[Session]:
        with factory() as db:
            yield db

    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(refresh_token_policy, "get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings
    yield SimpleNamespace(client=TestClient(app), settings=settings, session=factory)
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _staff(ctx: SimpleNamespace, **overrides: object) -> User:
    """Persist one staff member (a receptionist unless told otherwise) and return it detached."""
    with ctx.session() as db:
        user = StaffFactory.create(db, **overrides)  # type: ignore[arg-type]
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def _sign_in(ctx: SimpleNamespace, email: str) -> str:
    """Sign in with the factory password and return the access JWT from the cookie."""
    response = ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": email, "password": FACTORY_STAFF_PASSWORD},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    token = ctx.client.cookies.get(ctx.settings.access_token_cookie_name)
    assert token
    return token


# --- the access token -------------------------------------------------------------------


def test_a_sign_in_mints_an_hs256_token_with_subject_role_sites_and_expiry(
    ctx: SimpleNamespace,
) -> None:
    """Decoded with PyJWT alone: the claims a client may read, and a 15-minute life."""
    staff = _staff(ctx, site_id=_SITE_A)
    token = _sign_in(ctx, staff.email)

    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    claims = jwt.decode(token, _SECRET, algorithms=["HS256"])
    assert claims["sub"] == staff.email
    assert claims["uid"] == staff.id
    assert claims["type"] == TokenType.ACCESS.value
    assert claims["role"] == UserRole.RECEPTIONIST.value
    assert claims["sites"] == [_SITE_A]
    assert claims["exp"] - claims["iat"] == 15 * 60


def test_the_sites_claim_lists_every_site_the_account_holds_a_role_at(
    ctx: SimpleNamespace,
) -> None:
    """One person may work at two clinics: the claim is a list, read from the assignments."""
    staff = _staff(ctx, site_id=_SITE_B)
    with ctx.session() as db:
        db.add(
            UserRoleAssignment(
                user_id=staff.id,
                role=UserRole.NURSE_DOCTOR.value,
                scope_type=AssignmentScopeType.SITE.value,
                scope_id=_SITE_A,
            )
        )
        db.commit()
    claims = jwt.decode(_sign_in(ctx, staff.email), _SECRET, algorithms=["HS256"])
    assert claims["sites"] == sorted([_SITE_A, _SITE_B])


# --- get_current_staff: header and cookie -------------------------------------------------


def test_get_current_staff_accepts_the_bearer_header(ctx: SimpleNamespace) -> None:
    """An API client sends the token in ``Authorization``; no cookie is involved."""
    staff = _staff(ctx)
    token = _sign_in(ctx, staff.email)
    ctx.client.cookies.clear()
    response = ctx.client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["email"] == staff.email


def test_get_current_staff_accepts_the_access_cookie(ctx: SimpleNamespace) -> None:
    """A browser carries the httpOnly cookie the sign-in set; no header is involved."""
    staff = _staff(ctx)
    _sign_in(ctx, staff.email)
    response = ctx.client.get("/api/v1/auth/me")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["email"] == staff.email


# --- only an access token is a session ----------------------------------------------------


@pytest.mark.parametrize("carrier", ["bearer", "cookie"])
def test_a_link_token_is_never_a_session(ctx: SimpleNamespace, carrier: str) -> None:
    """Every link token is validly signed, and every one of them is refused as a session."""
    staff = _staff(ctx)
    links = {
        "activation": security.create_activation_token(staff.id, staff.email),
        "password reset": security.create_password_reset_token(staff.id, staff.email),
        "email change": security.create_email_change_token(
            staff.id, "new@clinicq.example"
        ),
        "unsubscribe": security.create_unsubscribe_token(staff.email, "marketing"),
    }
    for kind, token in links.items():
        ctx.client.cookies.clear()
        if carrier == "bearer":
            response = ctx.client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            )
        else:
            ctx.client.cookies.set(ctx.settings.access_token_cookie_name, token)
            response = ctx.client.get("/api/v1/auth/me")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, kind


# --- an account switched off stops at once ------------------------------------------------


@pytest.mark.parametrize("switch_off", ["deactivated", "deleted"])
def test_an_account_switched_off_is_refused_while_its_token_is_still_valid(
    ctx: SimpleNamespace, switch_off: str
) -> None:
    """``/auth/me`` and an RBAC-gated route both answer 401 on the very next request."""
    admin = _staff(ctx, role=UserRole.ADMIN)
    headers = {"Authorization": f"Bearer {_sign_in(ctx, admin.email)}"}
    assert ctx.client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert (
        ctx.client.get("/api/v1/admin/rbac/users", headers=headers).status_code == 200
    )

    values = (
        {"is_active": False} if switch_off == "deactivated" else {"is_deleted": True}
    )
    with ctx.session() as db:
        db.execute(update(User).where(User.id == admin.id).values(**values))
        db.commit()

    # The same, still-unexpired token.
    assert jwt.decode(headers["Authorization"][7:], _SECRET, algorithms=["HS256"])
    assert ctx.client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert (
        ctx.client.get("/api/v1/admin/rbac/users", headers=headers).status_code == 401
    )


def test_a_switched_off_account_cannot_sign_back_in(ctx: SimpleNamespace) -> None:
    """Deactivation holds at the door too, not only on requests made with an old token."""
    staff = _staff(ctx, is_active=False)
    response = ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": staff.email, "password": FACTORY_STAFF_PASSWORD},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# --- passwords bcrypt cannot hash whole ---------------------------------------------------


@pytest.mark.parametrize("password", ["b" * 73, "é" * 40])
def test_a_new_password_over_72_bytes_is_a_422_not_a_500(
    ctx: SimpleNamespace, password: str
) -> None:
    """Counted in bytes: 40 accented letters are 80 bytes. Both reset and change refuse it."""
    staff = _staff(ctx)
    reset = ctx.client.post(
        "/api/v1/auth/password/reset",
        json={
            "token": security.create_password_reset_token(
                staff.id, staff.email, password_hash=staff.password
            ),
            "new_password": password,
        },
    )
    assert reset.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    _sign_in(ctx, staff.email)
    change = ctx.client.post(
        "/api/v1/auth/me/password",
        json={"current_password": FACTORY_STAFF_PASSWORD, "new_password": password},
        headers={"X-CSRF-Token": ctx.client.cookies.get(ctx.settings.csrf_cookie_name)},
    )
    assert change.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
