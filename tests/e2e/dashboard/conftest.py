"""The clinic dashboard in a real browser: one clinic on a migrated PostgreSQL database (Issue 55).

A module gets its own database, brought to ``head`` by the real migrations, and its own server; every
test starts from an empty day (:func:`fresh_day`). The clinic mirrors the HTTP suite's clinic A: open
around the clock, Triage (``T``, Room 2) and the Pharmacy (``P``), a receptionist, a nurse on Triage
and a clinic manager.

``DASHBOARD_OUTBOX_EXPIRY_SECONDS`` is 8 here rather than 120, so the test of an action held too long
waits seconds, not minutes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import SiteStatus, TicketSource, UserRole
from src.commons.ids import new_id
from src.core import refresh_token_policy, security
from src.core.config import get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.core.site_scope import SiteAccess
from src.database.models import Queue
from src.database.schema import apply_postgres_search_path
from src.database.session import get_db
from src.main import create_app
from src.modules.queue.sequence import issue_ticket
from src.modules.queue.snapshot import NoSnapshotCache, set_snapshot_cache
from src.modules.staff.assignments import set_room_assignments
from src.web.dashboard import routes as dashboard_routes
from tests.conftest import _TEST_DB_PREFIX, run_alembic
from tests.e2e.conftest import serve
from tests.factories import (
    FACTORY_STAFF_PASSWORD,
    QueueFactory,
    SiteFactory,
    StaffFactory,
)
from tests.integration.dashboard.conftest import dashboard_settings
from tests.integration.queue.conftest import open_all_day

pytestmark = pytest.mark.postgres

SITE = "0199b0c0-0000-7000-8000-0000000e2e01"
EMAIL_DOMAIN = "clinicq.example"
OUTBOX_EXPIRY_SECONDS = 8

#: Everything a day in the queues writes, emptied between tests.
_DAY_TABLES = (
    "queue_request_key",
    "queue_reorder",
    "visit_note",
    "ticket",
    "visit",
    "ticket_sequence",
    "site_queue_snapshot",
)


@pytest.fixture(scope="module")
def e2e_database(postgres_server_url: URL) -> Iterator[URL]:
    """A migrated database for one module, dropped afterwards."""
    name = f"{_TEST_DB_PREFIX}{new_id().replace('-', '')}"
    admin = create_engine(postgres_server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = postgres_server_url.set(database=name)
    try:
        run_alembic(url, ("upgrade", "head"))
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def clinic(e2e_database: URL, browser: Any) -> Iterator[SimpleNamespace]:
    """The clinic, its people and a running server for this module."""
    settings = dashboard_settings(
        scheduler_enabled=False,
        password_login_rate_limit_per_email=10_000,
        password_login_rate_limit_per_ip=10_000,
        dashboard_outbox_expiry_seconds=OUTBOX_EXPIRY_SECONDS,
    )
    engine = create_engine(e2e_database, pool_size=10, max_overflow=10)
    apply_postgres_search_path(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    set_snapshot_cache(NoSnapshotCache())

    with factory() as db:
        sync_rbac_catalog(db)
        SiteFactory.create(db, id=SITE, name="Zola Clinic", status=SiteStatus.VERIFIED)
        open_all_day(db, SITE)
        triage = QueueFactory.create(
            db,
            site_id=SITE,
            name="Triage",
            slug="triage",
            ticket_prefix="T",
            room_label="Room 2",
        )
        pharmacy = QueueFactory.create(
            db, site_id=SITE, name="Pharmacy", slug="pharmacy", ticket_prefix="P"
        )
        people = {
            name: StaffFactory.create(
                db,
                email=f"{name}@{EMAIL_DOMAIN}",
                first_name=name.split(".")[0].title(),
                role=role,
                site_id=SITE,
            )
            for name, role in (
                ("desk", UserRole.RECEPTIONIST),
                ("nurse", UserRole.NURSE_DOCTOR),
                ("manager", UserRole.CLINIC_MANAGER),
            )
        }
        db.flush()
        set_room_assignments(
            db,
            SiteAccess(site_id=SITE, user=people["manager"]),
            people["nurse"].id,
            [triage.id],
        )
        db.commit()
        triage_id, pharmacy_id = triage.id, pharmacy.id

    def _db() -> Iterator[Session]:
        with factory() as db:
            yield db

    with pytest.MonkeyPatch.context() as patch:
        for module in (security, refresh_token_policy, dashboard_routes):
            patch.setattr(module, "get_settings", lambda: settings)
        app = create_app(settings)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_settings] = lambda: settings
        with serve(app) as base_url:
            yield SimpleNamespace(
                base_url=base_url,
                browser=browser,
                session=factory,
                settings=settings,
                site=SITE,
                triage=triage_id,
                pharmacy=pharmacy_id,
            )
    engine.dispose()


@pytest.fixture
def fresh_day(clinic: SimpleNamespace) -> SimpleNamespace:
    """Clinic with an empty day: no tickets, keys or overrides from an earlier test."""
    with clinic.session() as db:
        tables = ", ".join(f"clinicq.{name}" for name in _DAY_TABLES)
        db.execute(text(f"TRUNCATE {tables} CASCADE"))
        db.commit()
    return clinic


@pytest.fixture
def walk_ins(fresh_day: SimpleNamespace) -> Callable[[str, int], list[str]]:
    """Issue ``count`` walk-ins in a queue; their numbers in order."""

    def issue(queue_id: str, count: int) -> list[str]:
        with fresh_day.session() as db:
            queue = db.get(Queue, queue_id)
            numbers = [
                issue_ticket(db, queue=queue, source=TicketSource.WALK_IN).number
                for _ in range(count)
            ]
            db.commit()
        return numbers

    return issue


@pytest.fixture
def signed_in(fresh_day: SimpleNamespace) -> Iterator[Callable[..., Any]]:
    """Open a page as ``name`` in a fresh browser context; every context is closed after the test."""
    contexts: list[Any] = []

    def open_page(name: str, path: str, **context_options: Any) -> Any:
        context = fresh_day.browser.new_context(
            base_url=fresh_day.base_url,
            viewport=context_options.pop("viewport", {"width": 1366, "height": 768}),
            **context_options,
        )
        contexts.append(context)
        answer = context.request.post(
            "/api/v1/auth/password/login",
            data={
                "email": f"{name}@{EMAIL_DOMAIN}",
                "password": FACTORY_STAFF_PASSWORD,
            },
        )
        assert answer.ok, answer.text()
        page = context.new_page()
        page.goto(path)
        return page

    yield open_page
    for context in contexts:
        context.close()
