"""String-keyed enforcement + the generic ``require``/``require_action``/``require_management``
dependency factories (Issue #149, M26).

Mirrors ``test_rbac.py``'s coverage of the cumulative gate (no-op when auth is off, 401
unauthenticated, 403 under-privileged, cumulative + inherited grants) for the new string-keyed
twins that back :func:`src.api.rbac_deps.require` and friends, plus a direct test of the factory
callables themselves (FastAPI dependency functions) — the Applications pilot module wires
``require``/``require_management`` into real routes (covered by the existing
``tests/integration/applications`` suite unchanged), but ``require_action`` has no live call site
today (the ``approve`` named action was already unenforced before this migration), so it is proven
here at the dependency level instead of inventing new route behaviour.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

import asyncio
from collections.abc import Callable, Generator
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session, sessionmaker
from starlette import status

from src.commons.enums import GrantScope, PermissionAction, PermissionVerb, UserRole
from src.core import rbac
from src.core.rbac import (
    ensure_management_permission_key,
    ensure_named_action_permission_key,
    ensure_permission_key,
    seeded_grant_scope,
)
from src.database.models import RolePermission, User

_ADMIN = UserRole.ADMIN.value


_TENANT = UserRole.PATIENT.value


_ADMIN_EMAIL = "admin.keyed@example.com"


_TENANT_EMAIL = "tenant.keyed@example.com"


def _grant(
    db: Session,
    role: str,
    resource: str,
    verb: str,
    *,
    scope: GrantScope = GrantScope.BUSINESS,
) -> None:
    """Insert one cumulative ``(role, resource, max_verb, scope)`` grant.

    ``scope`` is stated rather than left to the column default: since Issue #172 that default is
    ``own`` for every role, so a fixture standing in for a seeded staff grant has to say
    ``business`` — which is the point of the flip, not a workaround for it.
    """
    db.add(
        RolePermission(
            role=role,
            resource=resource,
            max_verb=verb,
            scope=scope.value,
            created_at=datetime.now(UTC),
        )
    )


def _named_grant(db: Session, role: str, resource: str, action: str) -> None:
    """Insert one named-action ``(role, resource, action)`` grant."""
    db.add(
        RolePermission(
            role=role,
            resource=resource,
            action=action,
            max_verb=None,
            scope=seeded_grant_scope(role).value,
        )
    )


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Generator[Session]:
    """A session with an admin (broad grant) and a tenant (no grants) user."""
    with session_factory() as session:
        db_add = session.add
        db_add(User(email=_ADMIN_EMAIL, role=_ADMIN, is_verified=True))
        db_add(User(email=_TENANT_EMAIL, role=_TENANT, is_verified=True))
        # Admin holds DELETE on the "requests" root — should cascade to its
        # "application.screening" child via the enum's parent map fallback.
        _grant(
            session,
            _ADMIN,
            "requests",
            PermissionVerb.DELETE.value,
        )
        _named_grant(
            session,
            _ADMIN,
            "application.screening",
            PermissionAction.APPROVE.value,
        )
        session.commit()
        yield session


@pytest.fixture
def auth_on(monkeypatch: pytest.MonkeyPatch) -> Callable[[bool], None]:
    """Return a switch that forces ``settings.auth_enabled`` on or off for RBAC."""

    def _set(enabled: bool) -> None:
        monkeypatch.setattr(
            rbac, "get_settings", lambda: type("S", (), {"auth_enabled": enabled})()
        )

    return _set


def test_ensure_permission_key_noops_when_auth_disabled(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """With auth disabled the gate returns without raising, even for an anonymous principal."""
    auth_on(False)
    ensure_permission_key(db, {"sub": "anonymous"}, "requests", "delete")


def test_ensure_permission_key_allows_a_sufficient_grant(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """The admin's DELETE on ``applications`` satisfies a READ check (cumulative)."""
    auth_on(True)
    ensure_permission_key(db, {"email": _ADMIN_EMAIL}, "requests", "read")


def test_ensure_permission_key_forbids_an_ungranted_role(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """A tenant with no grant on ``applications`` is refused with 403."""
    auth_on(True)
    with pytest.raises(HTTPException) as exc:
        ensure_permission_key(db, {"email": _TENANT_EMAIL}, "requests", "read")
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["code"] == rbac.RBAC_FORBIDDEN_CODE
    assert exc.value.detail["resource"] == "requests"


def test_ensure_permission_key_401_for_anonymous(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """An anonymous principal is unauthenticated (401), not merely forbidden."""
    auth_on(True)
    with pytest.raises(HTTPException) as exc:
        ensure_permission_key(db, {"sub": "anonymous"}, "requests", "read")
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_ensure_management_permission_key_refuses_a_portal_only_role(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """An ``own``-tier caller is refused even with a hypothetical matching verb grant.

    The tier does the refusing, not the role's name — since Issue #172 there is no name check left
    to do it, and this grant would pass the gate if an admin had written ``business`` on it.
    """
    auth_on(True)
    _grant(
        db,
        _TENANT,
        "requests",
        PermissionVerb.DELETE.value,
        scope=GrantScope.OWN,
    )
    db.commit()
    with pytest.raises(HTTPException) as exc:
        ensure_management_permission_key(
            db, {"email": _TENANT_EMAIL}, "requests", "read"
        )
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


def test_ensure_management_permission_key_allows_a_management_role(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """An admin (a management role) holding the verb passes."""
    auth_on(True)
    ensure_management_permission_key(db, {"email": _ADMIN_EMAIL}, "requests", "read")


def test_ensure_named_action_permission_key_allows_the_granted_action(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """The admin's ``approve`` grant on ``application.screening`` is honoured by string key."""
    auth_on(True)
    ensure_named_action_permission_key(
        db, {"email": _ADMIN_EMAIL}, "application.screening", "approve"
    )


def test_ensure_named_action_permission_key_refuses_an_ungranted_action(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """A role with no named-action grant is refused with 403 (``required_action``)."""
    auth_on(True)
    with pytest.raises(HTTPException) as exc:
        ensure_named_action_permission_key(
            db, {"email": _TENANT_EMAIL}, "application.screening", "approve"
        )
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail["required_action"] == "approve"


def test_require_factory_dependency_allows_and_forbids(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """``require(...)`` returns a dependency behaving exactly like ``ensure_permission_key``."""
    from src.api.rbac_deps import require

    auth_on(True)
    dependency = require("requests", "read")
    asyncio.run(dependency(db, {"email": _ADMIN_EMAIL}))
    with pytest.raises(HTTPException):
        asyncio.run(dependency(db, {"email": _TENANT_EMAIL}))


def test_require_management_factory_dependency(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """``require_management(...)`` additionally refuses a portal-only role."""
    from src.api.rbac_deps import require_management

    auth_on(True)
    dependency = require_management("requests", "read")
    asyncio.run(dependency(db, {"email": _ADMIN_EMAIL}))
    with pytest.raises(HTTPException):
        asyncio.run(dependency(db, {"email": _TENANT_EMAIL}))


def test_require_action_factory_dependency(
    db: Session, auth_on: Callable[[bool], None]
) -> None:
    """``require_action(...)`` resolves the named-action grant, proving the third factory."""
    from src.api.rbac_deps import require_action

    auth_on(True)
    dependency = require_action("application.screening", "approve")
    asyncio.run(dependency(db, {"email": _ADMIN_EMAIL}))
    with pytest.raises(HTTPException):
        asyncio.run(dependency(db, {"email": _TENANT_EMAIL}))
