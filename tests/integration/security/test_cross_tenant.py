"""A Clinic A token reaches nothing at Clinic B, and cannot tell that Clinic B exists (Issue 19).

Non-negotiable 3, over real HTTP, for every site-scoped resource that has a route:

* every Clinic B id answers **404, never 403**, and with the **same bytes** an id that never
  existed gets, so ids cannot be probed;
* the caller's own clinic answers 200, so the suite is discriminating rather than always-red;
* a cross-site **read** by a platform admin is possible only with an explicit reason header, and
  writes an audit row every time; without the header it is a 404 like anyone else's, and a
  cross-site **write** is refused even with one.

**A new resource type cannot be added without appearing here.** The suite discovers the site-scoped
surface two ways — every model carrying a ``site_id`` column, and every RBAC resource whose manifest
declares a site-family scope shape — and fails if one has neither a case below nor an entry in
:data:`PENDING`, which names the issue that will bring its routes. When that issue lands, its routes
appear and the ``PENDING`` entry has to become a case.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import (
    AppEnvironment,
    AssignmentScopeType,
    AuditAction,
    AuditEntityType,
    ScopeShape,
    UserRole,
)
from src.core import refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.core.rbac_manifest_registry import manifest_scope_shape_map
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.core.site_scope import CROSS_SITE_REASON_HEADER
from src.database.models import AuditEvent, Base, User, UserRoleAssignment
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app
from tests.factories import FACTORY_STAFF_PASSWORD, StaffFactory

_SECRET = "cross-tenant-test-secret-min-32-characters"
_SITE_A = "0199b0c0-0000-7000-8000-0000000000aa"
_SITE_B = "0199b0c0-0000-7000-8000-0000000000bb"
#: An id shaped exactly like a real one, belonging to nothing.
_NOWHERE = "0199b0c0-0000-7000-8000-0000000000ff"

#: The site-scoped surfaces whose routes exist, and how to probe each one.
#:
#: ``paths`` takes the site id and the id of a row at that site, so the same case can be pointed at
#: Clinic A (the caller's own) and at Clinic B (the one they must not see).
CASES: dict[str, dict[str, object]] = {
    "staff": {
        "resource": "sites.staff",
        "paths": lambda site, row: (
            f"/api/v1/sites/{site}/staff",
            f"/api/v1/sites/{site}/staff/{row}",
        ),
    },
}

#: Site-scoped surfaces with no route yet: the issue that brings them must add a case here.
PENDING: dict[str, str] = {
    "sites": "the sites model and its CRUD land with Issue 23",
    "sites.profile": "Issue 23",
    "sites.settings": "display and privacy settings land with Issue 27",
    "sites.display": "Issue 27",
    "sites.reports": "clinic reports land with M12",
    "queues": "queues land with Issue 25",
    "queues.call": "call-next lands with Issue 42",
    "queues.tickets": "tickets land with Issue 39",
    "queues.tickets.priority": "the priority override lands with Issue 46",
}


def _settings() -> Settings:
    """Isolated settings with auth and RBAC on."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_SECRET,
        auth_password_login_enabled=True,
        smtp_host="",
    )


