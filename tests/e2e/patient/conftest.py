"""The patient's ticket page in a real browser: one clinic on a migrated PostgreSQL database (Issue 68).

A module gets its own database, brought to ``head`` by the real migrations, and its own server. Every
test starts from an empty day. ``ticket()`` issues a patient's web ticket behind walk-ins and gives back
its page link; ``owner_page()`` is a phone signed in as that ticket's patient, and ``follower_page()`` a
phone that was only sent the link.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from src.commons.enums import ActorKind, SiteStatus, TicketSource
from src.commons.ids import new_id
from src.core import refresh_token_policy, security
from src.core.config import get_settings
from src.core.csrf_middleware import mint_csrf_token
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.database.models import Queue
from src.database.schema import apply_postgres_search_path
from src.database.session import get_db
from src.main import create_app
from src.modules.queue.lifecycle import Actor, call_next
from src.modules.queue.sequence import issue_ticket
from src.modules.queue.snapshot import NoSnapshotCache, set_snapshot_cache
from src.web import ticket as ticket_routes
from tests.e2e.conftest import empty_tables, serve
from tests.e2e.dashboard.conftest import e2e_database
from tests.factories import PatientFactory, QueueFactory, SiteFactory
from tests.integration.dashboard.conftest import dashboard_settings
from tests.integration.queue.conftest import open_all_day

__all__ = ["e2e_database", "patient_clinic", "patient_day"]

pytestmark = pytest.mark.postgres

SITE = "0199b0c0-0000-7000-8000-0000000e2e68"
#: How often the test server's ticket streams beat (production: 15 seconds).
SERVER_HEARTBEAT_SECONDS = 0.5
DESK = Actor(kind=ActorKind.STAFF, label="desk@clinicq.example")
_DAY_TABLES = (
    "notification",
    "ticket",
    "visit",
    "ticket_sequence",
    "site_queue_snapshot",
)


@pytest.fixture(scope="module")
def patient_clinic(e2e_database: URL, browser: Any) -> Iterator[SimpleNamespace]:
    """The clinic, its Triage queue and a running server for this module."""
    settings = dashboard_settings(scheduler_enabled=False)
    engine = create_engine(e2e_database, pool_size=10, max_overflow=10)
    apply_postgres_search_path(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    set_snapshot_cache(NoSnapshotCache())

    with factory() as db:
        sync_rbac_catalog(db)
        SiteFactory.create(
            db,
            id=SITE,
            name="Zola Community Clinic",
            status=SiteStatus.VERIFIED,
            phone_e164="+27119330000",
        )
        open_all_day(db, SITE)
        queue_id = QueueFactory.create(
            db,
            site_id=SITE,
            name="Triage",
            slug="triage",
            ticket_prefix="T",
            room_label="Room 2",
        ).id
        db.commit()

    def _db() -> Iterator[Any]:
        with factory() as db:
            yield db

    with pytest.MonkeyPatch.context() as patch:
        for module in (security, refresh_token_policy):
            patch.setattr(module, "get_settings", lambda: settings)
        patch.setattr(
            ticket_routes, "TICKET_HEARTBEAT_SECONDS", SERVER_HEARTBEAT_SECONDS
        )
        app = create_app(settings)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_settings] = lambda: settings
        with serve(app) as base_url:
            yield SimpleNamespace(
                app=app,
                base_url=base_url,
                browser=browser,
                session=factory,
                settings=settings,
                queue=queue_id,
            )
    set_snapshot_cache(None)
    engine.dispose()


@pytest.fixture
def patient_day(patient_clinic: SimpleNamespace) -> Iterator[SimpleNamespace]:
    """An empty day with helpers to issue tickets, call them and open phones; contexts closed after."""
    empty_tables(patient_clinic.session, _DAY_TABLES)
    contexts: list[Any] = []

    def ticket(ahead: int) -> SimpleNamespace:
        """``ahead`` walk-ins, then a patient's web ticket: its number, page link and patient."""
        with patient_clinic.session() as db:
            queue = db.get_one(Queue, patient_clinic.queue)
            for _ in range(ahead):
                issue_ticket(db, queue=queue, source=TicketSource.WALK_IN)
            patient = PatientFactory.create(db)
            mine = issue_ticket(
                db, queue=queue, source=TicketSource.WEB, patient_id=patient.id
            )
            db.commit()
            return SimpleNamespace(
                number=mine.number,
                path=f"/t/{mine.page_token}",
                patient_id=patient.id,
                session_version=patient.session_version,
            )

    def call() -> str:
        """The desk calls the next patient; their number."""
        with patient_clinic.session() as db:
            number = call_next(
                db, db.get_one(Queue, patient_clinic.queue), actor=DESK
            ).number
            db.commit()
        return number

    def phone(width: int = 360, height: int = 760, **options: Any) -> Any:
        """A fresh browser context the size of a phone."""
        options.setdefault("base_url", patient_clinic.base_url)
        context = patient_clinic.browser.new_context(
            viewport={"width": width, "height": height}, **options
        )
        contexts.append(context)
        return context

    def owner_page(mine: SimpleNamespace, width: int = 360, **options: Any) -> Any:
        """A phone signed in as the ticket's own patient."""
        context = phone(width, **options)
        sid = new_id()
        base = options.get("base_url", patient_clinic.base_url)
        context.add_cookies(
            [
                {
                    "name": patient_clinic.settings.patient_session_cookie_name,
                    "value": security.create_patient_session_token(
                        mine.patient_id, sid, mine.session_version
                    ),
                    "url": base,
                    "httpOnly": True,
                },
                {
                    "name": patient_clinic.settings.csrf_cookie_name,
                    "value": mint_csrf_token(sid),
                    "url": base,
                },
            ]
        )
        return context.new_page()

    def follower_page(width: int = 360, **options: Any) -> Any:
        """A phone that was only sent the link."""
        return phone(width, **options).new_page()

    yield SimpleNamespace(
        clinic=patient_clinic,
        ticket=ticket,
        call=call,
        owner_page=owner_page,
        follower_page=follower_page,
    )
    for context in contexts:
        context.close()
