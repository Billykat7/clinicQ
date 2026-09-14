"""The live front desk over HTTP (Issue 49): what changes are announced, the cards, and their gates.

The browser half (a second device's change arriving, polling when the stream is unavailable, the
"reconnecting, data from HH:MM" line, focus kept) is demonstrated in the pull request with a real
browser; Issue 55 turns it into the Playwright suite. What this file proves, without reading HTML:

* every committed queue write announces itself to the clinic's live streams, a call with its number,
  and a write that rolls back announces nothing;
* the cards say who is waiting, who is with staff, the average wait today, the longest wait now, and
  mark a queue stuck once the longest wait passes the threshold;
* the cards and the stream are gated like the board: signed out ``401``, another clinic ``404``, no
  front desk here ``403``;
* ten queues and two hundred waiting tickets render in one read, inside a budget.
"""

from __future__ import annotations

import time as clock
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest
from starlette import status

from src.commons.enums import ActorKind, LiveEventType, TicketSource
from src.commons.time import APP_TIMEZONE, business_date
from src.core import live_events
from src.core.live_events import EVENT_STREAM_MEDIA_TYPE, LiveEvent, format_event
from src.core.site_scope import SiteAccess
from src.database.models import Queue, User
from src.modules.queue.lifecycle import Actor, call_next
from src.modules.queue.sequence import issue_ticket
from src.web.dashboard.board import read_board
from tests.factories import QueueFactory

#: The budget for rendering ten queues and two hundred waiting tickets once, on the test database.
#: Generous on purpose: it catches a query per ticket or per queue, not the speed of a CI runner.
CARDS_BUDGET_SECONDS = 1.5


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[LiveEvent]]:
    """Every event the process broker is handed, in order."""
    seen: list[LiveEvent] = []
    monkeypatch.setattr(live_events.broker, "publish", seen.append)
    yield seen


def _cards(client, dashboard: SimpleNamespace, site: str = ""):
    """GET the board's cards fragment at clinic A (or ``site``)."""
    return client.get(dashboard.page(site or dashboard.site_a, "board/cards"))


def test_a_committed_join_and_call_announce_themselves_to_the_clinics_streams(
    dashboard: SimpleNamespace, published: list[LiveEvent]
) -> None:
    """A walk-in is ``queue.updated``; Call next is ``ticket.called`` with the number; only clinic A's."""
    desk = dashboard.client("desk.a")
    walk_in = desk.post(
        f"/api/v1/sites/{dashboard.site_a}/queues/{dashboard.triage}/tickets", json={}
    )
    assert walk_in.status_code == status.HTTP_201_CREATED, walk_in.text
    called = desk.post(
        f"/api/v1/sites/{dashboard.site_a}/queues/{dashboard.triage}/tickets/call-next"
    )
    assert called.status_code == status.HTTP_200_OK, called.text

    kinds = [(event.type, event.site_id, event.queue_id) for event in published]
    assert kinds == [
        (LiveEventType.QUEUE_UPDATED, dashboard.site_a, dashboard.triage),
        (LiveEventType.TICKET_CALLED, dashboard.site_a, dashboard.triage),
    ]
    assert published[1].extra == {"number": walk_in.json()["ticket"]["number"]}
    # No patient data travels: the events carry ids, a time, and a public ticket number.
    for event in published:
        assert set(event.payload()) <= {"type", "site_id", "at", "queue_id", "number"}


def test_a_queue_write_that_rolls_back_announces_nothing(
    dashboard: SimpleNamespace, published: list[LiveEvent]
) -> None:
    """The event waits for the commit; a rollback throws it away."""
    with dashboard.session() as db:
        queue = db.get(Queue, dashboard.triage)
        issue_ticket(db, queue=queue, source=TicketSource.WALK_IN)
        db.commit()
    published.clear()
    with dashboard.session() as db:
        queue = db.get(Queue, dashboard.triage)
        call_next(db, queue, actor=Actor(kind=ActorKind.STAFF, label="desk.a"))
        db.rollback()
    assert published == []
    with dashboard.session() as db:
        queue = db.get(Queue, dashboard.triage)
        call_next(db, queue, actor=Actor(kind=ActorKind.STAFF, label="desk.a"))
        assert published == []  # not before the commit either
        db.commit()
    assert [event.type for event in published] == [LiveEventType.TICKET_CALLED]


