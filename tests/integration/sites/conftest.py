"""The M4 fixture: two clinics, the five ClinicQ roles, and a signed-in client for each (Issue 23).

Every sites, hours, queues, services, assignment and onboarding test in this package builds on
``clinics``. It exists once because the setup is identical each time and because the *shape* of it
is itself part of non-negotiable 3: a staff member's clinic is a role held **at a site**, so the
fixture creates real ``site`` rows and assigns roles to them, never a column on the user.

    def test_something(clinics):
        manager = clinics.client("manager.a@clinicq.example")
        assert manager.get(f"/api/v1/sites/{clinics.site_a}").status_code == 200

``clinics.client(email)`` returns a ``TestClient`` that has signed in and carries the session's
CSRF token on every unsafe request, so a test asserting a 403 is asserting authorization rather
than a missing header.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, SiteStatus, UserRole
from src.core import refresh_token_policy, security
from src.core.config import Settings, get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.database.models import Base
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app
from tests.factories import FACTORY_STAFF_PASSWORD, SiteFactory, StaffFactory

_SECRET = "m4-sites-test-secret-min-32-characters!!"

#: Clinic A is the caller's own throughout; Clinic B is the one they must never reach.
SITE_A = "0199b0c0-0000-7000-8000-00000000a001"
SITE_B = "0199b0c0-0000-7000-8000-00000000b001"

#: Who exists, in which role, at which clinic. ``None`` is an unscoped assignment: the operator,
#: who reaches a clinic only through the audited cross-site hatch (Issue 19).
PEOPLE: tuple[tuple[str, UserRole, str | None], ...] = (
    ("manager.a", UserRole.CLINIC_MANAGER, SITE_A),
    ("desk.a", UserRole.RECEPTIONIST, SITE_A),
    ("nurse.a", UserRole.NURSE_DOCTOR, SITE_A),
    ("manager.b", UserRole.CLINIC_MANAGER, SITE_B),
    ("desk.b", UserRole.RECEPTIONIST, SITE_B),
    ("operator", UserRole.PLATFORM_ADMIN, None),
)


@pytest.fixture
def clinics(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """Two clinics with staff, an app wired to an in-memory database, and a client per person."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_SECRET,
        auth_password_login_enabled=True,
        smtp_host="",
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with factory() as db:
        sync_rbac_catalog(db)
        # Verified, because these stand in for listed clinics: a draft one refuses joins for a
        # reason that has nothing to do with what most of these tests are about (Issue 24's gate).
        SiteFactory.create(db, id=SITE_A, status=SiteStatus.VERIFIED)
        SiteFactory.create(db, id=SITE_B, status=SiteStatus.VERIFIED)
        for name, role, site_id in PEOPLE:
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

    def _client(email: str) -> TestClient:
        """Sign in as ``email`` and return a client that carries the CSRF token on writes."""
        client = TestClient(app)
        signed_in = client.post(
            "/api/v1/auth/password/login",
            json={"email": email, "password": FACTORY_STAFF_PASSWORD},
        )
        assert signed_in.status_code == status.HTTP_200_OK, signed_in.text
        token = client.cookies.get(settings.csrf_cookie_name)
        if token:
            client.headers["X-CSRF-Token"] = token
        return client

    yield SimpleNamespace(
        app=app,
        session=factory,
        client=_client,
        settings=settings,
        site_a=SITE_A,
        site_b=SITE_B,
    )
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()
