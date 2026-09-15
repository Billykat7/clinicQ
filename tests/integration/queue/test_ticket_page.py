"""The patient's ticket page: the data contract behind it and its live stream (Issue 68).

Per ``docs/IDE/RULES/testing-strategy.mdc`` these tests read JSON and server-sent events, never the
page's HTML. The browser half (the page updating without a reload, the "not live" warning, two-tap
cancel, 320 px) is in ``tests/e2e/patient/test_ticket_page.py``.

What is proven here:

* **the link is a long unguessable token, not a sequential id**: 43 URL-safe characters, unrelated from
  one ticket to the next, and a ticket's id, number or reference code opens nothing;
* **position and estimate are live**: every read reflects the queue as it is, and the stream sends the
  ticket again after each change in its queue, starting with the whole ticket;
* **"you are next" and "please come in now"** are headlines of their own, not a position of 1;
* **the wait is a range**, and the data says when it was read and how soon to call it stale;
* **following is not owning**: only the ticket's own signed-in patient is offered cancellation, and
  cancelling through it ends the ticket;
* **nothing about the patient** is in the data.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette import status

from src.commons.enums import (
    ActorKind,
    LiveEventType,
    TicketPageHeadline,
    TicketStatus,
    TransferReason,
)
from src.core import live_events
from src.core.live_events import LiveEvent
from src.database.models import Queue, Ticket
from src.modules.queue.lifecycle import Actor, call_next, transition_ticket

_DESK = Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _join(desk: SimpleNamespace) -> tuple[TestClient, dict[str, Any]]:
    """A new patient joins Triage from the web; their client and the join answer."""
    client, _patient_id = desk.patient()
    answer = client.post(desk.join_path(desk.triage), json={})
    assert answer.status_code == status.HTTP_201_CREATED, answer.text
    return client, answer.json()


def _token(answer: dict[str, Any]) -> str:
    """The page token from a join answer's ``page_url``."""
    page_url = answer["page_url"]
    assert page_url.startswith("/t/")
    return page_url.removeprefix("/t/")


def _page(client: TestClient, token: str) -> dict[str, Any]:
    """The page data, asserting it was served uncached."""
    response = client.get(f"/api/v1/tickets/{token}")
    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _call(desk: SimpleNamespace) -> None:
    """The desk calls the next patient in Triage."""
    with desk.session() as db:
        call_next(db, db.get_one(Queue, desk.triage.id), actor=_DESK)
        db.commit()


# --- the link --------------------------------------------------------------------------------


def test_the_link_is_a_long_unguessable_token_and_nothing_else_opens_the_page(
    desk: SimpleNamespace,
) -> None:
    """Criterion: a long unguessable token, not a sequential id."""
    _, first = _join(desk)
    _, second = _join(desk)
    tokens = [_token(first), _token(second)]
    stranger = TestClient(desk.app)

    assert all(_TOKEN.match(token) for token in tokens)
    assert len(set(tokens)) == 2 and tokens[0][:8] != tokens[1][:8]
    ticket = first["ticket"]
    for guess in (
        ticket["id"],
        ticket["number"],
        ticket["reference_code"],
        ticket["reference_code"].replace("-", ""),
        tokens[0][:-1] + ("A" if tokens[0][-1] != "A" else "B"),
    ):
        response = stranger.get(f"/api/v1/tickets/{guess}")
        assert response.status_code == status.HTTP_404_NOT_FOUND, guess
        assert response.json()["detail"] == "No such ticket."
    assert stranger.get(f"/t/{ticket['id']}").status_code == status.HTTP_404_NOT_FOUND


def test_a_patients_own_tickets_carry_the_link(desk: SimpleNamespace) -> None:
    """A patient who closed the page finds the same link again among their tickets."""
    client, answer = _join(desk)
    mine = client.get("/api/v1/patients/me/tickets").json()
    assert [row["page_url"] for row in mine] == [answer["page_url"]]


def test_the_page_data_names_nobody(desk: SimpleNamespace) -> None:
    """A forwarded link shows a number, a clinic and a place in line; nothing about the patient."""
    _, answer = _join(desk)
    with desk.session() as db:
        ticket = db.get_one(Ticket, answer["ticket"]["id"])
        patient_id = ticket.patient_id
    body = json.dumps(_page(TestClient(desk.app), _token(answer)))
    assert answer["ticket"]["id"] not in body
    assert patient_id is not None and patient_id not in body
    assert "phone" not in json.dumps(
        {k: v for k, v in json.loads(body).items() if k != "clinic"}
    )


