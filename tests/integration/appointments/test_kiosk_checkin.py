"""The check-in tablet at the clinic's door, over HTTP, on a paired device (Issue 83).

Against the queue fixture's clinic A, at fixed times:

* **a booked patient scans their QR** and is checked in: the booking becomes a ticket in the same
  sequence as everyone else, the arrival is stamped, and the board's queue is told;
* **a patient who already holds a ticket** scans it and is stamped arrived, changing no status;
* **a phone number on the keypad** finds the same place, and a second scan says the same thing back;
* **the tablet sees only its own clinic**: another clinic's code, an unpaired device and a board device
  are all refused, and no answer carries a patient's name;
* **a booking more than an hour away** is told when to come back, not given a place;
* **walk-ins at the door** work only where the clinic has switched them on.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette import status

from src.commons.enums import (
    ConsentPurpose,
    DisplayDeviceKind,
    PatientChannel,
    TicketSource,
    TicketStatus,
)
from src.commons.time import APP_TIMEZONE, business_date, now_sast
from src.core import domain_events
from src.database.models import (
    Appointment,
    AppointmentSlot,
    Patient,
    PatientConsent,
    Queue,
    Site,
    Ticket,
)
from src.modules.appointments import capacity
from src.modules.display import devices
from src.modules.patients.consent import record_consent
from src.modules.queue.sequence import format_reference_code
from src.modules.queue.service import join_queue
from src.modules.queue.ticket_codes import QR_PREFIX
from src.modules.sites.hours import published_schedules
from tests.factories import PatientFactory
from tests.integration.queue.conftest import SITE_A, SITE_B

ARRIVALS = "/display/check-in/arrivals"
WALK_INS = "/display/check-in/walk-ins"


@pytest.fixture(autouse=True)
def door_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tablet's own clock, fixed at 09:00 today, so every booking below is a fixed distance away."""
    monkeypatch.setattr("src.web.kiosk.now_sast", lambda: _at(9))


@pytest.fixture
def heard() -> Iterator[list[domain_events.QueueChanged]]:
    """Every ``QueueChanged`` the application published while the test ran."""
    events: list[domain_events.QueueChanged] = []
    domain_events.subscribe(domain_events.QueueChanged)(events.append)
    yield events
    domain_events._SUBSCRIBERS[domain_events.QueueChanged].remove(events.append)


def _tablet(
    desk: SimpleNamespace,
    *,
    site_id: str = SITE_A,
    kind: DisplayDeviceKind = DisplayDeviceKind.CHECK_IN,
    paired: bool = True,
) -> TestClient:
    """A client that is a tablet by the door, as a manager's pairing leaves it."""
    with desk.session() as db:
        started = devices.start_device(db, user_agent="test tablet")
        if paired:
            started.device.site_id = site_id
            started.device.paired_at = now_sast()
            started.device.last_seen_at = now_sast()
            started.device.pairing_code_hash = None
        started.device.kind = kind.value
        db.commit()
    client = TestClient(desk.app, follow_redirects=False)
    client.cookies.set(desk.settings.display_device_cookie_name, started.secret)
    return client


def _at(hour: int, minute: int = 0) -> datetime:
    """A fixed time today, so a run near midnight reads the same day throughout."""
    return datetime.combine(
        business_date(now_sast()), time(hour, minute), tzinfo=APP_TIMEZONE
    )


def _booking(
    desk: SimpleNamespace, starts: datetime, *, site_id: str = SITE_A
) -> tuple[str, str, str]:
    """A booking at ``starts``: ``(appointment id, patient id, reference as it is printed)``."""
    with desk.session() as db:
        patient = PatientFactory.create(db)
        queue_id = desk.triage.id if site_id == SITE_A else desk.other_triage.id
        slot = AppointmentSlot(
            site_id=site_id,
            queue_id=queue_id,
            starts_at=starts,
            ends_at=starts + timedelta(minutes=15),
            service_day=business_date(starts),
            capacity=2,
        )
        db.add(slot)
        db.flush()
        appointment = capacity.claim_place(
            db,
            slot=slot,
            queue=db.get_one(Queue, queue_id),
            patient_id=patient.id,
            moment=starts - timedelta(days=1),
        )
        db.commit()
        return (
            appointment.id,
            patient.id,
            format_reference_code(appointment.reference),
        )


