"""A patient's preferences, quiet hours and opt-out, enforced at send time (Issue 67).

Against the queue fixture's two clinics, the real notification service, gate and routes, with every transport
able to reach the patient (web push, WhatsApp and SMS), so a block is the gate's doing and not a missing
address:

* **replying STOP stops the next message on every channel**, including one already waiting for quiet hours
  to end, and START lets messages through again (How to verify, step 1);
* **quiet hours hold a non-urgent message** to the end of the window, while "you are next" goes at once (step 2);
* **the urgent exception is exactly three messages**, each tested, and an opt-out still stops them;
* **preferences are editable without an account**, by the ticket page's link;
* **a muted event is not sent**, while the others are;
* **the opt-out holds at another clinic**, because it belongs to the patient.

"No transport can dispatch without passing the gate" is the source guard in
``tests/unit/security/test_transport_gate.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import time, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette import status

from src.commons.enums import (
    PATIENT_EVENT_TEMPLATE,
    PATIENT_QUIET_HOURS_EXEMPT,
    ConsentPurpose,
    NotificationChannel,
    NotificationStatus,
    PatientChannel,
    PatientEvent,
    TicketSource,
)
from src.commons.time import now_sast, stored_sast
from src.core.config import get_settings
from src.database.models import Notification, Patient, Queue, Ticket
from src.modules.notifications import service
from src.modules.notifications.transports import NoopTransport, use_transports
from src.modules.patients.consent import record_consent
from src.modules.queue.sequence import issue_ticket
from tests.integration.queue.conftest import SITE_A, SITE_B, queue_settings

_TOKEN = "rq" * 22


@pytest.fixture
def everywhere() -> Iterator[dict[NotificationChannel, NoopTransport]]:
    """Web push, WhatsApp and SMS, all reaching every patient, free ones first."""
    transports = {
        NotificationChannel.WEB_PUSH: NoopTransport(
            channel=NotificationChannel.WEB_PUSH, free=True, address="sub"
        ),
        NotificationChannel.WHATSAPP: NoopTransport(
            channel=NotificationChannel.WHATSAPP, free=True, address="wa"
        ),
        NotificationChannel.SMS: NoopTransport(
            channel=NotificationChannel.SMS, address="+27820000001"
        ),
    }
    with use_transports(*transports.values()):
        yield transports


def _sent(transports: dict[NotificationChannel, NoopTransport]) -> int:
    return sum(len(transport.sent) for transport in transports.values())


def _joined(desk: SimpleNamespace, queue: Queue | None = None) -> tuple[str, str, str]:
    """A consenting patient's web ticket: ``(patient id, ticket id, page token)``. They agreed to messages and
    to the post-visit question (Issue 87), so every event is the gate's to decide."""
    with desk.session() as db:
        client, patient_id = desk.patient()
        for purpose in (ConsentPurpose.NOTIFICATIONS, ConsentPurpose.FEEDBACK_SURVEY):
            record_consent(
                db,
                db.get_one(Patient, patient_id),
                purpose,
                granted=True,
                channel=PatientChannel.WEB,
            )
        db.commit()
    answer = client.post(desk.join_path(queue or desk.triage), json={}).json()
    return patient_id, answer["ticket"]["id"], answer["page_url"].removeprefix("/t/")


def _tell(
    db: Session, patient_id: str, ticket_id: str, event: PatientEvent, **kwargs: object
) -> Notification:
    ticket = db.get_one(Ticket, ticket_id)
    row = service.notify(
        db,
        patient_id=patient_id,
        event=event,
        context={
            "number": ticket.number,
            "clinic": "Zola Clinic",
            "queue": "Triage",
            "room": "Room 2",
            "minutes": 5,
            "wait": "~5-10 min",
        },
        site_id=ticket.site_id,
        **kwargs,  # type: ignore[arg-type]
    )
    db.commit()
    assert row is not None
    db.refresh(row)
    return row


def _preferences(desk: SimpleNamespace, token: str) -> str:
    return f"/api/v1/notifications/patient-preferences/{token}"