# --- live position, headline and wait range ---------------------------------------------------


def test_position_and_wait_follow_the_queue_and_the_headlines_say_what_to_do(
    desk: SimpleNamespace,
) -> None:
    """Criteria: position and estimate update without a refresh; "you are next" and "come in" stand out."""
    _join(desk)
    _join(desk)
    _, mine = _join(desk)
    token = _token(mine)
    family = TestClient(desk.app)

    third = _page(family, token)
    assert (third["headline"], third["position"], third["waiting_ahead"]) == (
        TicketPageHeadline.WAITING.value,
        3,
        2,
    )
    wait = third["wait"]
    assert wait["low_minutes"] < wait["high_minutes"] and "–" in wait["label"]
    assert datetime.fromisoformat(third["as_of"]).utcoffset() is not None
    assert third["refresh_seconds"] == 15 and third["stale_after_seconds"] == 45
    assert third["stream_url"] == f"/t/{token}/stream"

    _call(desk)
    second = _page(family, token)
    assert (second["position"], second["waiting_ahead"]) == (2, 1)
    assert second["wait"]["high_minutes"] <= wait["high_minutes"]

    _call(desk)
    next_up = _page(family, token)
    assert next_up["headline"] == TicketPageHeadline.NEXT.value
    assert next_up["position"] == 1 and next_up["waiting_ahead"] == 0

    _call(desk)
    called = _page(family, token)
    assert called["headline"] == TicketPageHeadline.CALLED.value
    assert called["status"] == TicketStatus.CALLED.value
    assert called["position"] is None and called["wait"] is None
    assert called["called_at"] is not None
    assert called["queue"]["name"] == "Triage"


def test_a_finished_ticket_stops_following_and_a_transfer_links_the_next_leg(
    desk: SimpleNamespace,
) -> None:
    """A transferred ticket's page points at the new ticket's page, so family follows the visit."""
    _, answer = _join(desk)
    token = _token(answer)
    staff = desk.staff("desk.a")
    _call(desk)
    with desk.session() as db:
        transition_ticket(
            db, answer["ticket"]["id"], TicketStatus.IN_PROGRESS, actor=_DESK
        )
        db.commit()
    moved = staff.post(
        f"/api/v1/sites/{desk.triage.site_id}/tickets/{answer['ticket']['id']}/transfer",
        json={
            "queue_id": desk.pharmacy.id,
            "reason": TransferReason.NEXT_STEP.value,
        },
    )
    assert moved.status_code == status.HTTP_200_OK, moved.text

    old = _page(TestClient(desk.app), token)
    assert old["headline"] == TicketPageHeadline.TRANSFERRED.value
    assert old["stream_url"] is None
    assert old["next_page_url"] and old["next_page_url"] != f"/t/{token}"
    new = _page(TestClient(desk.app), old["next_page_url"].removeprefix("/t/"))
    assert new["queue"]["name"] == "Pharmacy"
    assert new["headline"] == TicketPageHeadline.NEXT.value


# --- following is not owning ---------------------------------------------------------------------


def test_only_the_tickets_own_patient_is_offered_cancellation_and_it_works(
    desk: SimpleNamespace,
) -> None:
    """Criterion: cancelling is the patient's, and confirms clearly; a shared link cannot cancel."""
    owner, answer = _join(desk)
    token = _token(answer)
    other_patient, _ = _join(desk)

    for follower in (TestClient(desk.app), other_patient):
        assert _page(follower, token)["cancel_url"] is None
    cancel_url = _page(owner, token)["cancel_url"]
    assert cancel_url == f"/api/v1/patients/me/tickets/{answer['ticket']['id']}/cancel"

    refused = other_patient.post(cancel_url, json={})
    assert refused.status_code == status.HTTP_404_NOT_FOUND
    done = owner.post(cancel_url, json={})
    assert done.status_code == status.HTTP_200_OK
    assert done.json()["message"].startswith(
        f"Ticket {answer['ticket']['number']} is cancelled."
    )

    after = _page(owner, token)
    assert after["headline"] == TicketPageHeadline.CANCELLED.value
    assert after["cancel_url"] is None and after["stream_url"] is None


