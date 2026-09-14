"""Every board endpoint, searched for a patient's name: non-negotiable 4 over real HTTP (Issue 58).

    Under ``number_only``, a patient name is not in the response payload at all.

The proof is the one the issue asks for: seed a patient with an unmistakable name who has agreed to
**everything** (name, reason, and the reason for this visit), put them on the board, then fetch every
board endpoint as anyone and as the clinic's own staff and search the JSON for the name, and for the
``name`` and ``comment`` keys. Every route under ``/display`` is found from the application, not listed
here, so a board endpoint added tomorrow without a case in :data:`_READERS` fails this file.

The other modes are then walked through: ``name_lite`` shows a shortened name to the clinic's own
screen only; a withdrawal takes the name off the very next response; a reason needs ``full``, the
clinic's switch, the patient's standing consent **and** this visit's. JSON only, never HTML
(``docs/IDE/RULES/testing-strategy.mdc``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette import status

from src.commons.enums import (
    ActorKind,
    ConsentPurpose,
    DisplayMode,
    LiveEventType,
    SiteStatus,
    TicketStatus,
)
from src.core import live_events
from src.core.live_events import LiveEvent
from src.database.models import Queue
from src.modules.display.enums import BoardPersonalField
from src.modules.display.projection import NOW_SERVING_LIMIT, UP_NEXT_LIMIT
from src.modules.queue.lifecycle import Actor, call_next
from tests.factories import SiteFactory
from tests.integration.display.conftest import NOMVULA, NOMVULA_LITE, REASON
from tests.integration.display.test_display_sse import parse_events
from tests.unit.security.test_api_route_gates import _walk

_DESK = Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example")
_PERSONAL_KEYS = {field.value for field in BoardPersonalField}


def _keys(value: Any) -> Iterator[str]:
    """Every key anywhere in a JSON value."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _json(client: TestClient, path: str) -> Any:
    """GET ``path`` and return its JSON body, asserting a successful, uncached answer."""
    response = client.get(path)
    assert response.status_code == status.HTTP_200_OK, (path, response.text)
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _page(client: TestClient, path: str) -> tuple[Any, str]:
    """GET the board page: the payload it was rendered with, and its whole body to search."""
    response = client.get(path)
    assert response.status_code == status.HTTP_200_OK, (path, response.text)
    assert response.headers["cache-control"] == "no-store"
    return response.context["payload"], response.text


def _state(client: TestClient, path: str) -> tuple[Any, str]:
    """GET the board's JSON: the payload, and the raw body to search."""
    return _json(client, path), client.get(path).text


def _stream(client: TestClient, path: str) -> tuple[Any, str]:
    """Open the live stream: the board its first event carries, and every byte of a short stream.

    The broker's endless events are replaced by a heartbeat and one ``ticket.called``, so the response
    ends and can be read whole; the route and its projection are the real ones (Issue 57).
    """
    site_id = path.split("/")[2]

    async def events(site: str, **_: object) -> AsyncIterator[LiveEvent]:
        yield LiveEvent(LiveEventType.HEARTBEAT, site)
        yield LiveEvent(LiveEventType.TICKET_CALLED, site)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(live_events.broker, "events", events)
        response = client.get(path)
    assert response.status_code == status.HTTP_200_OK, (path, response.text)
    boards = [
        data["board"] for _, data in parse_events(response.text) if "board" in data
    ]
    assert len(boards) == 2 and all(b["site_id"] == site_id for b in boards)
    return boards[0], response.text


#: How each board route is read, by path template: ``(its payload, every byte it sent)``. A route under
#: /display with no entry fails the sweep below, so a new endpoint cannot ship without being searched.
_READERS: dict[str, Callable[[TestClient, str], tuple[Any, str]]] = {
    "/display/{site_id}": _page,
    "/display/{site_id}/state": _state,
    "/display/{site_id}/stream": _stream,
}


def _board_routes(board: SimpleNamespace) -> list[str]:
    """Every GET route the application serves under ``/display``."""
    return sorted(
        path
        for path, route in _walk(board.world.app.routes)
        if path.startswith("/display") and "GET" in (route.methods or ())
    )


def _everything_agreed(board: SimpleNamespace) -> str:
    """Nomvula, who agreed to every display purpose, called in Triage with a consented reason."""
    nomvula = board.patient()
    for purpose in (ConsentPurpose.DISPLAY_NAME, ConsentPurpose.DISPLAY_COMMENT):
        board.consent(nomvula, purpose, True)
    board.ticket(board.world.triage, nomvula, reason=REASON, comment_consent=True)
    board.ticket(board.world.triage, board.patient(f"{NOMVULA} Junior"))
    with board.session() as db:
        call_next(db, db.get_one(Queue, board.world.triage), actor=_DESK)
        db.commit()
    return nomvula