# --- STOP -----------------------------------------------------------------------------------------------------


@pytest.fixture
def replies(desk: SimpleNamespace) -> SimpleNamespace:
    settings = queue_settings(sms_webhook_token=_TOKEN)
    desk.app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(desk.app)

    def reply(phone: str, text: str, message_id: str) -> dict[str, str]:
        answer = client.post(
            f"/api/v1/webhooks/sms/africastalking/{_TOKEN}/inbound",
            content=urlencode(
                {"id": message_id, "from": phone, "text": text, "to": "12345"}
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert answer.status_code == status.HTTP_200_OK, answer.text
        return answer.json()

    return SimpleNamespace(reply=reply, client=client)


def test_replying_stop_blocks_the_next_message_on_every_channel_at_once(
    desk: SimpleNamespace,
    everywhere: dict[NotificationChannel, NoopTransport],
    replies: SimpleNamespace,
) -> None:
    """How to verify, step 1, including a message already waiting for quiet hours to end."""
    patient_id, ticket_id, token = _joined(desk)
    with desk.session() as db:
        phone = db.get_one(Patient, patient_id).phone_e164
        start = (now_sast() - timedelta(hours=1)).time().replace(microsecond=0)
        end = (now_sast() + timedelta(hours=1)).time().replace(microsecond=0)
    TestClient(desk.app).put(
        _preferences(desk, token),
        json={
            "quiet_hours_start": start.isoformat(),
            "quiet_hours_end": end.isoformat(),
        },
    )
    with desk.session() as db:
        waiting = _tell(
            db, patient_id, ticket_id, PatientEvent.CANCELLED, dedupe_key="waiting"
        )
    assert (
        waiting.status == NotificationStatus.QUEUED.value
        and waiting.next_attempt_at is not None
    )

    assert replies.reply(phone, "stop please", "ATXid_in_1")["outcome"] == "stopped"
    assert replies.reply(phone, "stop please", "ATXid_in_1")["outcome"] == "duplicate"

    blocked = {}
    for preferred in (
        NotificationChannel.WEB_PUSH,
        NotificationChannel.WHATSAPP,
        NotificationChannel.SMS,
    ):
        TestClient(desk.app).put(
            _preferences(desk, token), json={"preferred_channel": preferred.value}
        )
        with desk.session() as db:
            row = _tell(
                db,
                patient_id,
                ticket_id,
                PatientEvent.NEXT,
                dedupe_key=f"after-stop-{preferred.value}",
            )
        blocked[preferred.value] = (row.status, row.last_error)
    with desk.session() as db:
        service.run_retry_sweep(
            db, now=stored_sast(waiting.next_attempt_at) + timedelta(seconds=1)
        )
        db.commit()
        released = db.get_one(Notification, waiting.id)

    assert set(blocked.values()) == {
        (NotificationStatus.SUPPRESSED.value, "suppressed by preference (opted-out)")
    }
    assert released.status == NotificationStatus.SUPPRESSED.value, (
        "a message queued before STOP still went"
    )
    assert _sent(everywhere) == 0

    assert replies.reply(phone, "START", "ATXid_in_2")["outcome"] == "restarted"
    with desk.session() as db:
        again = _tell(
            db, patient_id, ticket_id, PatientEvent.CALLED, dedupe_key="after-start"
        )
    assert again.status == NotificationStatus.SENT.value and _sent(everywhere) == 1


def test_a_reply_without_a_keyword_or_from_an_unknown_number_changes_nothing(
    desk: SimpleNamespace, replies: SimpleNamespace
) -> None:
    patient_id, _, token = _joined(desk)
    with desk.session() as db:
        phone = db.get_one(Patient, patient_id).phone_e164
    assert (
        replies.reply(phone, "What time do you close?", "ATXid_q")["outcome"]
        == "ignored"
    )
    assert (
        replies.reply("+27829999999", "STOP", "ATXid_stranger")["outcome"] == "ignored"
    )
    assert (
        TestClient(desk.app).get(_preferences(desk, token)).json()["opted_out"] is False
    )
    wrong = replies.client.post(
        "/api/v1/webhooks/sms/africastalking/" + "x" * 44 + "/inbound",
        content=urlencode({"id": "ATXid_forged", "from": phone, "text": "STOP"}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert wrong.status_code == status.HTTP_404_NOT_FOUND


# --- quiet hours --------------------------------------------------------------------------------------------------


def _quiet_now(desk: SimpleNamespace, token: str) -> None:
    """Quiet hours around the present moment."""
    start = (now_sast() - timedelta(hours=2)).time().replace(second=0, microsecond=0)
    end = (now_sast() + timedelta(hours=2)).time().replace(second=0, microsecond=0)
    answer = TestClient(desk.app).put(
        _preferences(desk, token),
        json={
            "quiet_hours_start": start.isoformat(),
            "quiet_hours_end": end.isoformat(),
        },
    )
    assert answer.status_code == status.HTTP_200_OK, answer.text


def test_quiet_hours_hold_a_non_urgent_message_and_let_you_are_next_through(
    desk: SimpleNamespace, everywhere: dict[NotificationChannel, NoopTransport]
) -> None:
    """How to verify, step 2."""
    patient_id, ticket_id, token = _joined(desk)
    _quiet_now(desk, token)
    with desk.session() as db:
        held = _tell(db, patient_id, ticket_id, PatientEvent.CANCELLED)
        urgent = _tell(db, patient_id, ticket_id, PatientEvent.NEXT)
    assert (held.status, urgent.status) == (
        NotificationStatus.QUEUED.value,
        NotificationStatus.SENT.value,
    )
    assert _sent(everywhere) == 1

    assert held.next_attempt_at is not None
    with desk.session() as db:
        service.run_retry_sweep(
            db, now=stored_sast(held.next_attempt_at) + timedelta(seconds=1)
        )
        db.commit()
        assert db.get_one(Notification, held.id).status == NotificationStatus.SENT.value
    assert _sent(everywhere) == 2


@pytest.mark.parametrize("event", list(PatientEvent))
def test_the_urgent_exception_is_exactly_the_time_critical_messages(
    desk: SimpleNamespace,
    everywhere: dict[NotificationChannel, NoopTransport],
    event: PatientEvent,
) -> None:
    """Narrowly defined: next, called, recalled and time to leave (Issue 86) cross quiet hours; the rest wait."""
    assert {template.value for template in PATIENT_QUIET_HOURS_EXEMPT} == {
        "ticket_next",
        "ticket_called",
        "ticket_recalled",
        "ticket_leave_now",
    }
    patient_id, ticket_id, token = _joined(desk)
    _quiet_now(desk, token)
    with desk.session() as db:
        row = _tell(db, patient_id, ticket_id, event)
    urgent = PATIENT_EVENT_TEMPLATE[event] in PATIENT_QUIET_HOURS_EXEMPT
    assert row.status == (
        NotificationStatus.SENT.value if urgent else NotificationStatus.QUEUED.value
    )


def test_an_opt_out_stops_even_the_urgent_messages(
    desk: SimpleNamespace, everywhere: dict[NotificationChannel, NoopTransport]
) -> None:
    patient_id, ticket_id, token = _joined(desk)
    TestClient(desk.app).put(_preferences(desk, token), json={"opted_out": True})
    with desk.session() as db:
        rows = [
            _tell(db, patient_id, ticket_id, event)
            for event in (PatientEvent.NEXT, PatientEvent.CALLED)
        ]
    assert {row.status for row in rows} == {NotificationStatus.SUPPRESSED.value}
    assert _sent(everywhere) == 0


# --- preferences without an account ----------------------------------------------------------------------------


def test_preferences_are_editable_without_an_account_by_the_ticket_link(
    desk: SimpleNamespace, everywhere: dict[NotificationChannel, NoopTransport]
) -> None:
    patient_id, ticket_id, token = _joined(desk)
    nobody = TestClient(desk.app)  # no session, no account: the link only

    before = nobody.get(_preferences(desk, token)).json()
    assert before["opted_out"] is False and before["muted_events"] == []
    assert before["quiet_hours_exempt"] == ["called", "leave_now", "next", "recalled"]
    changed = nobody.put(
        _preferences(desk, token),
        json={
            "preferred_channel": "sms",
            "language": "zu",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "06:00",
            "muted_events": ["next"],
        },
    ).json()
    assert (
        changed["preferred_channel"],
        changed["language"],
        changed["muted_events"],
    ) == ("sms", "zu", ["next"])
    assert (changed["quiet_hours_start"], changed["quiet_hours_end"]) == (
        "22:00:00",
        "06:00:00",
    )

    for bad in (
        # ``email`` used to be here: a patient had no address, so preferring it was nonsense.
        # Issue 219 gave them one and Issue 220 made it a transport, so it is a real choice now —
        # ``letter`` stands in as the channel that still is not one.
        {"preferred_channel": "letter"},
        {"language": "tsn"},
        {"quiet_hours_start": "22:00"},
        {"patient_id": "someone-else"},
    ):
        assert nobody.put(_preferences(desk, token), json=bad).status_code == 422, bad

    # And the one that changed: a patient may now ask to be reached by email first (Issue 220).
    by_email = nobody.put(
        _preferences(desk, token), json={"preferred_channel": "email"}
    )
    assert by_email.status_code == status.HTTP_200_OK, by_email.text
    assert by_email.json()["preferred_channel"] == "email"
    # Put it back: the rest of this test is about the SMS preference being honoured.
    nobody.put(_preferences(desk, token), json={"preferred_channel": "sms"})
    assert (
        nobody.get(_preferences(desk, "x" * 43)).status_code
        == status.HTTP_404_NOT_FOUND
    )

    with desk.session() as db:
        muted = _tell(
            db, patient_id, ticket_id, PatientEvent.NEXT, dedupe_key="muted-next"
        )
        called = _tell(
            db, patient_id, ticket_id, PatientEvent.CALLED, dedupe_key="called"
        )
    assert muted.last_error == "suppressed by preference (muted-next)"
    assert called.status == NotificationStatus.SENT.value
    assert called.channel == NotificationChannel.SMS.value, (
        "the preferred channel came first"
    )


def test_a_walk_in_with_no_patient_has_no_preferences(desk: SimpleNamespace) -> None:
    with desk.session() as db:
        walk_in = issue_ticket(
            db, queue=db.get_one(Queue, desk.triage.id), source=TicketSource.WALK_IN
        )
        db.commit()
        token = walk_in.page_token
    assert token is not None
    assert (
        TestClient(desk.app).get(_preferences(desk, token)).status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_the_opt_out_holds_when_the_patient_joins_at_another_clinic(
    desk: SimpleNamespace, everywhere: dict[NotificationChannel, NoopTransport]
) -> None:
    patient_id, _, token = _joined(desk)
    TestClient(desk.app).put(_preferences(desk, token), json={"opted_out": True})

    with desk.session() as db:
        other = issue_ticket(
            db,
            queue=db.get_one(Queue, desk.other_triage.id),
            source=TicketSource.WEB,
            patient_id=patient_id,
        )
        db.commit()
        assert other.site_id == SITE_B != SITE_A
        row = _tell(db, patient_id, other.id, PatientEvent.CALLED)
    assert row.status == NotificationStatus.SUPPRESSED.value and _sent(everywhere) == 0


def test_quiet_hours_may_wrap_past_midnight() -> None:
    from src.core.s3_logging import APP_TIMEZONE
    from src.modules.notifications.preferences import quiet_until

    late = now_sast().replace(hour=23, minute=30, second=0, microsecond=0)
    early = late + timedelta(hours=2)  # 01:30 the next day
    midday = late.replace(hour=12)
    tonight = quiet_until(time(22), time(6), APP_TIMEZONE, late)
    after_midnight = quiet_until(time(22), time(6), APP_TIMEZONE, early)
    assert tonight is not None and stored_sast(tonight).hour == 6
    assert after_midnight is not None and stored_sast(after_midnight).hour == 6
    assert quiet_until(time(22), time(6), APP_TIMEZONE, midday) is None