def _ticket(desk: SimpleNamespace, *, patient_id: str | None = None) -> tuple[str, str]:
    """A ticket held in Triage today: ``(ticket id, its reference code)``."""
    with desk.session() as db:
        patient = (
            db.get_one(Patient, patient_id) if patient_id else PatientFactory.create(db)
        )
        result = join_queue(
            db,
            site=db.get_one(Site, SITE_A),
            queue=db.get_one(Queue, desk.triage.id),
            schedule=published_schedules(
                db, [SITE_A], from_day=business_date(now_sast())
            )[SITE_A],
            source=TicketSource.WEB,
            patient=patient,
            actor="patient",
        )
        db.commit()
        return result.ticket.id, result.ticket.reference_code


def _allow_walk_ins(desk: SimpleNamespace, allowed: bool = True) -> None:
    with desk.session() as db:
        db.get_one(Site, SITE_A).kiosk_walk_ins_enabled = allowed
        db.commit()


def test_a_booked_patient_scans_their_qr_and_is_checked_in(
    desk: SimpleNamespace, heard: list[domain_events.QueueChanged]
) -> None:
    """How to verify, step 1: one scan, a ticket in the same sequence, and the board is told."""
    appointment_id, patient_id, reference = _booking(desk, _at(9, 30))
    tablet = _tablet(desk)

    answer = tablet.post(ARRIVALS, json={"code": f"{QR_PREFIX}{reference}"})

    assert answer.status_code == status.HTTP_200_OK, answer.text
    body = answer.json()
    assert body["queue_name"] == "Triage" and body["from_booking"] is True
    assert body["message"].startswith(f"Checked in. Your number is {body['number']}")
    with desk.session() as db:
        ticket = db.execute(
            select(Ticket).where(Ticket.appointment_id == appointment_id)
        ).scalar_one()
        assert ticket.number == body["number"]
        assert ticket.patient_id == patient_id
        assert ticket.status_enum is TicketStatus.WAITING
        assert ticket.arrived_at is not None
    assert [event.queue_id for event in heard] == [desk.triage.id]


def test_a_second_scan_says_the_same_thing_and_makes_no_second_ticket(
    desk: SimpleNamespace,
) -> None:
    """A patient who scans twice, or whose companion scans again, is told they are already checked in."""
    appointment_id, _, reference = _booking(desk, _at(9, 45))
    tablet = _tablet(desk)

    first = tablet.post(ARRIVALS, json={"code": reference})
    second = tablet.post(ARRIVALS, json={"code": reference})

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["number"] == second.json()["number"]
    assert second.json()["already"] is True
    assert second.json()["message"].startswith("You are already checked in.")
    with desk.session() as db:
        tickets = (
            db.execute(select(Ticket).where(Ticket.appointment_id == appointment_id))
            .scalars()
            .all()
        )
    assert len(tickets) == 1


def test_a_ticket_already_held_is_stamped_arrived_and_keeps_its_place(
    desk: SimpleNamespace, heard: list[domain_events.QueueChanged]
) -> None:
    """A patient who joined from home scans at the door: nothing about their place changes."""
    ticket_id, code = _ticket(desk)
    heard.clear()
    tablet = _tablet(desk)

    answer = tablet.post(
        ARRIVALS, json={"code": f"{QR_PREFIX}{format_reference_code(code)}"}
    )

    assert answer.status_code == status.HTTP_200_OK, answer.text
    assert answer.json()["from_booking"] is False
    with desk.session() as db:
        ticket = db.get_one(Ticket, ticket_id)
        assert ticket.status_enum is TicketStatus.WAITING
        assert ticket.arrived_at is not None
    assert [event.queue_id for event in heard] == [desk.triage.id]


def test_a_phone_number_on_the_keypad_finds_the_same_place(
    desk: SimpleNamespace,
) -> None:
    """Digits only, no letters: the number the patient joined with finds their ticket."""
    ticket_id, _ = _ticket(desk)
    with desk.session() as db:
        phone = db.get_one(Ticket, ticket_id).patient_id
        phone = db.get_one(Patient, phone).phone_e164
    tablet = _tablet(desk)

    answer = tablet.post(ARRIVALS, json={"code": phone.replace("+27", "0")})

    assert answer.status_code == status.HTTP_200_OK, answer.text
    with desk.session() as db:
        assert db.get_one(Ticket, ticket_id).arrived_at is not None


