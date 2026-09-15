"""Post-visit feedback end to end: the done transition, the answer, the reply, the report, retention (Issue 87).

Against the queue fixture's clinics, the real lifecycle, notification service, SMS inbound webhook and report
route, with every transport able to reach the patient:

* **one request per completed visit, never repeated**: done sends one; a second attempt, and a transfer's
  earlier leg, send nothing more;
* **one tap on the web, one keypress by SMS**: a score posted to the link, or a reply "4 …" to the gateway;
* **free text is screened** before it is stored, and emptied after the clinic's retention, the score kept;
* **consent and opt-outs hold**: no survey consent, or an SMS STOP, suppresses the request, which is then
  neither answerable nor counted;
* **scores and the response rate roll up** per clinic, queue, staff member and channel.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette import status

from src.commons.enums import (
    ActorKind,
    AuditEntityType,
    ConsentPurpose,
    FeedbackRequestStatus,
    NotificationChannel,
    PatientChannel,
    PatientEvent,
    TicketSource,
    TicketStatus,
    TransferReason,
)
from src.commons.time import now_sast, stored_sast
from src.core.config import get_settings
from src.database.models import (
    AuditEvent,
    Notification,
    Patient,
    Queue,
    Ticket,
    User,
    VisitFeedback,
)
from src.modules.appointments import feedback
from src.modules.notifications import patient_preferences
from src.modules.notifications.transports import NoopTransport, use_transports
from src.modules.patients.consent import record_consent
from src.modules.queue.lifecycle import Actor, transition_ticket
from src.modules.queue.transfer import transfer_ticket
from tests.integration.queue.conftest import SITE_A, queue_settings

_TOKEN = "fb" * 22
_REPORT = f"/api/v1/sites/{SITE_A}/reports/feedback"


@pytest.fixture
def everywhere() -> Iterator[None]:
    """Web push, WhatsApp and SMS all reach every patient."""
    with use_transports(
        NoopTransport(channel=NotificationChannel.WEB_PUSH, free=True, address="sub"),
        NoopTransport(channel=NotificationChannel.WHATSAPP, free=True, address="wa"),
        NoopTransport(channel=NotificationChannel.SMS, address="+27820000001"),
    ):
        yield


def _nurse(desk: SimpleNamespace) -> Actor:
    with desk.session() as db:
        user = db.execute(
            select(User).where(User.email == "manager.a@clinicq.example")
        ).scalar_one()
    return Actor(kind=ActorKind.STAFF, label=user.email, user_id=str(user.id))


def _patient(desk: SimpleNamespace, *, survey: bool = True) -> tuple[TestClient, str]:
    client, patient_id = desk.patient()
    with desk.session() as db:
        patient = db.get_one(Patient, patient_id)
        record_consent(
            db,
            patient,
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
        record_consent(
            db,
            patient,
            ConsentPurpose.FEEDBACK_SURVEY,
            granted=survey,
            channel=PatientChannel.WEB,
        )
        db.commit()
    return client, patient_id


def _visit(
    desk: SimpleNamespace, client: TestClient, queue: Queue | None = None
) -> dict:
    joined = client.post(desk.join_path(queue or desk.triage), json={})
    assert joined.status_code == status.HTTP_201_CREATED, joined.text
    return joined.json()


def _finish(desk: SimpleNamespace, ticket_id: str, actor: Actor) -> None:
    with desk.session() as db:
        for step in (TicketStatus.CALLED, TicketStatus.IN_PROGRESS, TicketStatus.DONE):
            transition_ticket(db, ticket_id, step, actor=actor)
        db.commit()


def _requests(desk: SimpleNamespace) -> list[VisitFeedback]:
    with desk.session() as db:
        return list(db.execute(select(VisitFeedback)).scalars())


def _feedback_messages(desk: SimpleNamespace) -> int:
    with desk.session() as db:
        return db.execute(
            select(func.count(Notification.id)).where(
                Notification.event == PatientEvent.FEEDBACK.value
            )
        ).scalar_one()


def test_a_completed_visit_is_asked_once_and_never_again(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """How to verify, step 1: done sends exactly one request; asking again, or a second done, adds nothing."""
    client, patient_id = _patient(desk)
    joined = _visit(desk, client)
    ticket_id = joined["ticket"]["id"]
    actor = _nurse(desk)

    _finish(desk, ticket_id, actor)
    with desk.session() as db:
        again = feedback.request_after_visit(
            db, db.get_one(Ticket, ticket_id), served_by=None, moment=now_sast()
        )
        db.commit()

    [row] = _requests(desk)
    assert again is None
    assert (row.request_status, row.patient_id, row.served_by) == (
        FeedbackRequestStatus.SENT.value,
        patient_id,
        actor.user_id,
    )
    assert _feedback_messages(desk) == 1
    with desk.session() as db:
        sent = (
            db.execute(
                select(Notification).where(
                    Notification.event == PatientEvent.FEEDBACK.value
                )
            )
            .scalars()
            .all()
        )
    assert {n.dedupe_key for n in sent} == {f"{row.visit_id}:feedback"}
    assert all(n.payload["page_url"] == f"/f/{row.token}" for n in sent)


def test_a_transferred_visit_is_asked_once_at_its_end(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """Triage then the pharmacy: the transfer asks nothing; the pharmacy's done asks once, about the visit."""
    client, _ = _patient(desk)
    joined = _visit(desk, client)
    actor = _nurse(desk)
    with desk.session() as db:
        for step in (TicketStatus.CALLED, TicketStatus.IN_PROGRESS):
            transition_ticket(db, joined["ticket"]["id"], step, actor=actor)
        moved = transfer_ticket(
            db,
            joined["ticket"]["id"],
            db.get_one(Queue, desk.pharmacy.id),
            actor=actor,
            reason=TransferReason.NEXT_STEP,
        )
        db.commit()
        second_leg = moved.ticket.id
    assert _requests(desk) == []
    _finish(desk, second_leg, actor)
    [row] = _requests(desk)
    assert row.ticket_id == second_leg and row.queue_id == desk.pharmacy.id
    assert _feedback_messages(desk) == 1