def _viewers(board: SimpleNamespace) -> dict[str, TestClient]:
    """Anyone at all, the clinic's own manager, and a receptionist at another clinic."""
    return {
        "anonymous": board.world.anonymous(),
        "manager.a": board.world.client("manager.a"),
        "desk.b": board.world.client("desk.b"),
    }


def test_under_number_only_no_board_endpoint_carries_a_name_or_a_comment_key_for_anyone(
    board: SimpleNamespace,
) -> None:
    """The issue's proof: every endpoint, every viewer, the JSON searched for the name and the keys."""
    _everything_agreed(board)
    routes = _board_routes(board)
    assert routes, "no board routes found: the sweep would pass vacuously"
    assert set(routes) <= set(_READERS), (
        f"board routes with no privacy case: {sorted(set(routes) - set(_READERS))}"
    )

    for viewer, client in _viewers(board).items():
        for route in routes:
            body, raw = _READERS[route](
                client, route.format(site_id=board.world.site_a)
            )
            # The payload, and everything the response carried (a page's markup included): a privacy
            # search of the bytes sent, not a test of how the page is drawn.
            text = json.dumps(body, ensure_ascii=False) + raw
            assert NOMVULA.split()[0] not in text, (viewer, route)
            assert "Zwelithini" not in text, (viewer, route)
            assert REASON not in text, (viewer, route)
            assert not _PERSONAL_KEYS & set(_keys(body)), (viewer, route)
            assert body["display_mode"] == DisplayMode.NUMBER_ONLY.value
            # The number is still there: the privacy rule removes people, not the queue.
            triage = next(q for q in body["queues"] if q["id"] == board.world.triage)
            assert [t["number"] for t in triage["now_serving"]] == ["T001"]
            assert [t["number"] for t in triage["up_next"]] == ["T002"]


def test_name_lite_shows_a_short_name_to_the_clinics_own_screen_and_numbers_to_everyone_else(
    board: SimpleNamespace,
) -> None:
    """The mode the clinic chose applies to its screen; an address anyone can type stays numbers only."""
    _everything_agreed(board)
    board.display(DisplayMode.NAME_LITE, show_comment=True)
    viewers = _viewers(board)
    path = board.state(board.world.site_a)

    own = _json(viewers["manager.a"], path)
    serving = next(q for q in own["queues"] if q["id"] == board.world.triage)[
        "now_serving"
    ]
    assert own["display_mode"] == DisplayMode.NAME_LITE.value
    assert serving == [
        {
            "number": "T001",
            "status": TicketStatus.CALLED.value,
            "called_at": serving[0]["called_at"],
            "name": NOMVULA_LITE,
        }
    ]
    # Never the full name, and never a reason beside a name under name_lite.
    own_text = json.dumps(own, ensure_ascii=False)
    assert NOMVULA not in own_text and REASON not in own_text
    assert BoardPersonalField.COMMENT.value not in set(_keys(own))

    for viewer in ("anonymous", "desk.b"):
        other = _json(viewers[viewer], path)
        assert other["display_mode"] == DisplayMode.NUMBER_ONLY.value, viewer
        assert not _PERSONAL_KEYS & set(_keys(other)), viewer
        assert "Nomvula" not in json.dumps(other, ensure_ascii=False), viewer


def test_withdrawing_display_consent_takes_the_name_off_the_next_update(
    board: SimpleNamespace,
) -> None:
    """Consent is read at render time, so the response after a withdrawal has no name key."""
    nomvula = _everything_agreed(board)
    board.display(DisplayMode.NAME_LITE)
    manager = board.world.client("manager.a")
    path = board.state(board.world.site_a)

    before = _json(manager, path)
    assert NOMVULA_LITE in json.dumps(before, ensure_ascii=False)

    board.consent(nomvula, ConsentPurpose.DISPLAY_NAME, False)

    after = _json(manager, path)
    triage = next(q for q in after["queues"] if q["id"] == board.world.triage)
    assert triage["now_serving"][0]["number"] == "T001"
    assert BoardPersonalField.NAME.value not in triage["now_serving"][0]
    assert "Nomvula Z." not in json.dumps(after, ensure_ascii=False)


