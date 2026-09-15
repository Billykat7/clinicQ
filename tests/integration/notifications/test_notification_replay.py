"""A replayed queue event sends exactly one notification (Issue 71).

Re-verified from the shipped code: real joins, the real *Call next*, the real recall timer sweep and the real
post-commit delivery, with one in-memory SMS transport counting what reaches a phone. Each queue event is then
**replayed** the ways it can be in production:

* the move's own message is recorded again (a retried request, an event handled twice): one ledger row;
* "you are next" is looked for again on a line that has not changed: one message;
* the recall sweep runs twice for the same moment (two instances, a catch-up run): one "called again";
* the post-commit delivery of one row runs twice, and the retry sweep runs after it: one send.

In every case exactly one message reaches the transport and the ledger holds one row per event.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from src.commons.enums import (
    ConsentPurpose,
    NotificationChannel,
    NotificationStatus,
    PatientChannel,
    PatientEvent,
    TicketStatus,
)
from src.commons.time import stored_sast
from src.database.models import Notification, Patient, Queue, Ticket
from src.modules.notifications import service
from src.modules.notifications.transports import NoopTransport, use_transports
from src.modules.patients.consent import record_consent
from src.modules.queue import notices, timers
from src.modules.queue.lifecycle import Actor, call_next

_DESK = Actor.system()


@pytest.fixture
def phone() -> Iterator[NoopTransport]:
    """The only transport: SMS, reaching every patient, counting every message it is handed."""
    sms = NoopTransport(channel=NotificationChannel.SMS, address="+27820000001")
    with use_transports(sms):
        yield sms


def _join(desk: SimpleNamespace) -> tuple[str, str]:
    """A consenting patient's web ticket in Triage: ``(patient id, ticket id)``."""
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
    answer = client.post(desk.join_path(desk.triage), json={})
    return patient_id, answer.json()["ticket"]["id"]


def _messages(
    desk: SimpleNamespace, patient_id: str, template: str
) -> list[Notification]:
    with desk.session() as db:
        return list(
            db.scalars(
                select(Notification).where(
                    Notification.patient_id == patient_id,
                    Notification.template_key == template,
                )
            )
        )


def _sent_to_phone(phone: NoopTransport, text: str) -> int:
    return sum(text in delivery.text for delivery in phone.sent)


def test_replaying_the_call_and_the_next_in_line_look_sends_one_of_each(
    desk: SimpleNamespace, phone: NoopTransport
) -> None:
    """How to verify, step 2."""
    first_patient, first_ticket = _join(desk)
    second_patient, _ = _join(desk)
    with desk.session() as db:
        call_next(db, db.get_one(Queue, desk.triage.id), actor=_DESK)
        db.commit()

    # Replay both events in new transactions, exactly as the move made them.
    for _ in range(3):
        with desk.session() as db:
            ticket = db.get_one(Ticket, first_ticket)
            called_at = stored_sast(ticket.called_at)
            notices.tell(
                db,
                ticket,
                PatientEvent.CALLED,
                moment=called_at,
                occurrence=called_at.isoformat(),
            )
            notices.tell_next_in_line(
                db, ticket.queue_id, ticket.service_day, moment=called_at
            )
            db.commit()
        with desk.session() as db:
            service.run_retry_sweep(db, now=called_at + timedelta(hours=1))
            db.commit()

    called = _messages(desk, first_patient, "ticket_called")
    next_up = _messages(desk, second_patient, "ticket_next")
    assert [(row.status, row.attempts) for row in called] == [
        (NotificationStatus.SENT.value, 1)
    ]
    assert [(row.status, row.attempts) for row in next_up] == [
        (NotificationStatus.SENT.value, 1)
    ]
    assert _sent_to_phone(phone, "please come in now") == 1
    assert _sent_to_phone(phone, "you are next") == 1
    assert len(phone.sent) == 2


def test_a_recall_sweep_run_twice_for_the_same_moment_calls_again_once(
    desk: SimpleNamespace, phone: NoopTransport
) -> None:
    patient_id, ticket_id = _join(desk)
    with desk.session() as db:
        call_next(db, db.get_one(Queue, desk.triage.id), actor=_DESK)
        db.commit()
        called_at = stored_sast(db.get_one(Ticket, ticket_id).called_at)
    late = called_at + timedelta(minutes=desk.settings.queue_recall_timeout_minutes + 1)

    swept = []
    for _ in range(2):
        with desk.session() as db:
            swept.append(timers.run_recall_timers(db, moment=late))
            db.commit()
        with desk.session() as db:
            # The same event, replayed by hand as well: what a second instance's sweep would say.
            ticket = db.get_one(Ticket, ticket_id)
            notices.tell(db, ticket, PatientEvent.RECALLED, moment=late, minutes=5)
            db.commit()

    assert [len(result.recalled) for result in swept] == [1, 0]
    with desk.session() as db:
        assert db.get_one(Ticket, ticket_id).status == TicketStatus.RECALLED.value
    recalled = _messages(desk, patient_id, "ticket_recalled")
    assert [(row.status, row.attempts) for row in recalled] == [
        (NotificationStatus.SENT.value, 1)
    ]
    assert len(phone.sent) == 2  # "please come in now", then "called again": once each


def test_delivering_one_committed_row_twice_and_sweeping_after_sends_it_once(
    desk: SimpleNamespace, phone: NoopTransport
) -> None:
    patient_id, _ = _join(desk)
    with desk.session() as db:
        call_next(db, db.get_one(Queue, desk.triage.id), actor=_DESK)
        db.commit()
    (row,) = _messages(desk, patient_id, "ticket_called")
    assert row.status == NotificationStatus.SENT.value, "delivered after the commit"

    bind = desk.session().get_bind()
    assert service.deliver_committed(bind, row.id) is False
    assert service.deliver_committed(bind, row.id) is False
    with desk.session() as db:
        service.run_retry_sweep(db, now=stored_sast(row.created_at) + timedelta(days=1))
        db.commit()
        assert db.scalar(select(func.count(Notification.id))) == 1

    assert len(phone.sent) == 1
