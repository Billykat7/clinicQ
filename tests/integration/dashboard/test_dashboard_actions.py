"""Call next, recall, done, no-show and undo from the dashboard, over HTTP (Issue 50).

Each acceptance criterion the server can prove, without reading HTML (``docs/IDE/RULES/testing-strategy.mdc``):

* **A double tap issues exactly one call.** The dashboard sends an ``Idempotency-Key`` with every press;
  the same key twice calls one patient and answers both presses with that patient, a new key is a new
  call, a key reused for another action is refused, and a press that failed keeps no key. The same holds
  for a status change. The race of two presses at the same instant is in
  ``tests/integration/queue/test_queue_concurrency.py``, on PostgreSQL.
* **Undo-last-call has a short window and is audited.** The patient is back in the same place with the
  call's time cleared, the trail keeps the call and its undoing as two rows, and after the window, or
  once the patient is being seen, the undo is refused and changes nothing. A bare status change to
  ``waiting`` is refused.
* **A nurse can only call from a queue they are assigned to**, and only undo there.
* **The in-room panel shows elapsed consultation time**, and the buttons each ticket offers come from
  the lifecycle: disabled, not hidden, for a role without the grant.
* **The room is live** on its own cards and its own stream, gated like the room.

The optimistic update and its rollback with the reason are the browser's, shown in the pull request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from starlette import status

from src.commons.enums import (
    ActorKind,
    AuditEntityType,
    LiveEventType,
    TicketSource,
    TicketStatus,
)
from src.commons.time import business_date, now_sast
from src.core import live_events
from src.core.live_events import LiveEvent, format_event
from src.core.site_scope import SiteAccess
from src.database.models import AuditEvent, Queue, QueueRequestKey, Ticket, User
from src.modules.queue.lifecycle import (
    DEDICATED_MOVE_CODE,
    STALE_TRANSITION_CODE,
    UNDO_WINDOW_CLOSED_CODE,
    Actor,
    call_next,
    transition_ticket,
)
from src.modules.queue.request_keys import REQUEST_KEY_REUSED_CODE, purge_request_keys
from src.modules.queue.sequence import issue_ticket
from src.web.dashboard.actions import ActionKind
from src.web.dashboard.board import read_board

_DESK = Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example")


def _api(dashboard: SimpleNamespace) -> str:
    return f"/api/v1/sites/{dashboard.site_a}"


def _walk_ins(dashboard: SimpleNamespace, queue_id: str, count: int) -> list[str]:
    """Issue ``count`` walk-ins in ``queue_id``; their ids in call order."""
    with dashboard.session() as db:
        queue = db.get(Queue, queue_id)
        ids = [
            issue_ticket(db, queue=queue, source=TicketSource.WALK_IN).id
            for _ in range(count)
        ]
        db.commit()
    return ids


def _statuses(dashboard: SimpleNamespace, ids: list[str]) -> list[str]:
    with dashboard.session() as db:
        return [db.get(Ticket, ticket_id).status for ticket_id in ids]


def _status_moves(dashboard: SimpleNamespace, ticket_id: str) -> list[str]:
    """The ticket's audited status moves as ``before → after (note)``, sorted.

    Sorted rather than in time order: the rows of one test share a second on SQLite, and what the
    criteria need is that each move is there exactly once.
    """
    with dashboard.session() as db:
        rows = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.TICKET.value,
                AuditEvent.entity_id == ticket_id,
            )
        ).scalars()
        return sorted(row.context.split(": ", 1)[1] for row in rows if row.diff)


def _keyed(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key}


def test_a_double_tap_on_call_next_calls_exactly_one_patient(
    dashboard: SimpleNamespace,
) -> None:
    """Two presses with one key: one call, both answered with it. A new key is a new call."""
    first, second, third = _walk_ins(dashboard, dashboard.triage, 3)
    desk = dashboard.client("desk.a")
    url = f"{_api(dashboard)}/queues/{dashboard.triage}/tickets/call-next"

    tap = desk.post(url, headers=_keyed("tap-0001-aaaa"))
    double = desk.post(url, headers=_keyed("tap-0001-aaaa"))

    assert tap.status_code == double.status_code == status.HTTP_200_OK
    assert tap.json()["id"] == double.json()["id"] == first
    assert double.json()["status"] == TicketStatus.CALLED.value
    assert _statuses(dashboard, [first, second, third]) == [
        "called",
        "waiting",
        "waiting",
    ]
    assert _status_moves(dashboard, first) == ["waiting → called (call next)"]
    with dashboard.session() as db:
        assert db.scalar(select(func.count()).select_from(QueueRequestKey)) == 1

    # Keys do not block calling: the next deliberate press, with its own key, calls the next patient.
    again = desk.post(url, headers=_keyed("tap-0002-bbbb"))
    assert again.json()["id"] == second
    # And an API client that sends no key keeps the behaviour it always had.
    assert desk.post(url).json()["id"] == third


def test_a_key_reused_for_another_action_is_refused_and_moves_nothing(
    dashboard: SimpleNamespace,
) -> None:
    """The same key on another queue is a different request: answering it with Triage's patient would lie."""
    (triage_ticket,) = _walk_ins(dashboard, dashboard.triage, 1)
    (pharmacy_ticket,) = _walk_ins(dashboard, dashboard.pharmacy, 1)
    desk = dashboard.client("desk.a")
    base = f"{_api(dashboard)}/queues"

    assert (
        desk.post(
            f"{base}/{dashboard.triage}/tickets/call-next",
            headers=_keyed("key-reuse-01"),
        ).status_code
        == status.HTTP_200_OK
    )
    reused = desk.post(
        f"{base}/{dashboard.pharmacy}/tickets/call-next", headers=_keyed("key-reuse-01")
    )

    assert reused.status_code == status.HTTP_409_CONFLICT
    assert reused.json()["code"] == REQUEST_KEY_REUSED_CODE
    assert _statuses(dashboard, [triage_ticket, pharmacy_ticket]) == [
        "called",
        "waiting",
    ]
    # A key is its sender's: another person's identical key is their own request.
    other = dashboard.client("both").post(
        f"{base}/{dashboard.pharmacy}/tickets/call-next", headers=_keyed("key-reuse-01")
    )
    assert other.json()["id"] == pharmacy_ticket
    # Keys that are not keys are refused before anything is read.
    assert (
        desk.post(
            f"{base}/{dashboard.triage}/tickets/call-next", headers=_keyed("no spaces!")
        ).status_code
        == status.HTTP_422_UNPROCESSABLE_CONTENT
    )


