"""The waiting-room board in a real browser: one clinic on a migrated PostgreSQL database (M8).

A module gets its own database, brought to ``head`` by the real migrations
(:func:`tests.e2e.dashboard.conftest.e2e_database`), and its own server (:func:`tests.e2e.conftest.serve`).
The clinic has five queues, each with a room, so a test can switch on one, two, three or all five of them
and see the board lay itself out. Every test starts from an empty day (:func:`board_day`).

Nothing here signs in: a board is a public page.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text, update
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from src.commons.enums import ActorKind, SiteStatus, TicketSource, TicketStatus
from src.core.config import get_settings
from src.database.models import Queue, Ticket
from src.database.schema import apply_postgres_search_path
from src.database.session import get_db
from src.main import create_app
from src.modules.queue.lifecycle import Actor, call_next, transition_ticket
from src.modules.queue.sequence import issue_ticket
from src.modules.queue.snapshot import NoSnapshotCache, set_snapshot_cache
from src.web import display as display_routes
from src.web import display_stream
from tests.e2e.conftest import serve
from tests.e2e.dashboard.conftest import e2e_database
from tests.factories import QueueFactory, SiteFactory
from tests.integration.dashboard.conftest import dashboard_settings
from tests.integration.queue.conftest import open_all_day

__all__ = ["board_clinic", "board_day", "e2e_database"]

pytestmark = pytest.mark.postgres

SITE = "0199b0c0-0000-7000-8000-0000000e2e56"
#: How often the test server's board streams beat (production: 15 seconds).
SERVER_HEARTBEAT_SECONDS = 0.5
DESK = Actor(kind=ActorKind.STAFF, label="desk@clinicq.example")
#: The clinic's queues: name, ticket prefix, room.
QUEUES = (
    ("Triage", "T", "Room 2"),
    ("General consultation", "A", "Room 4"),
    ("Chronic medication collection", "C", "Window 3"),
    ("Immunisation", "I", "Room 6"),
    ("Family planning", "F", "Room 7"),
)
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
def board_clinic(e2e_database: URL, browser: Any) -> Iterator[SimpleNamespace]:
    """The clinic, its five queues and a running server for this module."""
    settings = dashboard_settings(scheduler_enabled=False, board_health_ticker=True)
    engine = create_engine(e2e_database, pool_size=10, max_overflow=10)
    apply_postgres_search_path(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    set_snapshot_cache(NoSnapshotCache())

    with factory() as db:
        SiteFactory.create(
            db, id=SITE, name="Zola Community Clinic", status=SiteStatus.VERIFIED
        )
        open_all_day(db, SITE)
        queue_ids = [
            QueueFactory.create(
                db,
                site_id=SITE,
                name=name,
                slug=name.lower().replace(" ", "-"),
                ticket_prefix=prefix,
                room_label=room,
                display_order=order,
            ).id
            for order, (name, prefix, room) in enumerate(QUEUES)
        ]
        db.commit()

    def _db() -> Iterator[Any]:
        with factory() as db:
            yield db

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(display_routes, "get_settings", lambda: settings)
        # The server beats every half second rather than every 15, so a test that moves the page's clock
        # on has real beats to hear. The page still expects one every 15 seconds (data-heartbeat-seconds).
        patch.setattr(
            display_stream, "BOARD_HEARTBEAT_SECONDS", SERVER_HEARTBEAT_SECONDS
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
                site=SITE,
                queues=queue_ids,
                page_path=f"/display/{SITE}",
            )
    engine.dispose()


@pytest.fixture
def board_day(board_clinic: SimpleNamespace) -> Iterator[SimpleNamespace]:
    """An empty day with helpers to open queues, issue tickets and call them; contexts closed after."""
    with board_clinic.session() as db:
        tables = ", ".join(f"clinicq.{name}" for name in _DAY_TABLES)
        db.execute(text(f"TRUNCATE {tables} CASCADE"))
        db.execute(update(Queue).where(Queue.site_id == SITE).values(is_active=True))
        db.commit()
    contexts: list[Any] = []

    def open_queues(count: int) -> list[str]:
        """Leave the first ``count`` queues open and close the rest; the open ones' ids."""
        with board_clinic.session() as db:
            for index, queue_id in enumerate(board_clinic.queues):
                db.execute(
                    update(Queue)
                    .where(Queue.id == queue_id)
                    .values(is_active=index < count)
                )
            db.commit()
        return board_clinic.queues[:count]

    def issue(queue_id: str, count: int) -> list[str]:
        """Issue ``count`` walk-ins in a queue; their numbers."""
        with board_clinic.session() as db:
            queue = db.get_one(Queue, queue_id)
            numbers = [
                issue_ticket(db, queue=queue, source=TicketSource.WALK_IN).number
                for _ in range(count)
            ]
            db.commit()
        return numbers

    def call(
        queue_id: str, *, finish_previous: bool = False, moment: datetime | None = None
    ) -> str:
        """Call the next patient in a queue at ``moment`` (now); its number. Optionally see off the last."""
        with board_clinic.session() as db:
            if finish_previous:
                for ticket in db.query(Ticket).filter(
                    Ticket.queue_id == queue_id,
                    Ticket.status == TicketStatus.CALLED.value,
                ):
                    transition_ticket(
                        db, ticket.id, TicketStatus.IN_PROGRESS, actor=DESK
                    )
                    transition_ticket(db, ticket.id, TicketStatus.DONE, actor=DESK)
            number = call_next(
                db, db.get_one(Queue, queue_id), actor=DESK, moment=moment
            ).number
            db.commit()
        return number

    def new_page(width: int = 1920, height: int = 1080, **options: Any) -> Any:
        """A fresh browser page at ``width`` x ``height``, closed with its context after the test."""
        options.setdefault("base_url", board_clinic.base_url)
        context = board_clinic.browser.new_context(
            viewport={"width": width, "height": height}, **options
        )
        contexts.append(context)
        return context.new_page()

    yield SimpleNamespace(
        clinic=board_clinic,
        open_queues=open_queues,
        issue=issue,
        call=call,
        new_page=new_page,
    )
    for context in contexts:
        context.close()
