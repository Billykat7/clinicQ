"""RBAC resource-expansion gating tests (Issue 125 / M22).

Proves the acceptance criteria the issue pins, over the real HTTP stack against an in-memory DB:

* **Communications gates the notification centre.** A role granted the ``communications`` resource
  (or its ``communications.notifications`` section) reaches the per-user centre; a role with no
  such grant is refused ``403`` — the centre is no longer open to every signed-in session.
* **Section sub-resources scope an action, and a coarse grant inherits them.** A role scoped to only
  ``payment.reversals`` may reverse a payment but is refused *recording* (``payment.ledger``), while
  a role scoped to only ``payment.ledger`` is refused *reversal* — the negative direction. A role
  holding the coarse ``payments`` grant is admitted to both sections by inheritance, so existing
  access is preserved.

The RBAC verb gate runs as a route dependency *before* the handler, so a permitted caller gets
past it (then a ``404`` for the deliberately-absent lease) while a refused caller gets ``403``.
Asserting "403 vs not-403" isolates the permission decision from the domain logic and needs no full
payment lifecycle. Per ``.cursor/rules/testing-strategy.mdc`` the assertions are status codes only,
``Settings`` are built with ``_env_file=None``, and ``smtp_host`` is blanked so nothing sends.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Callable, Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, PermissionVerb, UserRole
from src.core import security
from src.core.config import Settings, get_settings
from src.core.rbac import seeded_grant_scope
from src.core.security import create_access_token
from src.database.models import Base, RbacRole, RolePermission, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "rbac-resource-expansion-m22-secret-32chars!!"


_CENTER = "/api/v1/notifications/center/unread-count"


_ADMIN_EMAIL = "admin.m22@example.com"


# Custom roles, each carrying exactly one grant so the section boundary is unambiguous.
_ROLE_NOTIF = "comms_notif_only"  # communications.notifications READ


_ROLE_NO_COMMS = "no_comms"  # a grant, but none on communications


_ROLE_REVERSALS = "reversals_only"  # payment.reversals DELETE


_ROLE_LEDGER = "ledger_only"  # payment.ledger CREATE


_ROLE_PAYMENTS = "payments_coarse"  # payments DELETE (inherits every section)


def _settings(**overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with RBAC on and mail forced to no-op."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_enabled": True,
        "smtp_host": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def make_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Callable[..., SimpleNamespace]]:
    """Factory: a ``TestClient`` wired to an isolated in-memory DB."""
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


def _grant(role: str, resource: str, verb: PermissionVerb) -> RolePermission:
    return RolePermission(
        role=role,
        resource=resource,
        max_verb=verb.value,
        scope=seeded_grant_scope(role).value,
    )


def _seed(factory: sessionmaker[Session]) -> None:
    """Seed one user per custom role, each carrying exactly the grant its name describes."""
    with factory() as db:
        db.add_all(
            [
                RbacRole(name=_ROLE_NOTIF, description="notif", is_system=False),
                RbacRole(name=_ROLE_NO_COMMS, description="none", is_system=False),
                RbacRole(name=_ROLE_REVERSALS, description="rev", is_system=False),
                RbacRole(name=_ROLE_LEDGER, description="led", is_system=False),
                RbacRole(name=_ROLE_PAYMENTS, description="pay", is_system=False),
            ]
        )
        db.add_all(
            [
                _grant(
                    _ROLE_NOTIF,
                    "communications.notifications",
                    PermissionVerb.READ,
                ),
                # A grant on an unrelated resource, so the role is authenticated-with-grants but
                # holds nothing on communications — the negative case.
                _grant(_ROLE_NO_COMMS, "logs", PermissionVerb.READ),
                _grant(
                    _ROLE_REVERSALS,
                    "payment.reversals",
                    PermissionVerb.DELETE,
                ),
                _grant(
                    _ROLE_LEDGER,
                    "payment.ledger",
                    PermissionVerb.CREATE,
                ),
                _grant(_ROLE_PAYMENTS, "invoices", PermissionVerb.DELETE),
            ]
        )
        db.add_all(
            [
                User(email=_ADMIN_EMAIL, role=UserRole.ADMIN.value, is_verified=True),
                User(email=f"{_ROLE_NOTIF}@x.com", role=_ROLE_NOTIF, is_verified=True),
                User(
                    email=f"{_ROLE_NO_COMMS}@x.com",
                    role=_ROLE_NO_COMMS,
                    is_verified=True,
                ),
                User(
                    email=f"{_ROLE_REVERSALS}@x.com",
                    role=_ROLE_REVERSALS,
                    is_verified=True,
                ),
                User(
                    email=f"{_ROLE_LEDGER}@x.com", role=_ROLE_LEDGER, is_verified=True
                ),
                User(
                    email=f"{_ROLE_PAYMENTS}@x.com",
                    role=_ROLE_PAYMENTS,
                    is_verified=True,
                ),
            ]
        )
        db.commit()


def test_communications_notifications_grant_reaches_the_centre(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A role holding ``communications.notifications`` READ may read its own centre badge."""
    ctx = make_client()
    _seed(ctx.session)
    resp = ctx.client.get(_CENTER, headers=_bearer(f"{_ROLE_NOTIF}@x.com"))
    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.json()["unread_total"] == 0


def test_centre_is_refused_without_a_communications_grant(
    make_client: Callable[..., SimpleNamespace],
) -> None:
    """A signed-in role with no communications grant is refused the centre (403), not served 0."""
    ctx = make_client()
    _seed(ctx.session)
    resp = ctx.client.get(_CENTER, headers=_bearer(f"{_ROLE_NO_COMMS}@x.com"))
    assert resp.status_code == status.HTTP_403_FORBIDDEN