def test_a_press_that_failed_keeps_no_key_so_pressing_again_really_tries(
    dashboard: SimpleNamespace,
) -> None:
    """Nobody waiting: refused, and the key rolled back with it. Once someone joins, the same key calls."""
    desk = dashboard.client("desk.a")
    url = f"{_api(dashboard)}/queues/{dashboard.triage}/tickets/call-next"

    empty = desk.post(url, headers=_keyed("retry-key-01"))
    assert empty.status_code == status.HTTP_409_CONFLICT
    assert empty.json()["code"] == "ticket.call_next.empty"

    (joined,) = _walk_ins(dashboard, dashboard.triage, 1)
    retried = desk.post(url, headers=_keyed("retry-key-01"))
    assert retried.status_code == status.HTTP_200_OK
    assert retried.json()["id"] == joined


def test_a_double_tap_on_a_status_button_moves_the_ticket_once(
    dashboard: SimpleNamespace,
) -> None:
    """Start pressed twice: one move and one audit row; without the key the second press is a stale 409."""
    (ticket_id,) = _walk_ins(dashboard, dashboard.triage, 1)
    desk = dashboard.client("desk.a")
    desk.post(f"{_api(dashboard)}/queues/{dashboard.triage}/tickets/call-next")
    url = f"{_api(dashboard)}/tickets/{ticket_id}/transitions"
    start = {"to": "in_progress", "expected_status": "called"}

    presses = [
        desk.post(url, json=start, headers=_keyed("start-key-01")) for _ in range(2)
    ]

    assert [press.status_code for press in presses] == [200, 200]
    assert _status_moves(dashboard, ticket_id) == sorted(
        ["waiting → called (call next)", "called → in_progress"]
    )
    unkeyed = desk.post(url, json=start)
    assert unkeyed.status_code == status.HTTP_409_CONFLICT
    assert unkeyed.json()["code"] == STALE_TRANSITION_CODE