def test_the_tablet_answers_with_no_name_and_no_other_patient(
    desk: SimpleNamespace,
) -> None:
    """Everything the screen is given: a number, a queue, a room, and how many are ahead."""
    _ticket(desk)  # somebody else already waiting
    _, patient_id, reference = _booking(desk, _at(10))
    with desk.session() as db:
        patient = db.get_one(Patient, patient_id)
        patient.display_name = "Nomvula Dlamini"
        db.commit()
    tablet = _tablet(desk)

    answer = tablet.post(ARRIVALS, json={"code": reference})

    assert answer.status_code == status.HTTP_200_OK, answer.text
    assert "Nomvula" not in answer.text and "Dlamini" not in answer.text
    assert set(answer.json()) == {
        "number",
        "queue_name",
        "room_label",
        "waiting_ahead",
        "wait_label",
        "already",
        "from_booking",
        "message",
        "clear_seconds",
    }
    assert answer.json()["waiting_ahead"] == 1
    assert answer.headers["cache-control"] == "no-store"


def test_another_clinics_code_an_unpaired_device_and_a_board_are_all_refused(
    desk: SimpleNamespace,
) -> None:
    """The tablet is paired to one clinic (Issue 61), and a board is not a check-in tablet."""
    _, _, elsewhere = _booking(desk, _at(9, 30), site_id=SITE_B)
    _, _, here = _booking(desk, _at(9, 30))

    other_clinic = _tablet(desk).post(ARRIVALS, json={"code": elsewhere})
    unknown = _tablet(desk).post(ARRIVALS, json={"code": "K7M-4QP"})
    unpaired = _tablet(desk, paired=False).post(ARRIVALS, json={"code": here})
    board = _tablet(desk, kind=DisplayDeviceKind.BOARD).post(
        ARRIVALS, json={"code": here}
    )
    nonsense = _tablet(desk).post(ARRIVALS, json={"code": "hello"})

    assert other_clinic.status_code == status.HTTP_404_NOT_FOUND
    assert other_clinic.json() == unknown.json()  # the same "not found", to the letter
    assert unknown.json()["code"] == "appointments.checkin.not_found"
    assert unpaired.status_code == board.status_code == status.HTTP_401_UNAUTHORIZED
    assert nonsense.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert nonsense.json()["code"] == "appointments.checkin.not_a_code"


def test_a_booking_more_than_an_hour_away_is_told_when_to_come_back(
    desk: SimpleNamespace,
) -> None:
    """A patient at the door at 08:00 for 14:00 keeps their booking, and takes no place yet."""
    appointment_id, _, reference = _booking(desk, _at(14))
    tablet = _tablet(desk)

    answer = tablet.post(ARRIVALS, json={"code": reference})

    assert answer.status_code == status.HTTP_409_CONFLICT
    assert answer.json()["code"] == "appointments.checkin.too_early"
    assert "Please check in when it is closer." in answer.json()["detail"]
    with desk.session() as db:
        assert (
            db.execute(
                select(Ticket).where(Ticket.appointment_id == appointment_id)
            ).scalar_one_or_none()
            is None
        )


def test_a_cancelled_booking_sends_the_patient_to_reception(
    desk: SimpleNamespace,
) -> None:
    """Nothing is issued off a booking that is no longer held."""
    appointment_id, _, reference = _booking(desk, _at(9, 15))
    with desk.session() as db:
        capacity.release_place(db, db.get_one(Appointment, appointment_id))
        db.commit()
    tablet = _tablet(desk)

    answer = tablet.post(ARRIVALS, json={"code": reference})

    assert answer.status_code == status.HTTP_409_CONFLICT
    assert answer.json()["code"] == "appointments.checkin.ended"
    assert "Please see reception." in answer.json()["detail"]


