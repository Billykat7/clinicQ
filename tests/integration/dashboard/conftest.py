"""The clinic dashboard's fixture: two open clinics, their queues, and one person per job (M7).

    def test_something(dashboard):
        desk = dashboard.client("desk.a")
        assert desk.get(dashboard.page(dashboard.site_a, "board")).status_code == 200

Both clinics are verified and open around the clock, like the queue engine's fixture, so nothing here
depends on the time the suite runs at. Clinic A runs Triage (``T``, Room 2) and the Pharmacy (``P``);
clinic B runs its own Triage.

Who works where is the point of the fixture, and it is held the way production holds it: a role **at
a site** (``user_roles`` with ``scope_type='site'``), and a room as a queue assignment written by the
staff module (Issue 28), never a column on the user.

========== ======================= ===================================================
person     role                    where
========== ======================= ===================================================
desk.a     receptionist            clinic A
nurse.a    nurse or doctor         clinic A, assigned to Triage (Room 2) only
manager.a  clinic manager          clinic A
desk.b     receptionist            clinic B
both       receptionist, manager   receptionist at clinic A, clinic manager at clinic B
trainee    front desk trainee      clinic A; a custom role that reads the queues and changes nothing
operator   platform administrator  no clinic (unscoped)
========== ======================= ===================================================

The trainee's role exists only as grants (``dashboard`` and ``queues.tickets`` at ``read``), the way an
operator would build one in the access console: it is what a screen shows someone whose grants stop
short of a control.

``dashboard.client(name)`` signs in and returns a ``TestClient`` carrying the CSRF token on writes, so
a refusal in a test is an authorization answer, never a missing header.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from datetime import UTC, datetime, time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import (
    AppEnvironment,
    AssignmentScopeType,
    GrantScope,
    PermissionVerb,
    SiteStatus,
    UserRole,
)
from src.commons.ids import new_id
from src.core import email_send, refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.core.site_scope import SiteAccess
from src.database.models import (
    Base,
    RbacRole,
    RolePermission,
    SiteOpeningHours,
    UserRoleAssignment,
)
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app
from src.modules.notifications import dev_outbox
from src.modules.queue.snapshot import NoSnapshotCache, set_snapshot_cache
from src.modules.staff.assignments import set_room_assignments
from tests.factories import (
    FACTORY_STAFF_PASSWORD,
    PatientFactory,
    QueueFactory,
    SiteFactory,
    StaffFactory,
)

SITE_A = "0199b0c0-0000-7000-8000-0000000d0a01"
SITE_B = "0199b0c0-0000-7000-8000-0000000d0b01"
_SECRET = "dashboard-test-secret-min-32-characters!"
EMAIL_DOMAIN = "clinicq.example"
#: A role built from grants alone, as an operator would in the access console.
TRAINEE_ROLE = "front_desk_trainee"


def dashboard_settings(**overrides: object) -> Settings:
    """Isolated settings with password sign-in on, unaffected by a developer's ``.env``."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_SECRET,
        auth_password_login_enabled=True,
        smtp_host="",
        **overrides,  # type: ignore[arg-type]
    )


def _open_all_day(db: Session, site_id: str) -> None:
    """Every weekday 00:00–00:00: a span that crosses midnight, so the clinic never closes."""
    for weekday in range(7):
        db.add(
            SiteOpeningHours(
                site_id=site_id, weekday=weekday, opens_at=time(0), closes_at=time(0)
            )
        )