def test_an_undone_call_puts_the_patient_back_in_their_place_with_both_steps_audited(
    dashboard: SimpleNamespace,
) -> None:
    """T001 called by mistake and undone: waiting again, first in line, call time cleared, two rows."""
    first, second = _walk_ins(dashboard, dashboard.triage, 2)
    desk = dashboard.client("desk.a")
    queue_api = f"{_api(dashboard)}/queues/{dashboard.triage}/tickets"
    assert desk.post(f"{queue_api}/call-next").json()["id"] == first

    undone = desk.post(f"{_api(dashboard)}/tickets/{first}/undo-call")

    assert undone.status_code == status.HTTP_200_OK, undone.text
    assert undone.json()["status"] == TicketStatus.WAITING.value
    assert desk.get(f"{queue_api}/next").json()["id"] == first
    with dashboard.session() as db:
        ticket = db.get(Ticket, first)
        assert ticket.called_at is None and ticket.status == "waiting"
    assert _status_moves(dashboard, first) == sorted(
        ["waiting → called (call next)", "called → waiting (undo call)"]
    )
    assert _statuses(dashboard, [second]) == ["waiting"]
    # Back to waiting is never a bare status change.
    bare = desk.post(
        f"{_api(dashboard)}/tickets/{first}/transitions", json={"to": "waiting"}
    )
    assert bare.status_code == status.HTTP_409_CONFLICT
    assert bare.json()["code"] == DEDICATED_MOVE_CODE


def test_an_undo_after_the_window_or_once_the_patient_is_seen_is_refused_and_changes_nothing(
    dashboard: SimpleNamespace,
) -> None:
    """A call 31 seconds old cannot be undone; nor can one whose patient is already being seen."""
    late, seen = _walk_ins(dashboard, dashboard.triage, 2)
    window = dashboard.settings.queue_call_undo_seconds
    with dashboard.session() as db:
        queue = db.get(Queue, dashboard.triage)
        call_next(
            db, queue, actor=_DESK, moment=now_sast() - timedelta(seconds=window + 1)
        )
        call_next(db, queue, actor=_DESK)
        transition_ticket(db, seen, TicketStatus.IN_PROGRESS, actor=_DESK)
        db.commit()
    desk = dashboard.client("desk.a")

    too_late = desk.post(f"{_api(dashboard)}/tickets/{late}/undo-call")
    being_seen = desk.post(f"{_api(dashboard)}/tickets/{seen}/undo-call")

    assert too_late.status_code == status.HTTP_409_CONFLICT
    assert too_late.json()["code"] == UNDO_WINDOW_CLOSED_CODE
    assert too_late.json()["detail"].startswith(
        f"T001 was called more than {window} seconds ago"
    )
    assert being_seen.status_code == status.HTTP_409_CONFLICT
    assert being_seen.json()["code"] == STALE_TRANSITION_CODE
    assert _statuses(dashboard, [late, seen]) == ["called", "in_progress"]
    assert _status_moves(dashboard, late) == ["waiting → called (call next)"]