def test_a_reason_needs_full_mode_the_clinic_switch_and_both_consents(
    board: SimpleNamespace,
) -> None:
    """A reason beside a full name: full, reasons switched on, standing consent, and this visit's."""
    nomvula = board.patient()
    board.consent(nomvula, ConsentPurpose.DISPLAY_NAME, True)
    board.consent(nomvula, ConsentPurpose.DISPLAY_COMMENT, True)
    board.ticket(board.world.pharmacy, nomvula, reason=REASON, comment_consent=False)
    manager = board.world.client("manager.a")
    path = board.state(board.world.site_a)

    def pharmacy_entry() -> dict[str, Any]:
        body = _json(manager, path)
        pharmacy = next(q for q in body["queues"] if q["id"] == board.world.pharmacy)
        return dict(pharmacy["up_next"][0])

    board.display(DisplayMode.FULL, show_comment=False)
    assert pharmacy_entry() == {
        "number": "P001",
        "status": TicketStatus.WAITING.value,
        "name": NOMVULA,
    }

    board.display(DisplayMode.FULL, show_comment=True)
    # Standing consent and the clinic's switch, but no consent for this visit: no reason.
    assert BoardPersonalField.COMMENT.value not in pharmacy_entry()

    board.ticket(board.world.pharmacy, board.patient("Sipho Walk"), reason=REASON)
    second = board.patient("Thandeka Mhlongo-Radebe")
    board.consent(second, ConsentPurpose.DISPLAY_NAME, True)
    board.consent(second, ConsentPurpose.DISPLAY_COMMENT, True)
    board.ticket(
        board.world.pharmacy, second, reason="Wound dressing", comment_consent=True
    )
    body = _json(manager, path)
    entries = next(q for q in body["queues"] if q["id"] == board.world.pharmacy)[
        "up_next"
    ]
    assert entries[2] == {
        "number": "P003",
        "status": TicketStatus.WAITING.value,
        "name": "Thandeka Mhlongo-Radebe",
        "comment": "Wound dressing",
    }
    # The patient who never agreed to anything shows a number and no keys.
    assert entries[1] == {"number": "P002", "status": TicketStatus.WAITING.value}

    # And withdrawing the standing reason consent removes it on the next update.
    board.consent(second, ConsentPurpose.DISPLAY_COMMENT, False)
    entries = next(
        q for q in _json(manager, path)["queues"] if q["id"] == board.world.pharmacy
    )["up_next"]
    assert BoardPersonalField.COMMENT.value not in entries[2]


def test_the_board_lists_the_newest_calls_first_and_the_waiting_line_in_call_order(
    board: SimpleNamespace,
) -> None:
    """Now serving is the latest calls, capped; up next is the call order, capped; the count is all."""
    total = NOW_SERVING_LIMIT + UP_NEXT_LIMIT + 3
    numbers = [board.ticket(board.world.triage) for _ in range(total)]
    with board.session() as db:
        queue = db.get_one(Queue, board.world.triage)
        for _ in range(NOW_SERVING_LIMIT + 1):
            call_next(db, queue, actor=_DESK)
        db.commit()

    body = _json(board.world.anonymous(), board.state(board.world.site_a))
    triage = next(q for q in body["queues"] if q["id"] == board.world.triage)
    called = numbers[: NOW_SERVING_LIMIT + 1]
    assert [t["number"] for t in triage["now_serving"]] == list(reversed(called))[
        :NOW_SERVING_LIMIT
    ]
    waiting = numbers[NOW_SERVING_LIMIT + 1 :]
    assert [t["number"] for t in triage["up_next"]] == waiting[:UP_NEXT_LIMIT]
    assert triage["waiting"] == len(waiting)
    assert (triage["label"], triage["room"]) == ("Triage", "Room 2")
    assert [q["id"] for q in body["queues"]] == [
        board.world.triage,
        board.world.pharmacy,
    ]


def test_a_clinic_with_no_public_board_answers_exactly_like_one_that_does_not_exist(
    board: SimpleNamespace,
) -> None:
    """A draft clinic's id is not confirmed by its answer: the same 404 as a made-up id."""
    with board.session() as db:
        draft = SiteFactory.create(db, name="Draft Clinic", status=SiteStatus.DRAFT)
        db.commit()
        draft_id = draft.id
    anyone = board.world.anonymous()
    hidden = anyone.get(board.state(draft_id))
    unknown = anyone.get(board.state("0199b0c0-0000-7000-8000-00000000dead"))
    assert hidden.status_code == unknown.status_code == status.HTTP_404_NOT_FOUND
    # Everything but the request's own id is identical.
    assert {**hidden.json(), "request_id": None} == {
        **unknown.json(),
        "request_id": None,
    }
