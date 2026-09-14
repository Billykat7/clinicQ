"""The queue engine's HTTP fixture: two listed clinics, their queues and people, on SQLite (M6).

    def test_something(desk):
        ticket = desk.staff("desk.a").post(desk.walk_in_path(desk.triage), json={}).json()
        patient, patient_id = desk.patient()
        patient.post(desk.join_path(desk.triage), json={})

Both clinics are **verified** (publicly listed) and **open around the clock** (every weekday
00:00–00:00), so a test about joining is never refused by the clock it happens to run at; a test
about a closed clinic announces a closure, which does not depend on the time of day either.

Clinic A runs Triage (``T``, remote joins allowed) and the Pharmacy (``P``, walk-ins only); clinic B
runs its own Triage. ``desk.a`` and ``desk.b`` are receptionists at each, and ``manager.a`` manages
clinic A. The snapshot cache is off,
so a join's write-through lands in the table only and nothing reaches a Redis.

What concurrency needs PostgreSQL for lives in the PostgreSQL-marked tests, which build their own.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from datetime import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, SiteStatus, UserRole
from src.commons.ids import new_id
from src.core import refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.database.models import Base, Queue, SiteOpeningHours
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app
from src.modules.queue.snapshot import NoSnapshotCache, set_snapshot_cache
from tests.factories import (
    FACTORY_STAFF_PASSWORD,
    PatientFactory,
    QueueFactory,
    SiteFactory,
    StaffFactory,
)

SITE_A = "0199b0c0-0000-7000-8000-0000000c1a01"
SITE_B = "0199b0c0-0000-7000-8000-0000000c1b01"
_SECRET = "queue-engine-test-secret-min-32-characters"


def queue_settings(**overrides: object) -> Settings:
    """Isolated settings with password sign-in on, unaffected by a developer's ``.env``."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_SECRET,
        auth_password_login_enabled=True,
        smtp_host="",
        **overrides,  # type: ignore[arg-type]
    )


def open_all_day(db: Session, site_id: str) -> None:
    """Every weekday 00:00–00:00: a span that crosses midnight, so the clinic never closes."""
    for weekday in range(7):
        db.add(
            SiteOpeningHours(
                site_id=site_id, weekday=weekday, opens_at=time(0), closes_at=time(0)
            )
        )


@pytest.fixture
def desk(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """Two open, listed clinics with queues and receptionists; clients for staff and patients."""
    settings = queue_settings()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    set_snapshot_cache(NoSnapshotCache())

    with factory() as db:
        sync_rbac_catalog(db)
        for site_id in (SITE_A, SITE_B):
            SiteFactory.create(db, id=site_id, status=SiteStatus.VERIFIED)
            open_all_day(db, site_id)
        triage = QueueFactory.create(
            db, site_id=SITE_A, name="Triage", slug="triage", ticket_prefix="T"
        )
        pharmacy = QueueFactory.create(
            db,
            site_id=SITE_A,
            name="Pharmacy",
            slug="pharmacy",
            ticket_prefix="P",
            allows_remote_join=False,
        )
        other_triage = QueueFactory.create(
            db, site_id=SITE_B, name="Triage", slug="triage", ticket_prefix="T"
        )
        for name, role, site_id in (
            ("desk.a", UserRole.RECEPTIONIST, SITE_A),
            ("desk.b", UserRole.RECEPTIONIST, SITE_B),
            ("manager.a", UserRole.CLINIC_MANAGER, SITE_A),
        ):
            StaffFactory.create(
                db, email=f"{name}@clinicq.example", role=role, site_id=site_id
            )
        db.commit()

    def _db() -> Generator[Session]:
        with factory() as db:
            yield db

    for module in (security, refresh_token_policy):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings

    def staff(name: str) -> TestClient:
        """A receptionist signed in, carrying the CSRF token on writes."""
        client = TestClient(app)
        signed_in = client.post(
            "/api/v1/auth/password/login",
            json={
                "email": f"{name}@clinicq.example",
                "password": FACTORY_STAFF_PASSWORD,
            },
        )
        assert signed_in.status_code == status.HTTP_200_OK, signed_in.text
        token = client.cookies.get(settings.csrf_cookie_name)
        if token:
            client.headers["X-CSRF-Token"] = token
        return client

    def patient(phone: str | None = None) -> tuple[TestClient, str]:
        """A client with a new patient's web session (bearer token), and the patient's id."""
        with factory() as db:
            record = (
                PatientFactory.create(db, phone_e164=phone)
                if phone
                else PatientFactory.create(db)
            )
            db.commit()
            token = security.create_patient_session_token(
                record.id, new_id(), record.session_version
            )
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {token}"
        return client, record.id

    def join_path(queue: Queue) -> str:
        return f"/api/v1/clinics/{queue.site_id}/queues/{queue.id}/tickets"

    def walk_in_path(queue: Queue) -> str:
        return f"/api/v1/sites/{queue.site_id}/queues/{queue.id}/tickets"

    yield SimpleNamespace(
        app=app,
        session=factory,
        settings=settings,
        staff=staff,
        patient=patient,
        join_path=join_path,
        walk_in_path=walk_in_path,
        triage=triage,
        pharmacy=pharmacy,
        other_triage=other_triage,
    )
    app.dependency_overrides.clear()
    set_snapshot_cache(None)
    Base.metadata.drop_all(engine)
    engine.dispose()