@pytest.fixture
def dashboard(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """Two open clinics with queues and staff; a signed-in client per person."""
    settings = dashboard_settings()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    set_snapshot_cache(NoSnapshotCache())

    with factory() as db:
        sync_rbac_catalog(db)
        db.add(
            RbacRole(
                name=TRAINEE_ROLE, description="Front desk trainee.", is_system=False
            )
        )
        for resource, scope in (
            ("dashboard", GrantScope.BUSINESS),
            ("queues.tickets", GrantScope.ASSIGNED),
        ):
            db.add(
                RolePermission(
                    role=TRAINEE_ROLE,
                    resource=resource,
                    max_verb=PermissionVerb.READ.value,
                    scope=scope.value,
                    created_at=datetime.now(UTC),
                )
            )
        db.flush()
        SiteFactory.create(
            db, id=SITE_A, name="Zola Clinic", status=SiteStatus.VERIFIED
        )
        SiteFactory.create(
            db, id=SITE_B, name="Alex Clinic", status=SiteStatus.VERIFIED
        )
        for site_id in (SITE_A, SITE_B):
            _open_all_day(db, site_id)
        triage = QueueFactory.create(
            db,
            site_id=SITE_A,
            name="Triage",
            slug="triage",
            ticket_prefix="T",
            room_label="Room 2",
        )
        pharmacy = QueueFactory.create(
            db, site_id=SITE_A, name="Pharmacy", slug="pharmacy", ticket_prefix="P"
        )
        other_triage = QueueFactory.create(
            db, site_id=SITE_B, name="Triage", slug="triage", ticket_prefix="T"
        )
        people = {}
        for name, role, site_id in (
            ("desk.a", UserRole.RECEPTIONIST, SITE_A),
            ("nurse.a", UserRole.NURSE_DOCTOR, SITE_A),
            ("manager.a", UserRole.CLINIC_MANAGER, SITE_A),
            ("desk.b", UserRole.RECEPTIONIST, SITE_B),
            ("both", UserRole.RECEPTIONIST, SITE_A),
            ("trainee", TRAINEE_ROLE, SITE_A),
            ("operator", UserRole.PLATFORM_ADMIN, None),
        ):
            people[name] = StaffFactory.create(
                db,
                email=f"{name}@{EMAIL_DOMAIN}",
                first_name=name.split(".")[0].title(),
                role=role,
                site_id=site_id,
            )
        # ``both`` manages clinic B as well: a second role, held at the second clinic only.
        db.add(
            UserRoleAssignment(
                user_id=people["both"].id,
                role=UserRole.CLINIC_MANAGER.value,
                scope_type=AssignmentScopeType.SITE.value,
                scope_id=SITE_B,
            )
        )
        db.flush()
        set_room_assignments(
            db,
            SiteAccess(site_id=SITE_A, user=people["manager.a"]),
            people["nurse.a"].id,
            [triage.id],
        )
        db.commit()
        ids = {name: person.id for name, person in people.items()}

    def _db() -> Generator[Session]:
        with factory() as db:
            yield db

    # ``email_send`` and ``dev_outbox`` read settings directly, so they must read the test's own:
    # this fixture sets ``smtp_host=""`` precisely so a staff invitation goes to the development
    # outbox, and without the patch they read a developer's ``.env`` instead and send it for real.
    # Issue 220's SMTP guard in ``tests/conftest.py`` is what caught that.
    for module in (security, refresh_token_policy, email_send, dev_outbox):
        monkeypatch.setattr(module, "get_settings", lambda: settings)

    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings

    def _client(name: str) -> TestClient:
        """Sign in as ``name`` and return a client that carries the CSRF token on writes."""
        client = TestClient(app, follow_redirects=False)
        signed_in = client.post(
            "/api/v1/auth/password/login",
            json={
                "email": f"{name}@{EMAIL_DOMAIN}",
                "password": FACTORY_STAFF_PASSWORD,
            },
        )
        assert signed_in.status_code == status.HTTP_200_OK, signed_in.text
        token = client.cookies.get(settings.csrf_cookie_name)
        if token:
            client.headers["X-CSRF-Token"] = token
        return client

    def _patient() -> tuple[TestClient, str]:
        """A client with a new patient's web session (bearer token), and the patient's id."""
        with factory() as db:
            record = PatientFactory.create(db)
            db.commit()
            token = security.create_patient_session_token(
                record.id, new_id(), record.session_version
            )
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {token}"
        return client, record.id

    def _page(site_id: str, section: str = "") -> str:
        """A clinic page's path: ``/dashboard/sites/{site_id}`` plus ``/section`` when given."""
        base = f"/dashboard/sites/{site_id}"
        return f"{base}/{section}" if section else base

    yield SimpleNamespace(
        app=app,
        session=factory,
        settings=settings,
        client=_client,
        anonymous=lambda: TestClient(app, follow_redirects=False),
        patient=_patient,
        page=_page,
        site_a=SITE_A,
        site_b=SITE_B,
        triage=triage.id,
        pharmacy=pharmacy.id,
        other_triage=other_triage.id,
        ids=ids,
    )
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()