def test_the_cards_show_the_day_and_mark_a_stuck_queue(
    dashboard: SimpleNamespace,
) -> None:
    """A patient waiting past the threshold turns the card stuck; the average counts today's calls.

    Read at a fixed 10:00 on today's service day, so the test cannot straddle midnight.
    """
    ten = datetime.combine(business_date(), time(10, 0), tzinfo=APP_TIMEZONE)
    with dashboard.session() as db:
        triage = db.get(Queue, dashboard.triage)
        pharmacy = db.get(Queue, dashboard.pharmacy)
        # Pharmacy: someone has waited 50 minutes (over the default 45), and someone 5.
        for minutes in (50, 5):
            issue_ticket(
                db,
                queue=pharmacy,
                source=TicketSource.WALK_IN,
                moment=ten - timedelta(minutes=minutes),
            )
        # Triage: joined at 09:40 and called at 09:50, a 10-minute wait; one more still waiting.
        for minutes in (20, 2):
            issue_ticket(
                db,
                queue=triage,
                source=TicketSource.WALK_IN,
                moment=ten - timedelta(minutes=minutes),
            )
        db.commit()
        call_next(
            db,
            triage,
            actor=Actor(kind=ActorKind.STAFF, label="desk.a"),
            moment=ten - timedelta(minutes=10),
        )
        db.commit()
        manager = db.get(User, dashboard.ids["manager.a"])
        board = read_board(
            db, SiteAccess(site_id=dashboard.site_a, user=manager), moment=ten
        )

    cards = {card.id: card for card in board.cards}
    pharmacy_card, triage_card = cards[dashboard.pharmacy], cards[dashboard.triage]
    assert (pharmacy_card.waiting, pharmacy_card.with_staff) == (2, 0)
    assert pharmacy_card.longest_wait_minutes == 50
    assert pharmacy_card.stuck is True
    assert pharmacy_card.average_wait_minutes is None

    assert (triage_card.waiting, triage_card.with_staff) == (1, 1)
    assert triage_card.average_wait_minutes == 10
    assert triage_card.longest_wait_minutes == 2
    assert triage_card.stuck is False
    (with_staff,) = triage_card.with_staff_tickets
    assert (with_staff.badge.label, with_staff.minutes_since_called) == ("Called", 10)
    assert board.as_of == ten

    # The fragment the page fetches renders the same read, never cached.
    response = _cards(dashboard.client("desk.a"), dashboard)
    assert response.headers["cache-control"] == "no-store"
    assert {card.id for card in response.context["queues"]} == set(cards)


def test_the_cards_and_the_stream_are_gated_like_the_board(
    dashboard: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signed out 401 (a script must notice, not be redirected), another clinic 404, a nurse 403."""
    stream = dashboard.page(dashboard.site_a, "board/stream")
    anonymous = dashboard.anonymous()
    assert _cards(anonymous, dashboard).status_code == status.HTTP_401_UNAUTHORIZED
    assert anonymous.get(stream).status_code == status.HTTP_401_UNAUTHORIZED

    desk_b = dashboard.client("desk.b")
    assert _cards(desk_b, dashboard).status_code == status.HTTP_404_NOT_FOUND
    assert desk_b.get(stream).status_code == status.HTTP_404_NOT_FOUND

    nurse = dashboard.client("nurse.a")
    assert _cards(nurse, dashboard).status_code == status.HTTP_403_FORBIDDEN
    assert nurse.get(stream).status_code == status.HTTP_403_FORBIDDEN

    # The receptionist gets the event stream. A stream that ends after its first event stands in for
    # the endless one, so the test reads a real response and does not wait on a heartbeat.
    async def one_event(site_id: str, **_: object) -> AsyncIterator[str]:
        yield format_event(LiveEvent(LiveEventType.HEARTBEAT, site_id), 1)

    monkeypatch.setattr(live_events.broker, "stream", one_event)
    opened = dashboard.client("desk.a").get(stream)
    assert opened.status_code == status.HTTP_200_OK
    assert opened.headers["content-type"].startswith(EVENT_STREAM_MEDIA_TYPE)
    assert opened.headers["cache-control"] == "no-cache, no-transform"
    assert opened.text.startswith("id: 1\nevent: heartbeat\n")


def test_ten_queues_and_two_hundred_waiting_tickets_render_inside_the_budget(
    dashboard: SimpleNamespace,
) -> None:
    """The busiest front desk the issue names, read once: every card right, and quick."""
    with dashboard.session() as db:
        queues = [db.get(Queue, dashboard.triage), db.get(Queue, dashboard.pharmacy)]
        queues += [
            QueueFactory.create(
                db,
                site_id=dashboard.site_a,
                name=f"Room {n}",
                slug=f"room-{n}",
                ticket_prefix="R",
            )
            for n in range(8)
        ]
        for n in range(200):
            issue_ticket(db, queue=queues[n % 10], source=TicketSource.WALK_IN)
        db.commit()

    desk = dashboard.client("desk.a")
    timings = []
    response = None
    for _ in range(3):
        started = clock.perf_counter()
        response = _cards(desk, dashboard)
        timings.append(clock.perf_counter() - started)
    assert response is not None and response.status_code == status.HTTP_200_OK
    cards = response.context["queues"]
    assert len(cards) == 10
    assert sum(card.waiting for card in cards) == 200
    assert sum(len(line.waiting) for line in response.context["lines"].values()) == 200
    assert min(timings) < CARDS_BUDGET_SECONDS, timings
