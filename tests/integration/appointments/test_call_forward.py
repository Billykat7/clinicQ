"""The virtual waiting room end to end, over HTTP and the real sweep (Issue 86).

Against the queue fixture's clinic A (open around the clock), with its virtual waiting room switched on by
its manager through the settings API:

* **a patient with a 30-minute trip is told to leave with enough lead to arrive before their turn**: the
  line is called down one patient at a time, the ticket page and the sweep are read at each step, and the
  alert goes exactly once, when the earliest likely turn is still at least the trip away;
* **the alert and the screen use one estimate**: the alert's wait is the page's wait, word for word;
* **"On my way" reaches reception**, in the clinic's ticket list, once, and only for the patient's own ticket;
* **an unanswered alert costs nothing**: the ticket keeps its place through any number of sweeps, and only
  the recall rule (Issue 43) moves it once it has been called;
* **the feature is per clinic**: off, nobody is asked, nothing is kept and nobody is told to leave;
* **the trip is optional**: unsaid, it is 15 minutes.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette import status

from src.commons.enums import (
    ActorKind,
    ConsentPurpose,
    PatientChannel,
    PatientEvent,
    TicketStatus,
)
from src.commons.time import now_sast
from src.database.models import Notification, Patient, Queue, Site, Ticket
from src.modules.appointments import virtual_waiting
from src.modules.discovery import profile as profile_service
from src.modules.patients.consent import record_consent
from src.modules.queue.lifecycle import Actor, call_next
from src.modules.queue.timers import run_recall_timers
from src.web.join import join_page
from tests.integration.queue.conftest import SITE_A, SITE_B, queue_settings

logger = logging.getLogger(__name__)

_DESK = Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example")
_SETTINGS = f"/api/v1/sites/{SITE_A}/settings/virtual-waiting"


def _switch(desk: SimpleNamespace, on: bool) -> dict[str, Any]:
    answer = desk.staff("manager.a").put(
        _SETTINGS, json={"virtual_waiting_enabled": on}
    )
    assert answer.status_code == status.HTTP_200_OK, answer.text
    return answer.json()


def _patient(desk: SimpleNamespace) -> tuple[TestClient, str]:
    """A consenting patient's web client."""
    client, patient_id = desk.patient()
    with desk.session() as db:
        record_consent(
            db,
            db.get_one(Patient, patient_id),
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
        db.commit()
    return client, patient_id


def _join(desk: SimpleNamespace, client: TestClient, **body: Any) -> dict[str, Any]:
    answer = client.post(desk.join_path(desk.triage), json=body)
    assert answer.status_code == status.HTTP_201_CREATED, answer.text
    return answer.json()


def _page(client: TestClient, joined: dict[str, Any]) -> dict[str, Any]:
    token = joined["page_url"].removeprefix("/t/")
    return client.get(f"/api/v1/tickets/{token}").json()


def _alerts(desk: SimpleNamespace, ticket_id: str) -> list[Notification]:
    with desk.session() as db:
        return list(
            db.execute(
                select(Notification).where(
                    Notification.event == PatientEvent.LEAVE_NOW.value,
                    Notification.dedupe_key.startswith(ticket_id),
                )
            ).scalars()
        )


def _walk_ins(desk: SimpleNamespace, count: int) -> None:
    front = desk.staff("desk.a")
    for _ in range(count):
        assert (
            front.post(desk.walk_in_path(desk.triage), json={}).status_code
            == status.HTTP_201_CREATED
        )


def _set_service_minutes(desk: SimpleNamespace, minutes: int) -> None:
    with desk.session() as db:
        db.get_one(Queue, desk.triage.id).expected_service_minutes = minutes
        db.commit()


def test_a_30_minute_trip_is_told_to_leave_once_with_enough_lead_to_arrive_before_the_turn(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 1: call the line down; the alert comes once, while the turn is still 30+ minutes off."""
    _switch(desk, True)
    _set_service_minutes(desk, 10)
    _walk_ins(desk, 8)
    client, _ = _patient(desk)
    joined = _join(desk, client, travel_minutes=30)
    ticket_id = joined["ticket"]["id"]
    assert joined["ticket"]["travel_minutes"] == 30

    steps: list[tuple[int, int, int, bool, bool]] = []
    alerted_page: dict[str, Any] | None = None
    previous_low: int | None = None
    for _ in range(9):
        moment = now_sast()
        page = _page(client, joined)
        with desk.session() as db:
            told = virtual_waiting.run_call_forward(db, moment=moment)
            db.commit()
        wait, travel = page["wait"], page["call_forward"]
        steps.append(
            (
                page["waiting_ahead"],
                wait["low_minutes"],
                wait["high_minutes"],
                travel["due"],
                bool(told),
            )
        )
        if told:
            alerted_page = page
            break
        previous_low = wait["low_minutes"]
        with desk.session() as db:
            call_next(db, db.get_one(Queue, desk.triage.id), actor=_DESK)
            db.commit()

    for ahead, low, high, due, told in steps:
        logger.info(
            "ahead %s: wait %s–%s min, due %s, alert sent %s",
            ahead,
            low,
            high,
            due,
            told,
        )
    assert alerted_page is not None, steps
    # Enough lead: leaving now, a 30-minute trip ends before the earliest likely turn.
    assert alerted_page["wait"]["low_minutes"] >= 30
    assert alerted_page["wait"]["low_minutes"] <= 30 + 10
    # Not early: one step before, the earliest turn was still more than the trip and the margin away.
    assert previous_low is not None and previous_low > 30 + 10
    [alert] = _alerts(desk, ticket_id)
    # The same estimate: the alert says the wait the page showed at that moment.
    assert alert.payload["wait"] == alerted_page["wait"]["label"]
    assert alert.payload["minutes"] == 30

    # Never twice, however often the sweep runs.
    for _ in range(3):
        with desk.session() as db:
            assert virtual_waiting.run_call_forward(db) == 0
            db.commit()
    assert len(_alerts(desk, ticket_id)) == 1
    after = _page(client, joined)["call_forward"]
    assert after["alerted_at"] and after["due"] is True


def test_on_my_way_reaches_reception_once_and_only_for_the_patients_own_ticket(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 2: tap On my way; the front desk's ticket list says so."""
    _switch(desk, True)
    client, _ = _patient(desk)
    joined = _join(desk, client, travel_minutes=20)
    ticket_id = joined["ticket"]["id"]
    url = _page(client, joined)["call_forward"]["on_my_way_url"]
    stranger, _ = _patient(desk)

    first = client.post(url, json={})
    again = client.post(url, json={})
    theirs = stranger.post(url, json={})

    assert first.status_code == status.HTTP_200_OK, first.text
    said = first.json()["ticket"]["on_my_way_at"]
    assert said and again.json()["ticket"]["on_my_way_at"] == said
    assert (
        first.json()["message"]
        == f"Reception knows {joined['ticket']['number']} is on the way."
    )
    assert theirs.status_code == status.HTTP_404_NOT_FOUND
    listed = desk.staff("desk.a").get(f"/api/v1/sites/{SITE_A}/tickets").json()
    [row] = [item for item in listed["items"] if item["id"] == ticket_id]
    assert (row["on_my_way_at"], row["travel_minutes"]) == (said, 20)
    assert _page(client, joined)["call_forward"]["on_my_way_url"] is None


def test_an_unanswered_alert_keeps_the_place_until_the_recall_rule_applies(
    desk: SimpleNamespace,
) -> None:
    """Alerted and silent: still waiting in the same place hours of sweeps later; only a call and the timer move it."""
    _switch(desk, True)
    _walk_ins(desk, 2)
    client, _ = _patient(desk)
    joined = _join(
        desk, client, travel_minutes=60
    )  # longer than the wait: told at once, at join
    ticket_id = joined["ticket"]["id"]
    assert len(_alerts(desk, ticket_id)) == 1
    place = _page(client, joined)["position"]

    later = now_sast()
    for hours in range(1, 6):
        with desk.session() as db:
            virtual_waiting.run_call_forward(db, moment=later + timedelta(hours=hours))
            db.commit()
    page = _page(client, joined)
    assert (page["status"], page["position"]) == (TicketStatus.WAITING.value, place)

    with desk.session() as db:
        queue = db.get_one(Queue, desk.triage.id)
        for _ in range(3):
            called = call_next(db, queue, actor=_DESK)
        db.commit()
        assert called.id == ticket_id
        called_at = now_sast()
        settings = queue_settings()
        early = run_recall_timers(
            db, moment=called_at + timedelta(minutes=1), settings=settings
        )
        db.commit()
        assert early.moved == 0
        swept = run_recall_timers(
            db, moment=called_at + timedelta(minutes=6), settings=settings
        )
        db.commit()
        assert ticket_id in swept.recalled
        assert db.get_one(Ticket, ticket_id).status == TicketStatus.RECALLED.value


def test_switched_off_nobody_is_asked_nothing_is_kept_and_nobody_is_told(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 3, and the switch's own rules: off by default, a manager's, audited."""
    default = desk.staff("manager.a").get(_SETTINGS).json()
    assert default["virtual_waiting_enabled"] is False
    assert default["default_travel_minutes"] == 15
    assert (
        desk.staff("desk.a")
        .put(_SETTINGS, json={"virtual_waiting_enabled": True})
        .status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert desk.staff("desk.b").get(_SETTINGS).status_code == status.HTTP_404_NOT_FOUND

    client, _ = _patient(desk)
    joined = _join(desk, client, travel_minutes=120)
    page = _page(client, joined)
    refused = client.post(
        f"/api/v1/patients/me/tickets/{joined['ticket']['id']}/on-my-way", json={}
    )
    with desk.session() as db:
        told = virtual_waiting.run_call_forward(db)
        db.commit()

    assert joined["ticket"]["travel_minutes"] is None
    assert page["call_forward"] is None
    assert told == 0 and _alerts(desk, joined["ticket"]["id"]) == []
    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["code"] == virtual_waiting.NOT_OFFERED_CODE


def test_the_join_page_asks_only_where_the_clinic_runs_it_and_the_trip_defaults_to_15(
    desk: SimpleNamespace,
) -> None:
    """The travel question is part of the page only when switched on; an unsaid trip is 15 minutes."""
    with desk.session() as db:
        profile = profile_service.clinic_profile(db, db.get_one(Site, SITE_A).slug)
    assert profile is not None
    assert join_page(profile, join_enabled=True, signed_in=True).travel_choices == ()
    asked = join_page(profile, join_enabled=True, signed_in=True, virtual_waiting=True)
    assert [choice.minutes for choice in asked.travel_choices][:4] == [0, 5, 10, 15]
    assert asked.travel_choices[0].label == "I am at the clinic already"
    assert asked.travel_default == 15

    _switch(desk, True)
    client, _ = _patient(desk)
    joined = _join(desk, client)
    assert joined["ticket"]["travel_minutes"] == 15
    other = desk.patient()[0].post(
        f"/api/v1/clinics/{SITE_B}/queues/{desk.other_triage.id}/tickets",
        json={"travel_minutes": 30},
    )
    assert (
        other.json()["ticket"]["travel_minutes"] is None
    )  # clinic B has not switched it on
