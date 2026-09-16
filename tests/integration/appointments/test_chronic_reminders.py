"""Repeating collections: the reminder, the one follow-up and the one-tap join (Issue 85).

Against the queue fixture's clinic A, with the sweep's own clock:

* **a 28-day repeat reminds once** on the due day, rolls forward from the day the collection actually
  happened, and reminds again a cycle later;
* **a missed collection gets exactly one follow-up**, however many sweeps run, and the cycle then moves on
  instead of nagging;
* **the join action is one interaction**: the reminder's token takes a place in the collection queue, in
  the same sequence as everyone else, and a second tap gives the same number;
* **a reply of COLLECT** does the same over SMS;
* **stopping** — by the clinic, or by the patient's own STOP — sends nothing more;
* **the clinic's day**: nothing is sent for a due date the clinic is shut on until it opens;
* **adherence** is reportable per queue.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette import status

from src.commons.enums import (
    ActorKind,
    ConsentPurpose,
    NotificationChannel,
    NotificationStatus,
    NotificationTemplate,
    PatientChannel,
    TicketStatus,
)
from src.commons.time import APP_TIMEZONE, business_date, now_sast
from src.core.config import get_settings
from src.database.models import (
    ChronicSchedule,
    Notification,
    Patient,
    SiteOpeningHours,
    Ticket,
)
from src.modules.appointments import chronic
from src.modules.notifications.transports import NoopTransport, use_transports
from src.modules.patients.consent import record_consent
from src.modules.queue.lifecycle import Actor, transition_ticket
from tests.factories import PatientFactory
from tests.integration.queue.conftest import SITE_A, queue_settings

_TOKEN = "cc" * 22
_DESK = Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example")


@pytest.fixture
def reachable():
    """SMS reaches every patient in these tests."""
    with use_transports(
        NoopTransport(channel=NotificationChannel.SMS, address="+27820000001")
    ):
        yield


def _at(day_offset: int, hour: int = 9) -> datetime:
    """A fixed moment, so a run at any hour of the day reads the same days throughout."""
    return datetime.combine(
        business_date(now_sast()) + timedelta(days=day_offset),
        time(hour),
        tzinfo=APP_TIMEZONE,
    )


def _schedule(
    desk: SimpleNamespace,
    *,
    due_in_days: int = 0,
    interval_days: int = 28,
    grace_days: int = 3,
) -> tuple[str, str]:
    """A consenting patient's repeat in Triage: ``(schedule id, patient id)``."""
    with desk.session() as db:
        patient = PatientFactory.create(db)
        record_consent(
            db,
            patient,
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
        schedule = ChronicSchedule(
            site_id=SITE_A,
            queue_id=desk.triage.id,
            patient_id=patient.id,
            interval_days=interval_days,
            grace_days=grace_days,
            next_due_on=business_date(now_sast()) + timedelta(days=due_in_days),
            join_token=None,
        )
        db.add(schedule)
        db.commit()
        return schedule.id, patient.id


def _schedule_in(desk: SimpleNamespace, queue_id: str) -> tuple[str, str]:
    """A repeat due today in a queue that takes walk-ins only."""
    with desk.session() as db:
        patient = PatientFactory.create(db)
        record_consent(
            db,
            patient,
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
        schedule = ChronicSchedule(
            site_id=SITE_A,
            queue_id=queue_id,
            patient_id=patient.id,
            interval_days=28,
            grace_days=3,
            next_due_on=business_date(now_sast()),
        )
        db.add(schedule)
        db.commit()
        return schedule.id, patient.id


def _sweep(desk: SimpleNamespace, moment: datetime) -> chronic.SweepResult:
    with desk.session() as db:
        done = chronic.run_due(db, moment=moment)
        db.commit()
    return done


def _messages(desk: SimpleNamespace, patient_id: str) -> list[Notification]:
    with desk.session() as db:
        return list(
            db.execute(
                select(Notification)
                .where(Notification.patient_id == patient_id)
                .order_by(Notification.created_at)
            ).scalars()
        )


def _token(desk: SimpleNamespace, schedule_id: str) -> str:
    with desk.session() as db:
        token = db.get_one(ChronicSchedule, schedule_id).join_token
    assert token is not None
    return token


def test_a_repeat_reminds_once_and_rolls_forward_from_the_collection(
    desk: SimpleNamespace, reachable: None
) -> None:
    """How to verify, step 1: one reminder on the due day, then the next due date follows the collection."""
    schedule_id, patient_id = _schedule(desk, due_in_days=0)

    early = _sweep(desk, _at(0, hour=6))
    due = _sweep(desk, _at(0))
    again = _sweep(desk, _at(0, hour=14))

    assert early.reminded == [] and due.reminded == [schedule_id]
    assert again.reminded == []
    sent = _messages(desk, patient_id)
    assert [row.template_key for row in sent] == [
        NotificationTemplate.COLLECTION_DUE.value
    ]

    # The patient comes two days late; the next collection is 28 days from the day they came.
    with desk.session() as db:
        schedule = db.get_one(ChronicSchedule, schedule_id)
        token = schedule.join_token
    assert token is not None
    anyone = TestClient(desk.app)
    joined = anyone.post(f"/api/v1/collections/joins/{token}")
    assert joined.status_code == status.HTTP_200_OK, joined.text
    with desk.session() as db:
        ticket = db.execute(
            select(Ticket).where(Ticket.patient_id == patient_id)
        ).scalar_one()
        for step in (TicketStatus.CALLED, TicketStatus.IN_PROGRESS, TicketStatus.DONE):
            transition_ticket(db, ticket.id, step, actor=_DESK)
        db.commit()
        rolled = db.get_one(ChronicSchedule, schedule_id)
        assert rolled.last_collected_on == business_date(now_sast())
        assert rolled.next_due_on == business_date(now_sast()) + timedelta(days=28)


def test_a_missed_collection_gets_exactly_one_follow_up(
    desk: SimpleNamespace, reachable: None
) -> None:
    """How to verify, step 2: one message after the grace period, never a repeating nag."""
    schedule_id, patient_id = _schedule(desk, due_in_days=0, grace_days=3)
    _sweep(desk, _at(0))

    inside_grace = _sweep(desk, _at(3))
    missed = _sweep(desk, _at(4))
    again = _sweep(desk, _at(5))
    and_again = _sweep(desk, _at(6))

    assert inside_grace.followed_up == []
    assert missed.followed_up == [schedule_id]
    assert again.followed_up == [] and and_again.followed_up == []
    assert [row.template_key for row in _messages(desk, patient_id)] == [
        NotificationTemplate.COLLECTION_DUE.value,
        NotificationTemplate.COLLECTION_MISSED.value,
    ]
    with desk.session() as db:
        # The cycle moved on: the patient is reminded next month, not nagged this one.
        assert db.get_one(ChronicSchedule, schedule_id).next_due_on == business_date(
            now_sast()
        ) + timedelta(days=28)


def test_the_join_action_takes_a_place_in_one_interaction(
    desk: SimpleNamespace, reachable: None
) -> None:
    """How to verify: the reminder's own button, with no sign-in, and a second tap says the same."""
    schedule_id, patient_id = _schedule(desk, due_in_days=0)
    _sweep(desk, _at(0))
    token = _token(desk, schedule_id)
    anyone = TestClient(desk.app)

    first = anyone.post(f"/api/v1/collections/joins/{token}")
    second = anyone.post(f"/api/v1/collections/joins/{token}")
    unknown = anyone.post("/api/v1/collections/joins/" + "z" * 40)

    assert first.status_code == status.HTTP_200_OK, first.text
    assert first.json()["queue_name"] == "Triage"
    assert first.json()["message"].startswith("You have a place in the queue")
    assert second.json()["number"] == first.json()["number"]
    assert second.json()["already"] is True
    assert unknown.status_code == status.HTTP_404_NOT_FOUND
    assert unknown.json()["code"] == "collections.not_found"
    with desk.session() as db:
        tickets = (
            db.execute(select(Ticket).where(Ticket.patient_id == patient_id))
            .scalars()
            .all()
        )
    assert len(tickets) == 1


def test_a_reply_of_collect_takes_the_same_place(
    desk: SimpleNamespace, reachable: None
) -> None:
    """One keypress on a feature phone: the SMS gateway's own path to the same join."""
    _, patient_id = _schedule(desk, due_in_days=0)
    _sweep(desk, _at(0))
    with desk.session() as db:
        phone = db.get_one(Patient, patient_id).phone_e164
    desk.app.dependency_overrides[get_settings] = lambda: queue_settings(
        sms_webhook_token=_TOKEN
    )
    client = TestClient(desk.app)

    answer = client.post(
        f"/api/v1/webhooks/sms/africastalking/{_TOKEN}/inbound",
        content=urlencode({"id": "cc-1", "from": phone, "text": "COLLECT", "to": "1"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert answer.status_code == status.HTTP_200_OK, answer.text
    assert answer.json()["outcome"] == "collecting"
    with desk.session() as db:
        assert (
            db.execute(
                select(Ticket).where(Ticket.patient_id == patient_id)
            ).scalar_one()
            is not None
        )


def test_stopping_sends_nothing_more(desk: SimpleNamespace, reachable: None) -> None:
    """The clinic stops a repeat: the sweep skips it, and its join link no longer does anything."""
    schedule_id, patient_id = _schedule(desk, due_in_days=0)
    _sweep(desk, _at(0))
    token = _token(desk, schedule_id)
    with desk.session() as db:
        chronic.stop(
            db, db.get_one(ChronicSchedule, schedule_id), by="manager.a@clinicq.example"
        )
        db.commit()

    later = _sweep(desk, _at(28))
    refused = TestClient(desk.app).post(f"/api/v1/collections/joins/{token}")

    assert later.reminded == [] and later.followed_up == []
    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["code"] == "collections.stopped"
    assert len(_messages(desk, patient_id)) == 1


def test_a_patients_stop_reply_ends_every_message(
    desk: SimpleNamespace, reachable: None
) -> None:
    """How to verify, step 3: STOP on any channel, and the next reminder is suppressed by the service."""
    _, patient_id = _schedule(desk, due_in_days=0)
    with desk.session() as db:
        phone = db.get_one(Patient, patient_id).phone_e164
    desk.app.dependency_overrides[get_settings] = lambda: queue_settings(
        sms_webhook_token=_TOKEN
    )
    stopped = TestClient(desk.app).post(
        f"/api/v1/webhooks/sms/africastalking/{_TOKEN}/inbound",
        content=urlencode({"id": "cc-2", "from": phone, "text": "STOP", "to": "1"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert stopped.json()["outcome"] == "stopped"

    _sweep(desk, _at(0))

    [message] = _messages(desk, patient_id)
    assert message.status == NotificationStatus.SUPPRESSED.value


def test_nothing_is_sent_for_a_day_the_clinic_is_shut(
    desk: SimpleNamespace, reachable: None
) -> None:
    """A collection is a visit: the reminder waits for a day the doors are open."""
    shut_day = business_date(now_sast()) + timedelta(days=2)
    with desk.session() as db:
        # The fixture opens every weekday; take the due day's weekday out of the week.
        for hours in (
            db.execute(
                select(SiteOpeningHours).where(
                    SiteOpeningHours.site_id == SITE_A,
                    SiteOpeningHours.weekday == shut_day.weekday(),
                )
            )
            .scalars()
            .all()
        ):
            db.delete(hours)
        db.commit()
    schedule_id, patient_id = _schedule(desk, due_in_days=2)

    on_the_day = _sweep(desk, _at(2))
    next_day = _sweep(desk, _at(3))

    assert on_the_day.reminded == []
    assert next_day.reminded == [schedule_id]
    assert len(_messages(desk, patient_id)) == 1


def test_adherence_is_reportable_per_queue(
    desk: SimpleNamespace, reachable: None
) -> None:
    """What Issue 90 reads: schedules, cycles due, collected on time, collected late and missed."""
    on_time_id, on_time_patient = _schedule(desk, due_in_days=0)
    _schedule(desk, due_in_days=0)  # the patient who does not come
    _sweep(desk, _at(0))
    token = _token(desk, on_time_id)
    TestClient(desk.app).post(f"/api/v1/collections/joins/{token}")
    with desk.session() as db:
        ticket = db.execute(
            select(Ticket).where(Ticket.patient_id == on_time_patient)
        ).scalar_one()
        for step in (TicketStatus.CALLED, TicketStatus.IN_PROGRESS, TicketStatus.DONE):
            transition_ticket(db, ticket.id, step, actor=_DESK)
        db.commit()
    _sweep(desk, _at(4))
    today = business_date(now_sast())

    report = desk.staff("manager.a").get(
        f"/api/v1/sites/{SITE_A}/reports/collections",
        params={"start": today.isoformat(), "end": today.isoformat()},
    )
    elsewhere = desk.staff("desk.b").get(f"/api/v1/sites/{SITE_A}/reports/collections")

    assert report.status_code == status.HTTP_200_OK, report.text
    [queue] = [row for row in report.json()["queues"] if row["queue_name"] == "Triage"]
    assert queue["schedules"] == 2
    assert queue["collected_on_time"] == 1
    assert queue["missed"] == 1
    # Another clinic's report is the same "not found" as one that does not exist (Issue 19).
    assert elsewhere.status_code == status.HTTP_404_NOT_FOUND


def test_the_front_desk_sets_a_repeat_up_and_stops_it(
    desk: SimpleNamespace, reachable: None
) -> None:
    """The clinic's own management: by phone number, corrected rather than duplicated, and stoppable."""
    manager = desk.staff("manager.a")
    day = (business_date(now_sast()) + timedelta(days=7)).isoformat()
    body = {
        "phone": "0825550851",
        "queue_id": desk.triage.id,
        "next_due_on": day,
        "interval_days": 28,
        "service": "ARV refill",
    }

    made = manager.post(f"/api/v1/sites/{SITE_A}/collection-schedules", json=body)
    again = manager.post(
        f"/api/v1/sites/{SITE_A}/collection-schedules",
        json={**body, "interval_days": 56},
    )
    listed = desk.staff("desk.a").get(f"/api/v1/sites/{SITE_A}/collection-schedules")
    elsewhere = desk.staff("desk.b").get(f"/api/v1/sites/{SITE_A}/collection-schedules")
    stopped = manager.post(
        f"/api/v1/sites/{SITE_A}/collection-schedules/{made.json()['items'][0]['id']}/stop"
    )

    assert made.status_code == status.HTTP_201_CREATED, made.text
    assert again.json()["total"] == 1, "a second setup corrects the first"
    assert again.json()["items"][0]["interval_days"] == 56
    assert listed.json()["items"][0]["service"] == "ARV refill"
    assert listed.json()["items"][0]["patient_phone"].endswith("0851")
    assert elsewhere.status_code == status.HTTP_404_NOT_FOUND
    assert stopped.json()["total"] == 0
    with desk.session() as db:
        schedule = db.execute(select(ChronicSchedule)).scalar_one()
        assert schedule.stopped_by == "manager.a@clinicq.example"
        assert schedule.join_token is not None


def test_a_walk_in_only_queue_still_takes_the_patient_the_clinic_invited(
    desk: SimpleNamespace, reachable: None
) -> None:
    """The clinic put this patient in the pharmacy's repeat list: its walk-in-only rule is not aimed at them.

    Everything else about the queue still holds — its hours, its daily capacity — and a patient with no
    schedule there is refused as before.
    """
    schedule_id, _ = _schedule_in(desk, desk.pharmacy.id)
    _sweep(desk, _at(0))
    anyone = TestClient(desk.app)

    joined = anyone.post(f"/api/v1/collections/joins/{_token(desk, schedule_id)}")
    stranger, _ = desk.patient()
    refused = stranger.post(desk.join_path(desk.pharmacy), json={})

    assert joined.status_code == status.HTTP_200_OK, joined.text
    assert joined.json()["queue_name"] == "Pharmacy"
    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["code"] == "queue.join.walk_in_only"