def test_one_tap_on_the_web_answers_once_and_a_comment_is_screened_before_it_is_stored(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """How to verify, steps 2 and 3 (web): the score and a phone number in the comment, removed before storage."""
    client, _ = _patient(desk)
    joined = _visit(desk, client)
    _finish(desk, joined["ticket"]["id"], _nurse(desk))
    [row] = _requests(desk)
    anyone = TestClient(desk.app)

    opened = anyone.get(f"/api/v1/feedback/{row.token}")
    page = client.get(
        f"/api/v1/tickets/{joined['page_url'].removeprefix('/t/')}"
    ).json()
    shared = anyone.get(
        f"/api/v1/tickets/{joined['page_url'].removeprefix('/t/')}"
    ).json()
    answered = anyone.post(
        opened.json()["answer_url"],
        json={
            "score": 4,
            "comment": "Kind nurse. Call me on 082 123 4567 or thandi@example.com",
        },
    )
    again = anyone.post(f"/api/v1/feedback/{row.token}", json={"score": 1})

    assert opened.status_code == status.HTTP_200_OK
    assert opened.json()["question"] == "How was your visit today?"
    assert [c["score"] for c in opened.json()["choices"]] == [1, 2, 3, 4, 5]
    assert page["feedback"]["answer_url"] == opened.json()["answer_url"]
    assert (
        shared["feedback"] is None
    )  # a shared ticket link never answers for the patient
    assert answered.status_code == status.HTTP_200_OK, answered.text
    assert (answered.json()["answered"], answered.json()["score"]) == (True, 4)
    assert again.status_code == status.HTTP_409_CONFLICT
    assert again.json()["code"] == feedback.ANSWERED_CODE
    [stored] = _requests(desk)
    assert stored.score == 4 and stored.answered_via == "web"
    assert (
        stored.comment == "Kind nurse. Call me on [REDACTED:phone] or [REDACTED:email]"
    )
    assert stored.comment_redactions == 2
    assert "082 123 4567" not in str(stored.comment)
    with desk.session() as db:
        audit = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.VISIT_FEEDBACK.value
            )
        ).scalar_one()
    assert "082" not in (audit.context or "") and "nurse" not in (audit.context or "")


