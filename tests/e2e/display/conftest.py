"""The waiting-room board in a real browser: one clinic on a migrated PostgreSQL database (M8).

A module gets its own database, brought to ``head`` by the real migrations
(:func:`tests.e2e.dashboard.conftest.e2e_database`), and its own server (:func:`tests.e2e.conftest.serve`).
The clinic has five queues, each with a room, so a test can switch on one, two, three or all five of them
and see the board lay itself out. Every test starts from an empty day (:func:`board_day`).

Every page a test opens is a screen paired with the clinic unless it asks otherwise (``paired=False``),
because since Issue 61 only a paired screen or the clinic's staff may see its board. ``manager_page()`` is
a browser signed in as the clinic manager, who pairs and removes screens.
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

from src.commons.enums import (
    SITE_DEFAULT_ANNOUNCE_VOLUME,
    SITE_DEFAULT_BOARD_LANGUAGE,
    SITE_DEFAULT_BOARD_THEME,
    SITE_DEFAULT_DISPLAY_MODE,
    ActorKind,
    SiteStatus,
    TicketSource,
    TicketStatus,
    UserRole,
)
from src.commons.time import now_sast
from src.core import refresh_token_policy, security
from src.core.config import get_settings
from src.core.rbac_manifest_sync import sync_rbac_catalog
from src.database.models import Queue, Site, Ticket
from src.database.schema import apply_postgres_search_path
from src.database.session import get_db
from src.main import create_app
from src.modules.display import devices
from src.modules.queue.lifecycle import Actor, call_next, transition_ticket
from src.modules.queue.sequence import issue_ticket
from src.modules.queue.snapshot import NoSnapshotCache, set_snapshot_cache
from src.web import display as display_routes
from src.web import display_stream
from src.web.dashboard import routes as dashboard_routes
from tests.e2e.conftest import empty_tables, serve
from tests.e2e.dashboard.conftest import e2e_database
from tests.factories import (
    FACTORY_STAFF_PASSWORD,
    QueueFactory,
    SiteFactory,
    StaffFactory,
)
from tests.integration.dashboard.conftest import dashboard_settings
from tests.integration.queue.conftest import open_all_day

__all__ = ["board_clinic", "board_day", "e2e_database"]

pytestmark = pytest.mark.postgres

SITE = "0199b0c0-0000-7000-8000-0000000e2e56"
#: How often the test server's board streams beat (production: 15 seconds).
SERVER_HEARTBEAT_SECONDS = 0.5
#: How often the test server's board streams check their screen may still see the board (production: 30).
ACCESS_CHECK_SECONDS = 2.0
#: The clinic manager who pairs and removes screens in the browser tests.
MANAGER_EMAIL = "manager@clinicq.example"
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
        sync_rbac_catalog(db)
        SiteFactory.create(
            db, id=SITE, name="Zola Community Clinic", status=SiteStatus.VERIFIED
        )
        StaffFactory.create(
            db,
            email=MANAGER_EMAIL,
            first_name="Manager",
            role=UserRole.CLINIC_MANAGER,
            site_id=SITE,
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
        for module in (
            security,
            refresh_token_policy,
            display_routes,
            dashboard_routes,
        ):
            patch.setattr(module, "get_settings", lambda: settings)
        # A removed screen is noticed by its open stream within two seconds here, not thirty.
        patch.setattr(
            display_stream, "BOARD_ACCESS_CHECK_SECONDS", ACCESS_CHECK_SECONDS
        )
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
                settings=settings,
                site=SITE,
                queues=queue_ids,
                page_path=f"/display/{SITE}",
            )
    engine.dispose()


@pytest.fixture
def board_day(board_clinic: SimpleNamespace) -> Iterator[SimpleNamespace]:
    """An empty day with helpers to open queues, issue tickets and call them; contexts closed after."""
    empty_tables(board_clinic.session, _DAY_TABLES)
    with board_clinic.session() as db:
        db.execute(update(Queue).where(Queue.site_id == SITE).values(is_active=True))
        # A test that changed what the board shows or says leaves the next one the clinic's defaults.
        db.execute(
            update(Site)
            .where(Site.id == SITE)
            .values(
                display_mode=SITE_DEFAULT_DISPLAY_MODE.value,
                display_show_comment=False,
                board_language=SITE_DEFAULT_BOARD_LANGUAGE.value,
                board_theme=SITE_DEFAULT_BOARD_THEME.value,
                announce_audio=True,
                announce_volume=SITE_DEFAULT_ANNOUNCE_VOLUME,
            )
        )
        db.execute(text("DELETE FROM clinicq.display_device"))
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

    def paired_secret() -> str:
        """The secret of a new screen paired with the clinic, as a manager's pairing leaves it."""
        with board_clinic.session() as db:
            started = devices.start_device(db, user_agent="e2e kiosk")
            started.device.site_id = SITE
            started.device.paired_at = now_sast()
            started.device.last_seen_at = now_sast()
            started.device.pairing_code_hash = None
            db.commit()
        return started.secret

    def pair_context(context: Any, base_url: str) -> None:
        """Make a browser context a screen paired with the clinic."""
        context.add_cookies(
            [
                {
                    "name": board_clinic.settings.display_device_cookie_name,
                    "value": paired_secret(),
                    "url": f"{base_url}/display",
                    "httpOnly": True,
                    "sameSite": "Strict",
                }
            ]
        )

    def new_page(
        width: int = 1920, height: int = 1080, *, paired: bool = True, **options: Any
    ) -> Any:
        """A fresh browser page at ``width`` x ``height``: a paired screen unless ``paired=False``."""
        options.setdefault("base_url", board_clinic.base_url)
        context = board_clinic.browser.new_context(
            viewport={"width": width, "height": height}, **options
        )
        contexts.append(context)
        if paired:
            pair_context(context, options["base_url"])
        return context.new_page()

    def manager_page(width: int = 1366, height: int = 900) -> Any:
        """A page signed in as the clinic manager."""
        context = board_clinic.browser.new_context(
            base_url=board_clinic.base_url, viewport={"width": width, "height": height}
        )
        contexts.append(context)
        answer = context.request.post(
            "/api/v1/auth/password/login",
            data={"email": MANAGER_EMAIL, "password": FACTORY_STAFF_PASSWORD},
        )
        assert answer.ok, answer.text()
        return context.new_page()

    yield SimpleNamespace(
        clinic=board_clinic,
        open_queues=open_queues,
        issue=issue,
        call=call,
        new_page=new_page,
        manager_page=manager_page,
        paired_secret=paired_secret,
        pair_context=pair_context,
        track=contexts.append,
    )
    for context in contexts:
        context.close()