def test_a_nurse_calls_and_undoes_only_in_their_own_room(
    dashboard: SimpleNamespace,
) -> None:
    """Room 2's nurse: the Pharmacy's call and undo are the 404 another clinic's would be; Triage works."""
    (pharmacy_ticket,) = _walk_ins(dashboard, dashboard.pharmacy, 1)
    (triage_ticket,) = _walk_ins(dashboard, dashboard.triage, 1)
    with dashboard.session() as db:
        call_next(db, db.get(Queue, dashboard.pharmacy), actor=_DESK)
        db.commit()
    nurse = dashboard.client("nurse.a")
    api = _api(dashboard)

    assert (
        nurse.post(f"{api}/queues/{dashboard.pharmacy}/tickets/call-next").status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert (
        nurse.post(f"{api}/tickets/{pharmacy_ticket}/undo-call").status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert _statuses(dashboard, [pharmacy_ticket]) == ["called"]

    called = nurse.post(
        f"{api}/queues/{dashboard.triage}/tickets/call-next",
        headers=_keyed("nurse-key-01"),
    )
    assert called.json()["id"] == triage_ticket
    assert nurse.post(f"{api}/tickets/{triage_ticket}/undo-call").status_code == 200


def test_the_cards_offer_the_lifecycles_buttons_with_time_at_each_step_and_the_undo_window(
    dashboard: SimpleNamespace,
) -> None:
    """Called: start, recall, no-show (asked first) and undo; recalled: start, no-show; being seen: done."""
    ids = _walk_ins(dashboard, dashboard.triage, 4)
    today = now_sast()
    moment = today.replace(hour=10, minute=0, second=0, microsecond=0)
    with dashboard.session() as db:
        queue = db.get(Queue, dashboard.triage)
        in_room, recalled, _just_called, _waiting = (db.get(Ticket, i) for i in ids)
        call_next(db, queue, actor=_DESK, moment=moment - timedelta(minutes=20))
        transition_ticket(
            db,
            in_room.id,
            TicketStatus.IN_PROGRESS,
            actor=_DESK,
            moment=moment - timedelta(minutes=12),
        )
        call_next(db, queue, actor=_DESK, moment=moment - timedelta(minutes=9))
        transition_ticket(
            db,
            recalled.id,
            TicketStatus.RECALLED,
            actor=_DESK,
            moment=moment - timedelta(minutes=4),
        )
        call_next(db, queue, actor=_DESK, moment=moment - timedelta(seconds=10))
        db.commit()
        desk = db.get(User, dashboard.ids["desk.a"])
        board = read_board(
            db, SiteAccess(site_id=dashboard.site_a, user=desk), moment=moment
        )

    (card,) = [card for card in board.cards if card.id == dashboard.triage]
    panel = {ticket.number: ticket for ticket in card.with_staff_tickets}
    assert card.next_number == "T004"
    assert [a.kind for a in panel["T001"].actions] == [ActionKind.DONE]
    assert [a.kind for a in panel["T002"].actions] == [
        ActionKind.START,
        ActionKind.NO_SHOW,
    ]
    assert [a.kind for a in panel["T003"].actions] == [
        ActionKind.START,
        ActionKind.RECALL,
        ActionKind.NO_SHOW,
        ActionKind.UNDO_CALL,
    ]
    # The in-room panel's clock: being seen for 12 minutes, called again 4 minutes ago.
    assert (panel["T001"].elapsed.label, panel["T001"].elapsed.minutes) == (
        "Being seen",
        12,
    )
    assert (panel["T002"].elapsed.label, panel["T002"].elapsed.minutes) == (
        "Called again",
        4,
    )
    # Only the call made seconds ago can be undone, until 30 seconds after it.
    assert [n for n, t in panel.items() if t.undo_until] == ["T003"]
    assert panel["T003"].undo_until == moment - timedelta(seconds=10) + timedelta(
        seconds=dashboard.settings.queue_call_undo_seconds
    )
    (no_show,) = [a for a in panel["T002"].actions if a.kind is ActionKind.NO_SHOW]
    assert no_show.confirm and no_show.confirm.startswith("Mark T002 as a no-show?")
    assert all(a.confirm is None for a in panel["T001"].actions)
    assert business_date(moment) == business_date(today)


def test_the_buttons_are_offered_to_everyone_and_enabled_only_with_the_grant(
    dashboard: SimpleNamespace,
) -> None:
    """The receptionist may call and move; the trainee reads the same card with its buttons switched off."""
    _walk_ins(dashboard, dashboard.triage, 2)
    with dashboard.session() as db:
        call_next(db, db.get(Queue, dashboard.triage), actor=_DESK)
        db.commit()

    for name, allowed in (("desk.a", True), ("trainee", False)):
        page = dashboard.client(name).get(dashboard.page(dashboard.site_a, "board"))
        assert page.status_code == status.HTTP_200_OK, name
        assert (page.context["can_call"], page.context["can_move"]) == (
            allowed,
            allowed,
        )
        (card,) = [c for c in page.context["queues"] if c.id == dashboard.triage]
        (ticket,) = card.with_staff_tickets
        assert {a.kind for a in ticket.actions} >= {
            ActionKind.START,
            ActionKind.UNDO_CALL,
        }

    assert (
        dashboard.client("trainee")
        .post(f"{_api(dashboard)}/queues/{dashboard.triage}/tickets/call-next")
        .status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_the_room_is_live_on_its_own_cards_and_stream(
    dashboard: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The nurse's cards and stream; signed out 401, another clinic 404, the front desk 403."""
    _walk_ins(dashboard, dashboard.triage, 1)
    cards = dashboard.page(dashboard.site_a, "room/cards")
    stream = dashboard.page(dashboard.site_a, "room/stream")
    nurse = dashboard.client("nurse.a")

    fragment = nurse.get(cards)
    assert fragment.status_code == status.HTTP_200_OK
    assert fragment.headers["cache-control"] == "no-store"
    assert [room.card.id for room in fragment.context["room"].rooms] == [
        dashboard.triage
    ]
    assert fragment.context["can_call"] is True

    anonymous = dashboard.anonymous()
    assert anonymous.get(cards).status_code == status.HTTP_401_UNAUTHORIZED
    assert anonymous.get(stream).status_code == status.HTTP_401_UNAUTHORIZED
    desk_b = dashboard.client("desk.b")
    assert desk_b.get(cards).status_code == status.HTTP_404_NOT_FOUND
    assert desk_b.get(stream).status_code == status.HTTP_404_NOT_FOUND
    desk = dashboard.client("desk.a")
    assert desk.get(cards).status_code == status.HTTP_403_FORBIDDEN
    assert desk.get(stream).status_code == status.HTTP_403_FORBIDDEN

    # The nurse's stream takes only Triage's events: the filter it is opened with says so.
    seen: dict[str, object] = {}

    async def one_event(site_id: str, **options: object) -> AsyncIterator[str]:
        seen.update(options)
        yield format_event(LiveEvent(LiveEventType.HEARTBEAT, site_id), 1)

    monkeypatch.setattr(live_events.broker, "stream", one_event)
    opened = nurse.get(stream)
    assert opened.status_code == status.HTTP_200_OK
    accept = seen["accept"]
    assert callable(accept)
    assert accept(
        LiveEvent(
            LiveEventType.QUEUE_UPDATED, dashboard.site_a, queue_id=dashboard.triage
        )
    )
    assert not accept(
        LiveEvent(
            LiveEventType.QUEUE_UPDATED, dashboard.site_a, queue_id=dashboard.pharmacy
        )
    )
    assert accept(LiveEvent(LiveEventType.BOARD_CONFIG_CHANGED, dashboard.site_a))


def test_request_keys_older_than_a_day_are_swept(dashboard: SimpleNamespace) -> None:
    """A key only has to outlive its retries: yesterday's is deleted, today's is kept."""
    _walk_ins(dashboard, dashboard.triage, 2)
    desk = dashboard.client("desk.a")
    url = f"{_api(dashboard)}/queues/{dashboard.triage}/tickets/call-next"
    desk.post(url, headers=_keyed("old-key-0001"))
    desk.post(url, headers=_keyed("new-key-0001"))
    with dashboard.session() as db:
        old = db.execute(
            select(QueueRequestKey).where(QueueRequestKey.key == "old-key-0001")
        ).scalar_one()
        old.created_at = now_sast() - timedelta(days=1, minutes=1)
        db.commit()
        assert purge_request_keys(db) == 1
        assert db.scalars(select(QueueRequestKey.key)).all() == ["new-key-0001"]
