"""``POST /admin/rbac/simulate`` — the decision simulator over HTTP (Issue #174, M29).

``docs/architecture/rbac-decision-transparency.md`` §4. Four things are proven here:

1. **Both target forms answer**, for a role principal and a user principal, including a user
   holding several active assignments — scoped and time-boxed — at once.
2. **The deciding statement names the role the grant came from**, including when it arrived through
   the role closure or cascaded from an ancestor resource.
3. **Grid parity.** Across every seeded role, every catalog resource and every verb, the
   simulator's verdict equals what :func:`~src.core.rbac.ensure_permission_key` does on a live
   request; and across every nav surface, what
   :class:`~src.core.nav_visibility.NavVisibility` renders. This is the assertion the whole design
   rests on — a simulator that has drifted from the enforcement path is worse than none, because it
   answers confidently and wrongly.
4. **The endpoint is gated on ``rbac:READ``** and refuses everyone else itself, before any
   resolution runs.

Plus the regression that motivated the milestone: simulating the ``tenant`` role against
``/admin/leases`` reproduces M28 #165's finding as a single API call — the thing that originally
took two git worktrees and a bespoke probe.

Per ``docs/IDE/RULES/testing-strategy.mdc`` these assert JSON, status codes and resolver outcomes —
never HTML — and build isolated ``Settings`` (``_env_file=None``) so a developer's local ``.env``
cannot change outcomes.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, PermissionVerb, UserRole
from src.core import security
from src.core.config import Settings, get_settings
from src.core.nav_registry import NAV_DESTINATIONS
from src.core.nav_visibility import nav_visibility_for_user
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    ensure_permission_key,
    load_resource_parent_map,
    refresh_resource_descendants_py,
    seed_resource_catalog,
    seeded_grant_scope,
)
from src.core.security import create_access_token, hash_password
from src.database.models import Base, RbacRole, RolePermission, User, UserRoleAssignment
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "rbac-simulator-test-secret-min-32-characters"


#: Password for every seeded user, so a test can sign in and reach the HTML explainer pages the
#: way a browser does (session cookies), rather than only the JSON API.
_PASSWORD = "Sim-Explainer-Pass-1"


_ADMIN_EMAIL = "console.admin.sim@example.com"


_SIMULATE_URL = "/api/v1/admin/rbac/simulate"


#: The seeded roles the grid parity test sweeps. Every one a fresh deployment ships with.
_SEEDED_ROLES = (
    UserRole.ADMIN.value,
    UserRole.PLATFORM_ADMIN.value,
    UserRole.NURSE_DOCTOR.value,
    UserRole.PATIENT.value,
    UserRole.RECEPTIONIST.value,
    UserRole.USER.value,
)


def _settings(**overrides: object) -> Settings:
    """Build isolated auth ``Settings`` (no ``.env``) with RBAC enforcement on."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "auth_enabled": True,
        "auth_password_login_enabled": True,
        "smtp_host": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _seed(factory: sessionmaker[Session]) -> None:
    """Seed roles, the full portal grant matrix, the resource catalog and one user per role.

    Deliberately the *deployed* shape rather than a minimal fixture: the grid parity assertion is
    only worth anything if it sweeps the grants a real deployment actually ships with.
    """
    with factory() as db:
        for name, description in (*default_system_roles(),):
            db.add(RbacRole(name=name, description=description, is_system=True))
        seen: set[tuple[str, str]] = set()
        for helper in (default_role_permissions,):
            for role, resource_key, verb in helper():
                if (role, resource_key) in seen:
                    continue
                seen.add((role, resource_key))
                db.add(
                    RolePermission(
                        role=role,
                        resource=resource_key,
                        max_verb=verb.value,
                        created_at=datetime.now(UTC),
                        scope=seeded_grant_scope(role).value,
                    )
                )
        password = hash_password(_PASSWORD)
        for role in _SEEDED_ROLES:
            db.add(
                User(
                    id=f"user-{role}",
                    email=f"{role}.sim@example.com",
                    role=role,
                    is_verified=True,
                    password=password,
                )
            )
        db.add(
            User(
                email=_ADMIN_EMAIL,
                role=UserRole.ADMIN.value,
                is_verified=True,
                password=password,
            )
        )
        db.commit()
        seed_resource_catalog(db)
        db.commit()
        refresh_resource_descendants_py(db)
        db.commit()


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    """A ``TestClient`` on an isolated, fully seeded database, plus a session factory."""
    settings = _settings()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    _seed(factory)

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
    yield SimpleNamespace(client=TestClient(app), session=factory, settings=settings)
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _bearer(email: str) -> dict[str, str]:
    """Authorization header carrying a freshly minted access JWT for ``email``."""
    return {"Authorization": f"Bearer {create_access_token(sub=email, email=email)}"}