def test_a_called_patient_is_not_offered_cancellation(desk: SimpleNamespace) -> None:
    """After the call the desk decides; the page says so by not offering the button."""
    owner, answer = _join(desk)
    _call(desk)
    assert _page(owner, _token(answer))["cancel_url"] is None


# --- the stream -----------------------------------------------------------------------------------


def parse_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    """``[(event name, data), …]`` from a server-sent events body."""
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if ": " in line
        )
        events.append((fields["event"], json.loads(fields["data"])))
    return events


@contextmanager
def scripted(
    monkeypatch: pytest.MonkeyPatch, script: Callable[[str], list[LiveEvent]]
) -> Iterator[None]:
    """Replace the patient broker's endless events with a heartbeat followed by ``script(site_id)``.

    ``accept`` is honoured as the real broker honours it, so the queue filter is tested too.
    """

    async def events(
        site_id: str, *, accept: Callable[[LiveEvent], bool] | None = None, **_: object
    ) -> AsyncIterator[LiveEvent]:
        yield LiveEvent(LiveEventType.HEARTBEAT, site_id)
        for event in script(site_id):
            if accept is None or accept(event):
                yield event

    with monkeypatch.context() as patch:
        patch.setattr(live_events.patient_broker, "events", events)
        yield


def test_the_stream_opens_with_the_ticket_and_sends_it_again_after_each_change_in_its_queue(
    desk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board's envelope (Issue 57): ``ticket.state`` first, a change elsewhere is not sent."""
    _join(desk)
    _, answer = _join(desk)
    token = _token(answer)

    def script(site_id: str) -> list[LiveEvent]:
        _call(desk)  # while the stream is open: the change the next event is about
        return [
            LiveEvent(LiveEventType.QUEUE_UPDATED, site_id, queue_id=desk.pharmacy.id),
            LiveEvent(LiveEventType.TICKET_CALLED, site_id, queue_id=desk.triage.id),
            LiveEvent(LiveEventType.HEARTBEAT, site_id),
        ]

    with scripted(monkeypatch, script):
        response = TestClient(desk.app).get(f"/t/{token}/stream")
    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_events(response.text)
    assert [name for name, _ in events] == ["ticket.state", "ticket.state", "heartbeat"]
    (_, opened), (_, changed), _beat = events
    assert opened["ticket"]["position"] == 2
    assert changed["ticket"]["position"] == 1
    assert changed["ticket"]["headline"] == TicketPageHeadline.NEXT.value


def test_a_stream_for_a_finished_ticket_says_so_once_and_ends(
    desk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing about a cancelled ticket will change, so its stream does not stay open."""
    owner, answer = _join(desk)
    token = _token(answer)
    owner.post(f"/api/v1/patients/me/tickets/{answer['ticket']['id']}/cancel", json={})
    with scripted(
        monkeypatch, lambda site_id: [LiveEvent(LiveEventType.HEARTBEAT, site_id)]
    ):
        body = TestClient(desk.app).get(f"/t/{token}/stream").text
    assert [name for name, _ in parse_events(body)] == ["ticket.state"]


def test_an_unknown_stream_is_404_and_a_full_clinic_is_503_with_retry_after(
    desk: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Beyond the patients' own budget the page polls instead; the board's budget is untouched."""
    _, answer = _join(desk)
    client = TestClient(desk.app)
    assert client.get("/t/" + "x" * 43 + "/stream").status_code == 404

    async def full(site_id: str, **_: object) -> AsyncIterator[LiveEvent]:
        raise live_events.TooManySubscribersError(site_id)
        yield  # pragma: no cover  (makes this an async generator)

    monkeypatch.setattr(live_events.patient_broker, "events", full)
    refused = client.get(f"{answer['page_url']}/stream")
    assert refused.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert refused.headers["retry-after"] == "30"
    assert live_events.patient_broker is not live_events.broker
    assert (
        live_events.patient_broker._max_per_site
        == live_events.MAX_PATIENT_STREAMS_PER_SITE
        > live_events.MAX_SUBSCRIBERS_PER_SITE
    )