def test_walk_ins_at_the_door_only_where_the_clinic_switched_them_on(
    desk: SimpleNamespace,
) -> None:
    """Off by default; on, the tablet issues a walk-in in the same sequence, with the phone optional."""
    tablet = _tablet(desk)

    refused = tablet.post(WALK_INS, json={"queue_id": desk.triage.id})
    _allow_walk_ins(desk)
    issued = tablet.post(
        WALK_INS,
        json={
            "queue_id": desk.triage.id,
            "phone": "0825550831",
            "notifications_consent": True,
        },
    )

    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["code"] == "appointments.checkin.not_offered"
    assert issued.status_code == status.HTTP_200_OK, issued.text
    with desk.session() as db:
        ticket = db.execute(
            select(Ticket).where(Ticket.number == issued.json()["number"])
        ).scalar_one()
        assert ticket.source == TicketSource.WALK_IN.value
        assert ticket.arrived_at is not None
        patient = db.get_one(Patient, ticket.patient_id)
        assert patient.phone_e164 == "+27825550831"
        assert patient.last_channel == PatientChannel.WALK_IN.value
        granted = (
            db.execute(
                select(PatientConsent).where(
                    PatientConsent.patient_id == patient.id,
                    PatientConsent.purpose == ConsentPurpose.NOTIFICATIONS.value,
                )
            )
            .scalars()
            .all()
        )
    assert [row.granted for row in granted] == [True]


def test_the_page_and_the_start_page_send_each_device_to_its_own_screen(
    desk: SimpleNamespace,
) -> None:
    """A check-in tablet opening /display lands on the check-in page; a board still lands on its board."""
    tablet = _tablet(desk)
    board = _tablet(desk, kind=DisplayDeviceKind.BOARD)
    stranger = TestClient(desk.app, follow_redirects=False)

    page = tablet.get("/display/check-in")
    sent = tablet.get("/display")
    board_sent = board.get("/display")
    board_page = board.get("/display/check-in")
    turned_away = stranger.get("/display/check-in")

    assert page.status_code == status.HTTP_200_OK
    assert "Check in at" in page.text and page.headers["cache-control"] == "no-store"
    assert sent.headers["location"] == "/display/check-in"
    assert board_sent.headers["location"] == f"/display/{SITE_A}"
    assert board_page.headers["location"] == "/display"
    assert turned_away.headers["location"] == "/display"


def test_the_idle_screen_offers_the_open_queues_only_when_walk_ins_are_on(
    desk: SimpleNamespace,
) -> None:
    """The queue buttons are the clinic's own switch, not the tablet's."""
    tablet = _tablet(desk)

    without = tablet.get("/display/check-in")
    _allow_walk_ins(desk)
    with_them = tablet.get("/display/check-in")

    assert "Join a queue" not in without.text
    assert "Join a queue" in with_them.text and "Triage" in with_them.text


def test_the_state_route_is_the_tablets_offline_check(desk: SimpleNamespace) -> None:
    """It answers only a paired tablet, and a heartbeat keeps the device from being reported silent."""
    tablet = _tablet(desk)

    ready = tablet.get("/display/check-in/state")
    stranger = TestClient(desk.app).get("/display/check-in/state")

    assert ready.status_code == status.HTTP_200_OK
    assert ready.json() == {"state": "paired"}
    assert stranger.status_code == status.HTTP_401_UNAUTHORIZED


def test_a_patient_with_nothing_here_today_is_sent_to_reception(
    desk: SimpleNamespace,
) -> None:
    """A patient the clinic knows, with no ticket and no booking today, is not left guessing."""
    with desk.session() as db:
        patient = PatientFactory.create(db)
        record_consent(
            db,
            patient,
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
        db.commit()
        phone = patient.phone_e164
    tablet = _tablet(desk)

    answer = tablet.post(ARRIVALS, json={"code": phone})

    assert answer.status_code == status.HTTP_404_NOT_FOUND
    assert "Please see reception." in answer.json()["detail"]


def test_the_screen_has_nothing_to_browse_away_to(desk: SimpleNamespace) -> None:
    """The tablet's own half of "it cannot browse away": no link, no form, nothing but its own buttons.

    The other half is the box itself, locked into one address by the kiosk-mode browser the setup
    guide installs (``docs/OPS/KIOSK_SETUP.md``); this test holds the page to its side of that bargain.
    """
    _allow_walk_ins(desk)
    tablet = _tablet(desk)

    page = tablet.get("/display/check-in").text

    assert "<a " not in page and "<form" not in page
    assert "/static/js/checkin.js" in page
    assert "target=" not in page
