"""The M5 fixture: the real demo clinics in a migrated PostGIS database, and an app that reads it.

Discovery is a PostGIS feature (``ST_DWithin`` on a ``geography`` column), so unlike the M4 suite
these tests cannot run on SQLite. Every test here gets a database that did not exist a moment ago,
brought to ``head`` by the real migrations (``migrated_engine`` in ``tests/conftest.py``), with the
eleven demo clinics from ``scripts/db/demo_dataset.py``, all verified, each with its queues and a
weekday schedule.

    def test_something(directory):
        page = directory.client.get("/api/v1/clinics/nearby", params={...}).json()
        with directory.session() as db:
            find_nearby_sites(db, JOHANNESBURG)

The app is the real one (``create_app``) with only ``get_db`` pointed at the throwaway database.
"""

from collections.abc import Generator, Iterator
from datetime import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from scripts.db.demo_dataset import CLINICS, queues_for
from src.commons.enums import AppEnvironment, SiteStatus
from src.commons.geo import Coordinates
from src.commons.ids import new_id
from src.core import security
from src.core.config import Settings, get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.database.models import SiteOpeningHours
from src.database.session import get_db
from src.main import create_app
from src.modules.sites.catalogue import seed_default_catalogue
from tests.factories import PatientFactory, QueueFactory, SiteFactory

#: The Johannesburg city centre: Hillbrow CHC is about 1.4 km away, Medicross Melville about 5 km,
#: the Soweto clinics 14–16 km, Pretoria 47 km and more, and Durban nearly 500 km.
JOHANNESBURG = Coordinates(latitude=-26.20500, longitude=28.04000)

#: Monday to Friday, 07:00–16:00: the demo dataset's hours, as real rows.
WEEKDAYS = range(5)


def add_verified_clinic(db: Session, **overrides: object) -> str:
    """Create one verified clinic with a weekday schedule and return its id. Flushed only."""
    site = SiteFactory.create(db, status=SiteStatus.VERIFIED, **overrides)
    for weekday in WEEKDAYS:
        db.add(
            SiteOpeningHours(
                site_id=site.id,
                weekday=weekday,
                opens_at=time(7, 0),
                closes_at=time(16, 0),
            )
        )
    return site.id


@pytest.fixture
def directory(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SimpleNamespace]:
    """The eleven demo clinics, verified, with queues, hours and services; an app and a session factory.

    The RBAC catalogue is synced too, so a patient session opens the patient-only routes.
    ``directory.patient_client()`` returns a client signed in as a new patient.
    """
    factory = sessionmaker(bind=migrated_engine, autoflush=False)
    ids: dict[str, str] = {}
    with factory() as db:
        sync_rbac_catalog(db)
        for clinic in CLINICS:
            site_id = add_verified_clinic(
                db,
                slug=clinic.slug,
                name=clinic.name,
                sector=clinic.sector,
                location=Coordinates(
                    latitude=clinic.latitude, longitude=clinic.longitude
                ),
                address_line=f"{clinic.name}, {clinic.suburb}",
                suburb=clinic.suburb,
                city=clinic.city,
                province=clinic.province,
            )
            ids[clinic.slug] = site_id
            seed_default_catalogue(db, site_id)
            for order, queue in enumerate(queues_for(clinic)):
                QueueFactory.create(
                    db,
                    site_id=site_id,
                    name=queue.name,
                    slug=queue.slug,
                    ticket_prefix=queue.prefix,
                    display_order=order,
                    expected_service_minutes=queue.service_minutes,
                )
        db.commit()

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret="m5-discovery-test-secret-min-32-characters",
        smtp_host="",
    )

    def _db() -> Generator[Session]:
        with factory() as db:
            yield db

    monkeypatch.setattr(security, "get_settings", lambda: settings)
    app = create_app(settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings

    def _patient_client() -> tuple[TestClient, str]:
        """A client carrying a new patient's session token, and that patient's id."""
        with factory() as db:
            patient = PatientFactory.create(db)
            db.commit()
            token = security.create_patient_session_token(
                patient.id, new_id(), patient.session_version
            )
            patient_id = patient.id
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {token}"
        return client, patient_id

    yield SimpleNamespace(
        app=app,
        client=TestClient(app),
        patient_client=_patient_client,
        session=factory,
        ids=ids,
        engine=migrated_engine,
    )
    app.dependency_overrides.clear()