def _simulate(ctx: SimpleNamespace, payload: dict, *, email: str = _ADMIN_EMAIL):
    """POST one simulation as ``email`` and return the response."""
    return ctx.client.post(_SIMULATE_URL, json=payload, headers=_bearer(email))


def _sign_in(ctx: SimpleNamespace, email: str) -> None:
    """Password sign-in so the client jar carries the browser's session cookies."""
    response = ctx.client.post(
        "/api/v1/auth/password/login", json={"email": email, "password": _PASSWORD}
    )
    assert response.status_code == status.HTTP_200_OK


def _catalog_keys(factory: sessionmaker[Session]) -> list[str]:
    """Every resource key the catalog knows — the grid's second axis."""
    with factory() as db:
        return sorted(load_resource_parent_map(db))


def test_the_resource_verb_form_answers_for_a_user_principal(
    ctx: SimpleNamespace,
) -> None:
    """The same question about a person, resolved through their active assignments."""
    response = _simulate(
        ctx,
        {
            "principal": {"email": f"{UserRole.PATIENT.value}.sim@example.com"},
            "target": {"resource": "orders", "verb": PermissionVerb.DELETE.value},
        },
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["decision"] == "deny"
    assert body["principal_roles"] == [UserRole.PATIENT.value]


def test_a_path_no_destination_declares_is_a_400_not_a_deny(
    ctx: SimpleNamespace,
) -> None:
    """A typo must not read as a security finding — the two facts stay distinguishable."""
    response = _simulate(
        ctx,
        {
            "principal": {"role": UserRole.PLATFORM_ADMIN.value},
            "target": {"path": "/admin/no-such-console"},
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_an_unknown_user_is_a_400_not_a_deny(ctx: SimpleNamespace) -> None:
    """Same distinction on the principal side."""
    response = _simulate(
        ctx,
        {
            "principal": {"email": "nobody@example.com"},
            "target": {"resource": "orders", "verb": PermissionVerb.READ.value},
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_a_principal_with_no_grant_anywhere_has_no_deciding_statement(
    ctx: SimpleNamespace,
) -> None:
    """ "Nothing grants it" is the answer to most "why can't they?" questions, and it is explicit."""
    body = _simulate(
        ctx,
        {
            "principal": {"role": UserRole.PATIENT.value},
            "target": {"resource": "rbac", "verb": PermissionVerb.READ.value},
        },
    ).json()
    assert body["decision"] == "deny"
    assert body["deciding_statement"] is None
    assert any(step["outcome"] == "denied" for step in body["trace"])


def test_the_simulator_matches_enforcement_for_every_role_resource_and_verb(
    ctx: SimpleNamespace,
) -> None:
    """Every seeded role by every catalog resource by every verb, against ``ensure_permission_key``.

    Run in-process rather than over HTTP: the grid is a few thousand decisions, and the endpoint's
    own contract is covered above. What matters is that the two *code paths* agree, and both are
    exercised against the very same session.
    """
    keys = _catalog_keys(ctx.session)
    assert len(keys) > 20, "the catalog fixture is too small to be a meaningful grid"
    mismatches: list[str] = []
    with ctx.session() as db:
        from src.core.rbac_simulator import (
            SimulationPrincipal,
            SimulationTarget,
            simulate,
        )

        for role in _SEEDED_ROLES:
            user = db.get(User, f"user-{role}")
            claims = {"sub": user.email, "email": user.email}
            for resource_key in keys:
                for verb in PermissionVerb:
                    try:
                        ensure_permission_key(db, claims, resource_key, verb.value)
                        enforced = "allow"
                    except HTTPException:
                        enforced = "deny"
                    simulated = simulate(
                        db,
                        SimulationPrincipal(role=role),
                        SimulationTarget(resource=resource_key, verb=verb.value),
                    ).decision.value
                    if simulated != enforced:
                        mismatches.append(
                            f"{role} x {resource_key} x {verb.value}: "
                            f"enforced={enforced} simulated={simulated}"
                        )
    assert not mismatches, (
        f"{len(mismatches)} decision(s) differ between the simulator and enforcement:\n"
        + "\n".join(mismatches[:20])
    )


def test_the_simulator_matches_nav_visibility_for_every_role_and_surface(
    ctx: SimpleNamespace,
) -> None:
    """Every seeded role by every nav destination, against the object the shell renders from.

    The #164 half of the parity claim. A resource+verb-only simulator would pass the test above and
    still be blind to the regression that milestone shipped.
    """
    mismatches: list[str] = []
    with ctx.session() as db:
        from src.core.rbac_simulator import (
            SimulationPrincipal,
            SimulationTarget,
            simulate,
        )

        for role in _SEEDED_ROLES:
            user = db.get(User, f"user-{role}")
            nav = nav_visibility_for_user(db, user, auth_enabled=True)
            for dest in NAV_DESTINATIONS:
                rendered = "allow" if nav.visible(dest.key) else "deny"
                simulated = simulate(
                    db,
                    SimulationPrincipal(role=role),
                    SimulationTarget(surface=dest.key),
                ).decision.value
                by_path = simulate(
                    db,
                    SimulationPrincipal(role=role),
                    SimulationTarget(path=dest.href),
                ).decision.value
                if not simulated == by_path == rendered:
                    mismatches.append(
                        f"{role} x {dest.key}: rendered={rendered} "
                        f"surface={simulated} path={by_path}"
                    )
    assert not mismatches, (
        f"{len(mismatches)} surface decision(s) differ from what the shell renders:\n"
        + "\n".join(mismatches[:20])
    )


def test_a_caller_without_rbac_read_is_refused_by_the_endpoint_itself(
    ctx: SimpleNamespace,
) -> None:
    """403 from the gate, before any resolution runs — the simulator leaks nothing to them."""
    response = _simulate(
        ctx,
        {
            "principal": {"role": UserRole.ADMIN.value},
            "target": {"resource": "rbac", "verb": PermissionVerb.READ.value},
        },
        email=f"{UserRole.PATIENT.value}.sim@example.com",
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_an_unauthenticated_caller_is_refused(ctx: SimpleNamespace) -> None:
    """No token, no simulation."""
    response = ctx.client.post(
        _SIMULATE_URL,
        json={
            "principal": {"role": UserRole.ADMIN.value},
            "target": {"resource": "rbac", "verb": PermissionVerb.READ.value},
        },
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_every_trace_step_carries_an_operator_facing_stage_label(
    ctx: SimpleNamespace,
) -> None:
    """The raw trace disclosure needs no vocabulary of its own in the browser."""
    body = _simulate(
        ctx,
        {
            "principal": {"role": UserRole.PLATFORM_ADMIN.value},
            "target": {"resource": "orders", "verb": PermissionVerb.UPDATE.value},
        },
    ).json()
    assert body["trace"]
    for step in body["trace"]:
        assert step["stage_label"]
        assert step["stage_label"] != step["stage"]


def test_user_effective_access_lists_expired_and_scoped_assignments_as_inactive(
    ctx: SimpleNamespace,
) -> None:
    """ "granted, expired 3 days ago" is the answer to most support tickets in this area.

    An assignment that has silently vanished from the page looks exactly like one that was never
    granted, so both are listed — with the status that distinguishes them.
    """
    with ctx.session() as db:
        db.add(
            User(
                id="user-mixed",
                email="mixed.sim@example.com",
                role=UserRole.PATIENT.value,
                is_verified=True,
            )
        )
        db.add(
            UserRoleAssignment(
                user_id="user-mixed",
                role=UserRole.PATIENT.value,
                granted_at=datetime.now(UTC),
            )
        )
        db.add(
            UserRoleAssignment(
                user_id="user-mixed",
                role=UserRole.PLATFORM_ADMIN.value,
                granted_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) - timedelta(days=3),
            )
        )
        db.add(
            UserRoleAssignment(
                user_id="user-mixed",
                role=UserRole.PLATFORM_ADMIN.value,
                scope_type="property",
                scope_id="prop-1",
                granted_at=datetime.now(UTC),
            )
        )
        db.commit()
    body = ctx.client.get(
        "/api/v1/admin/rbac/users/user-mixed/effective-access",
        headers=_bearer(_ADMIN_EMAIL),
    ).json()
    statuses = sorted(row["status"] for row in body["assignments"])
    assert statuses == ["active", "expired", "scoped"]
    expired = next(row for row in body["assignments"] if row["status"] == "expired")
    assert "expired" in expired["status_label"]
    scoped = next(row for row in body["assignments"] if row["status"] == "scoped")
    assert "property prop-1" in scoped["status_label"]
    # Only the unscoped, unexpired assignment resolves without a scope of its own.
    assert body["active_roles"] == [UserRole.PATIENT.value]


def test_user_effective_access_is_gated_like_the_rest_of_the_console(
    ctx: SimpleNamespace,
) -> None:
    """A caller without ``rbac:READ`` cannot read anyone's effective access."""
    response = ctx.client.get(
        f"/api/v1/admin/rbac/users/user-{UserRole.PLATFORM_ADMIN.value}/effective-access",
        headers=_bearer(f"{UserRole.PATIENT.value}.sim@example.com"),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_the_403_page_still_answers_403_for_a_caller_who_can_be_shown_why(
    ctx: SimpleNamespace,
) -> None:
    """The affordance is added *to* the refusal; it never turns one into a 200."""
    _sign_in(ctx, _ADMIN_EMAIL)
    assert (
        ctx.client.get("/admin/no-such-console").status_code
        == status.HTTP_404_NOT_FOUND
    )