@pytest.fixture
def clinics(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """Two clinics, a receptionist at each, a platform admin, and a client per person."""
    settings = _settings()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with factory() as db:
        sync_rbac_catalog(db)
        db.commit()

    def _db() -> Generator[Session]:
        with factory() as db:
            yield db

    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(refresh_token_policy, "get_settings", lambda: settings)
    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings

    people: dict[str, User] = {}
    with factory() as db:
        for name, role, site in (
            ("a", UserRole.RECEPTIONIST, _SITE_A),
            ("b", UserRole.RECEPTIONIST, _SITE_B),
            ("colleague_b", UserRole.NURSE_DOCTOR, _SITE_B),
        ):
            user = StaffFactory.create(
                db, email=f"{name}@clinicq.example", role=role, site_id=site
            )
            people[name] = user
        # The operator: every clinic through the tier, assigned to none of them.
        operator = StaffFactory.create(
            db, email="operator@clinicq.example", role=UserRole.PLATFORM_ADMIN
        )
        people["operator"] = operator
        db.commit()
        for user in people.values():
            db.refresh(user)
            db.expunge(user)

    def _client(email: str) -> TestClient:
        client = TestClient(app)
        response = client.post(
            "/api/v1/auth/password/login",
            json={"email": email, "password": FACTORY_STAFF_PASSWORD},
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return client

    yield SimpleNamespace(
        app=app, session=factory, people=people, client=_client, settings=settings
    )
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _paths(case: str, site: str, row: str) -> tuple[str, ...]:
    """The probe paths for one case, pointed at ``site``."""
    return CASES[case]["paths"](site, row)  # type: ignore[operator]


# --- the surface is covered ------------------------------------------------------------


def _site_scoped_surface() -> set[str]:
    """Every site-scoped thing that must be covered: models with ``site_id``, and RBAC resources."""
    models = {
        mapper.class_.__name__.lower()
        for mapper in Base.registry.mappers
        if "site_id" in mapper.columns
    }
    resources = {
        key
        for key, shape in manifest_scope_shape_map().items()
        if shape in (ScopeShape.SITE, ScopeShape.QUEUE)
    }
    return models | resources


def test_every_site_scoped_resource_is_covered_or_pending_with_its_issue() -> None:
    """A new site-scoped model or resource has to appear here, in a case or in PENDING."""
    covered = {str(case["resource"]) for case in CASES.values()} | set(CASES)
    uncovered = sorted(_site_scoped_surface() - covered - set(PENDING))
    assert not uncovered, (
        "site-scoped resources with no cross-tenant case and no PENDING entry: "
        f"{uncovered}. Add a case to CASES (preferred) or a PENDING entry naming the issue."
    )
    assert all(reason.strip() for reason in PENDING.values())


def test_a_pending_resource_that_gained_routes_must_become_a_case() -> None:
    """When the issue named in PENDING lands, its routes appear and the entry has to go."""
    from tests.unit.security.test_api_route_gates import _walk

    gated: set[str] = set()
    for _path, route in _walk(create_app().routes):
        for dependency in route.dependant.dependencies:
            gate = getattr(dependency.call, "__rbac_gate__", None)
            if gate:
                gated.add(gate[0])
    still_pending = sorted(set(PENDING) & gated)
    assert not still_pending, (
        f"these resources now gate a route and must have a cross-tenant case: {still_pending}"
    )


# --- the rule itself -------------------------------------------------------------------


@pytest.mark.parametrize("case", sorted(CASES))
def test_another_clinics_ids_are_not_found_and_look_exactly_like_ids_that_never_existed(
    clinics: SimpleNamespace, case: str
) -> None:
    """The heart of Issue 19: 404, never 403, and the same body for "not yours" and "no such id"."""
    receptionist_a = clinics.client("a@clinicq.example")
    colleague_b = clinics.people["colleague_b"].id

    for path in _paths(case, _SITE_B, colleague_b):
        response = receptionist_a.get(path)
        assert response.status_code == status.HTTP_404_NOT_FOUND, path
        nowhere = receptionist_a.get(path.replace(_SITE_B, _NOWHERE))
        assert nowhere.status_code == status.HTTP_404_NOT_FOUND
        # Identical answers, down to the body, apart from the per-request id.
        assert _without_request_id(response.json()) == _without_request_id(
            nowhere.json()
        )

    # And their own clinic works, so the suite is not simply refusing everything.
    own = clinics.people["a"].id
    for path in _paths(case, _SITE_A, own):
        assert receptionist_a.get(path).status_code == status.HTTP_200_OK, path


def _without_request_id(body: dict) -> dict:
    """The error body minus the per-request id, which is different on every response."""
    return {key: value for key, value in body.items() if key != "request_id"}


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_sequential_walk_of_another_clinics_ids_leaks_nothing(
    clinics: SimpleNamespace, case: str
) -> None:
    """Every id a probe could try answers identically: the real one, and made-up neighbours."""
    receptionist_a = clinics.client("a@clinicq.example")
    real = clinics.people["colleague_b"].id
    neighbours = [real[:-1] + digit for digit in "0123456789"]
    bodies = set()
    for candidate in [real, *neighbours]:
        response = receptionist_a.get(_paths(case, _SITE_B, candidate)[1])
        assert response.status_code == status.HTTP_404_NOT_FOUND
        bodies.add(str(_without_request_id(response.json())))
    assert len(bodies) == 1, "a probe could tell the real id from a made-up one"


def test_the_caller_sees_only_their_own_clinics_staff(clinics: SimpleNamespace) -> None:
    """The list is the site's, not the table's: Clinic B's people are not in Clinic A's list."""
    listing = clinics.client("a@clinicq.example").get(f"/api/v1/sites/{_SITE_A}/staff")
    assert listing.status_code == status.HTTP_200_OK
    emails = {member["email"] for member in listing.json()["items"]}
    assert emails == {"a@clinicq.example"}


# --- the platform-admin escape hatch ---------------------------------------------------


def test_a_platform_admin_reads_another_clinic_only_on_purpose_and_it_is_audited(
    clinics: SimpleNamespace,
) -> None:
    """No header, no cross-site read; with one, the read happens and an audit row records it."""
    operator = clinics.client("operator@clinicq.example")
    path = f"/api/v1/sites/{_SITE_B}/staff"

    assert operator.get(path).status_code == status.HTTP_404_NOT_FOUND
    with clinics.session() as db:
        assert db.execute(select(AuditEvent)).scalars().all() == []

    allowed = operator.get(
        path, headers={CROSS_SITE_REASON_HEADER: "support ticket 4821"}
    )

    assert allowed.status_code == status.HTTP_200_OK
    assert {m["email"] for m in allowed.json()["items"]} == {
        "b@clinicq.example",
        "colleague_b@clinicq.example",
    }
    with clinics.session() as db:
        rows = db.execute(select(AuditEvent)).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.action == AuditAction.READ.value
    assert row.entity_type == AuditEntityType.SITE.value
    assert row.entity_id == _SITE_B
    assert row.actor == "operator@clinicq.example"
    assert "support ticket 4821" in (row.context or "")

    # Every cross-site read, not just the first.
    operator.get(path, headers={CROSS_SITE_REASON_HEADER: "support ticket 4822"})
    with clinics.session() as db:
        assert len(db.execute(select(AuditEvent)).scalars().all()) == 2


def test_the_hatch_is_read_only_and_only_for_a_business_tier_grant(
    clinics: SimpleNamespace,
) -> None:
    """A reason header does not open a write, and it does nothing at all for a receptionist."""
    operator = clinics.client("operator@clinicq.example")
    header = {
        CROSS_SITE_REASON_HEADER: "support ticket 4823",
        # The session's own CSRF token, so this probes the site guard and not the CSRF check.
        "X-CSRF-Token": operator.cookies.get(clinics.settings.csrf_cookie_name),
    }
    # A write to another clinic's staff: refused, even with a reason. (The route is Issue 22's;
    # what is asserted here is the guard's rule, so it cannot be a 200 when that route lands.)
    write = operator.post(f"/api/v1/sites/{_SITE_B}/staff", json={}, headers=header)
    assert write.status_code in (
        status.HTTP_404_NOT_FOUND,
        status.HTTP_405_METHOD_NOT_ALLOWED,
    )

    receptionist_a = clinics.client("a@clinicq.example")
    assert (
        receptionist_a.get(f"/api/v1/sites/{_SITE_B}/staff", headers=header).status_code
        == status.HTTP_404_NOT_FOUND
    )
    with clinics.session() as db:
        assert db.execute(select(AuditEvent)).scalars().all() == []


def test_a_role_held_at_one_clinic_is_not_held_at_another(
    clinics: SimpleNamespace,
) -> None:
    """RBAC resolves the roles held *at the site in the path*, so a manager elsewhere is nobody here.

    The manager below manages Clinic B and is a receptionist at Clinic A: at Clinic A they may read
    the staff list and not manage it, whatever they may do at B.
    """
    with clinics.session() as db:
        manager = StaffFactory.create(
            db,
            email="manager@clinicq.example",
            role=UserRole.CLINIC_MANAGER,
            site_id=_SITE_B,
        )
        db.add(
            UserRoleAssignment(
                user_id=manager.id,
                role=UserRole.RECEPTIONIST.value,
                scope_type=AssignmentScopeType.SITE.value,
                scope_id=_SITE_A,
            )
        )
        db.commit()
    client = clinics.client("manager@clinicq.example")

    assert (
        client.get(f"/api/v1/sites/{_SITE_A}/staff").status_code == status.HTTP_200_OK
    )
    assert (
        client.get(f"/api/v1/sites/{_SITE_B}/staff").status_code == status.HTTP_200_OK
    )
    at_a = client.get(f"/api/v1/sites/{_SITE_A}/staff").json()["items"]
    assert {member["email"] for member in at_a} == {
        "a@clinicq.example",
        "manager@clinicq.example",
    }
    roles_at_a = next(
        m["roles"] for m in at_a if m["email"] == "manager@clinicq.example"
    )
    assert roles_at_a == [UserRole.RECEPTIONIST.value]  # not their Clinic B role