def test_an_sms_reply_with_one_digit_answers_and_stop_still_wins(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """How to verify, step 2 (SMS): "5 thank you" answers with one keypress; a later "3" changes nothing."""
    settings = queue_settings(sms_webhook_token=_TOKEN)
    desk.app.dependency_overrides[get_settings] = lambda: settings
    gateway = TestClient(desk.app)
    client, patient_id = _patient(desk)
    joined = _visit(desk, client)
    _finish(desk, joined["ticket"]["id"], _nurse(desk))
    with desk.session() as db:
        phone = db.get_one(Patient, patient_id).phone_e164

    def reply(text: str, message_id: str) -> dict:
        answer = gateway.post(
            f"/api/v1/webhooks/sms/africastalking/{_TOKEN}/inbound",
            content=urlencode(
                {"id": message_id, "from": phone, "text": text, "to": "12345"}
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert answer.status_code == status.HTTP_200_OK, answer.text
        return answer.json()

    first = reply("5 thank you, my ID is 8001015009087", "at-1")
    later = reply("3", "at-2")
    [row] = _requests(desk)

    assert first["outcome"] == "rated" and later["outcome"] == "ignored"
    assert (row.score, row.answered_via) == (5, "sms")
    assert row.comment == "thank you, my ID is [REDACTED:id]"


def test_no_survey_consent_or_an_opt_out_suppresses_the_request_which_is_not_answerable_or_counted(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """Patients who opted out receive no request; the suppressed rows sit outside the response rate."""
    declined, _ = _patient(desk, survey=False)
    stopped, stopped_id = _patient(desk)
    with desk.session() as db:
        patient_preferences.apply_reply(
            db, db.get_one(Patient, stopped_id).phone_e164, "STOP", now=now_sast()
        )
        db.commit()
    actor = _nurse(desk)
    for client in (declined, stopped):
        _finish(desk, _visit(desk, client)["ticket"]["id"], actor)

    rows = _requests(desk)
    assert [row.request_status for row in rows] == [
        FeedbackRequestStatus.SUPPRESSED.value
    ] * 2
    with desk.session() as db:
        sent = (
            db.execute(
                select(Notification.status).where(
                    Notification.event == PatientEvent.FEEDBACK.value
                )
            )
            .scalars()
            .all()
        )
    assert set(sent) == {"suppressed"}
    for row in rows:
        assert (
            TestClient(desk.app).get(f"/api/v1/feedback/{row.token}").status_code == 404
        )
    report = desk.staff("manager.a").get(_REPORT).json()
    assert report["overall"]["sent"] == 0 and report["suppressed"] == 2
    assert report["overall"]["response_rate"] is None


def test_scores_and_the_response_rate_roll_up_per_clinic_queue_staff_and_channel(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """Three sent, two answered: 67% response, average 4.5; the desk may not read it; another clinic is a 404."""
    actor = _nurse(desk)
    scores = [5, 4, None]
    for score in scores:
        client, _ = _patient(desk)
        _finish(desk, _visit(desk, client)["ticket"]["id"], actor)
        if score is not None:
            row = _requests(desk)[-1]
            TestClient(desk.app).post(
                f"/api/v1/feedback/{row.token}", json={"score": score}
            )

    report = desk.staff("manager.a").get(_REPORT)
    body = report.json()

    assert report.status_code == status.HTTP_200_OK, report.text
    assert body["overall"] == {
        "sent": 3,
        "answered": 2,
        "response_rate": 0.667,
        "average_score": 4.5,
    }
    assert body["distribution"] == {"1": 0, "2": 0, "3": 0, "4": 1, "5": 1}
    assert [(q["name"], q["tally"]["sent"]) for q in body["by_queue"]] == [
        ("Triage", 3)
    ]
    assert [(s["name"], s["tally"]["answered"]) for s in body["by_staff"]] == [
        ("manager.a@clinicq.example", 2)
    ]
    assert body["answers_by_channel"] == {"web": 2}
    assert desk.staff("desk.a").get(_REPORT).status_code == status.HTTP_403_FORBIDDEN
    assert desk.staff("desk.b").get(_REPORT).status_code == status.HTTP_404_NOT_FOUND
    assert (
        desk.staff("manager.a")
        .get(_REPORT, params={"start": "2026-09-10", "end": "2026-09-01"})
        .status_code
        == status.HTTP_422_UNPROCESSABLE_CONTENT
    )


def test_comments_are_emptied_after_the_clinics_retention_and_a_closed_question_refuses(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """The same retention as other patient text: the comment goes, the score stays, once, audited by count."""
    client, _ = _patient(desk)
    _finish(desk, _visit(desk, client)["ticket"]["id"], _nurse(desk))
    [row] = _requests(desk)
    TestClient(desk.app).post(
        f"/api/v1/feedback/{row.token}", json={"score": 3, "comment": "Long wait"}
    )
    [row] = _requests(desk)
    expires = stored_sast(row.comment_expires_at)
    assert (
        expires - stored_sast(row.answered_at)
    ).days == 30  # the clinic's reason_retention_days

    with desk.session() as db:
        early = feedback.purge_expired_comments(
            db, moment=expires - timedelta(minutes=1)
        )
        purged = feedback.purge_expired_comments(db, moment=expires)
        again = feedback.purge_expired_comments(db, moment=expires + timedelta(days=1))
        db.commit()
    [row] = _requests(desk)
    assert (early, purged, again) == (0, 1, 0)
    assert (row.comment, row.score) == (None, 3)

    other, _ = _patient(desk)
    _finish(desk, _visit(desk, other)["ticket"]["id"], _nurse(desk))
    late = _requests(desk)[-1]
    with desk.session() as db, pytest.raises(feedback.FeedbackExpiredError):
        feedback.answer(
            db,
            db.get_one(VisitFeedback, late.id),
            score=5,
            comment=None,
            channel=feedback.FeedbackChannel.WEB,
            moment=stored_sast(late.expires_at),
        )


def test_a_walk_in_with_no_patient_is_not_asked(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """Nobody to message: no request, and nothing to count."""
    ticket = (
        desk.staff("desk.a")
        .post(desk.walk_in_path(desk.triage), json={})
        .json()["ticket"]
    )
    assert ticket["source"] == TicketSource.WALK_IN.value
    _finish(desk, ticket["id"], _nurse(desk))
    assert _requests(desk) == []
