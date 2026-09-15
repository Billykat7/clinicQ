"""Patient notifications: one call from the queue, a transport per patient, delivery after the commit (Issue 63).

The issue's acceptance criteria, each demonstrated against the queue engine and the ledger:

* **every send is recorded with a terminal status**, including a message no transport can deliver;
* **a failed send retries with backoff and stops at the documented maximum**
  (``NOTIFICATION_MAX_ATTEMPTS``, backoff ``NOTIFICATION_RETRY_BASE_SECONDS * 2**(n-1)``);
* **transport selection honours the patient's preference before the fallback chain**, and the chain
  tries free transports before SMS, recording each transport it tried and what it cost;
* **sends never block a queue transition**: *Call next* commits while the SMS provider is down, a
  slow provider cannot hold the request in background mode, and a move that rolls back sends nothing;
* **adapters are swappable in tests without touching a real provider** (every test here does it).

"The queue engine calls one function and knows nothing about transports" is a source guard, in
``tests/unit/queue/test_queue_knows_no_transport.py``.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from starlette import status

from src.commons.enums import (
    ConsentPurpose,
    NotificationChannel,
    NotificationDispatchMode,
    NotificationStatus,
    NotificationTemplate,
    PatientChannel,
    PatientEvent,
    TicketSource,
    TicketStatus,
)
from src.commons.time import now_sast, stored_sast
from src.core.config import get_settings
from src.database.models import (
    Base,
    Notification,
    Patient,
    PatientNotificationPreference,
    Queue,
    Ticket,
)
from src.database.schema import sqlite_schema_translate_map
from src.modules.notifications import dispatch, service
from src.modules.notifications.transports import NoopTransport, use_transports
from src.modules.patients.consent import record_consent
from src.modules.queue.lifecycle import Actor, call_next
from tests.factories import PatientFactory, QueueFactory, SiteFactory, TicketFactory
from tests.integration.queue.conftest import SITE_A, open_all_day

_DESK = Actor.system()


def _consenting_patient(db: Session, *, consent: bool = True) -> Patient:
    """A patient who has agreed (or not) to be messaged."""
    patient = PatientFactory.create(db)
    if consent:
        record_consent(
            db,
            patient,
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
    return patient


def _waiting_ticket(db: Session, queue: Queue, patient: Patient) -> Ticket:
    """A web join by ``patient`` in ``queue``."""
    return TicketFactory.create(
        db, queue=queue, source=TicketSource.WEB, patient_id=patient.id
    )


def _ledger(db: Session, patient_id: str) -> list[Notification]:
    """The patient's ledger rows, oldest first."""
    db.expire_all()
    return list(
        db.scalars(
            select(Notification)
            .where(Notification.patient_id == patient_id)
            .order_by(Notification.created_at, Notification.attempts.desc())
        )
    )


def _notify(db: Session, patient: Patient, **overrides: object) -> Notification:
    """Record one "please come in" message for ``patient`` in ``db``'s transaction."""
    fields: dict[str, object] = {
        "patient_id": patient.id,
        "event": PatientEvent.CALLED,
        "context": {
            "number": "T001",
            "clinic": "Zola Clinic",
            "queue": "Triage",
            "room": "Room 2",
        },
        "site_id": SITE_A,
    }
    fields.update(overrides)
    notification = service.notify(db, **fields)  # type: ignore[arg-type]
    assert notification is not None
    return notification


# --- sends never block a queue transition ------------------------------------------------


def test_call_next_commits_while_the_sms_provider_is_down(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 3: the call is committed and answered; the message waits for a retry."""
    with desk.session() as db:
        patient = _consenting_patient(db)
        ticket = _waiting_ticket(db, db.get(Queue, desk.triage.id), patient)
        db.commit()
        ticket_id, patient_id = ticket.id, patient.id

    # Not a polite TransportError: a provider SDK blowing up with anything at all.
    down = NoopTransport(channel=NotificationChannel.SMS, fail_times=1_000)
    with use_transports(down):
        response = desk.staff("desk.a").post(
            f"/api/v1/sites/{SITE_A}/queues/{desk.triage.id}/tickets/call-next"
        )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["id"] == ticket_id
    with desk.session() as db:
        assert db.get(Ticket, ticket_id).status == TicketStatus.CALLED.value
        (row,) = _ledger(db, patient_id)
    assert down.attempts == 1 and down.sent == []
    assert row.template_key == NotificationTemplate.TICKET_CALLED.value
    assert row.status == NotificationStatus.FAILED.value
    assert row.attempts == 1 and row.next_attempt_at is not None
    assert "provider down" in (row.last_error or "")


def test_a_provider_raising_anything_cannot_undo_the_call(
    desk: SimpleNamespace,
) -> None:
    """An exception that is not a transport error is still recorded, not raised into the request."""

    class Exploding(NoopTransport):
        def send(self, *, to, message, patient=None):  # type: ignore[no-untyped-def]
            raise RuntimeError("the SDK crashed")

    with desk.session() as db:
        patient = _consenting_patient(db)
        ticket = _waiting_ticket(db, db.get(Queue, desk.triage.id), patient)
        db.commit()
        ticket_id, patient_id = ticket.id, patient.id

    with use_transports(Exploding(channel=NotificationChannel.SMS)):
        response = desk.staff("desk.a").post(
            f"/api/v1/sites/{SITE_A}/queues/{desk.triage.id}/tickets/call-next"
        )

    assert response.status_code == status.HTTP_200_OK, response.text
    with desk.session() as db:
        assert db.get(Ticket, ticket_id).status == TicketStatus.CALLED.value
        (row,) = _ledger(db, patient_id)
    assert row.status == NotificationStatus.FAILED.value
    assert row.last_error == "the SDK crashed"


def test_a_call_that_rolls_back_sends_nothing_and_leaves_no_row(
    desk: SimpleNamespace,
) -> None:
    """The ledger row is written in the move's transaction, so it rolls back with it."""
    sms = NoopTransport(channel=NotificationChannel.SMS)
    with desk.session() as db, use_transports(sms):
        patient = _consenting_patient(db)
        _waiting_ticket(db, db.get(Queue, desk.triage.id), patient)
        db.commit()
        patient_id = patient.id
        call_next(db, db.get(Queue, desk.triage.id), actor=_DESK)
        assert _pending_rows(db) == 1  # recorded inside the transaction
        db.rollback()
        assert _ledger(db, patient_id) == []
        db.commit()  # a later commit on the same session must not deliver the discarded message
    assert sms.sent == []


def _pending_rows(db: Session) -> int:
    """Rows visible inside the current transaction."""
    return len(db.scalars(select(Notification.id)).all())


@pytest.fixture
def file_database(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    """A SQLite file with a real connection pool, so a worker thread gets its own connection."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'notify.db'}", connect_args={"check_same_thread": False}
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    dispatch.drain()
    engine.dispose()


def test_a_slow_provider_does_not_hold_the_call_in_background_mode(
    file_database: sessionmaker[Session],
) -> None:
    """The default mode: the move commits and returns, and the message arrives a moment later."""
    slow = NoopTransport(channel=NotificationChannel.SMS, delay_seconds=2.0)
    with file_database() as db:
        site = SiteFactory.create(db)
        open_all_day(db, site.id)
        queue = QueueFactory.create(db, site_id=site.id)
        patient = _consenting_patient(db)
        _waiting_ticket(db, queue, patient)
        db.commit()

        with (
            use_transports(slow),
            dispatch.forced(NotificationDispatchMode.BACKGROUND),
        ):
            started = time.perf_counter()
            called = call_next(db, queue, actor=_DESK)
            db.commit()
            held_for = time.perf_counter() - started
            dispatch.drain()  # wait for the worker, only so the test can look at what it did

        assert held_for < 1.0, f"the call waited {held_for:.2f}s for the provider"
        assert called.status == TicketStatus.CALLED.value
        (row,) = _ledger(db, patient.id)
    assert row.status == NotificationStatus.SENT.value
    assert len(slow.sent) == 1


# --- every send is recorded; retries back off and stop -----------------------------------


def _sweep_at(db: Session, moment: datetime) -> None:
    """Run the retry sweep at ``moment`` and commit."""
    service.run_retry_sweep(db, now=moment)
    db.commit()


def test_a_send_that_fails_twice_then_succeeds_is_three_attempts_with_backoff(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 2: three attempts on one row, the waits doubling, final status sent."""
    base = get_settings().notification_retry_base_seconds
    flaky = NoopTransport(channel=NotificationChannel.SMS, fail_times=2)
    start = now_sast()
    waits: list[float] = []
    with desk.session() as db, use_transports(flaky):
        patient = _consenting_patient(db)
        row = _notify(db, patient, now=start)
        db.commit()  # attempt 1, after the commit: fails
        row_id = row.id

        for _ in range(2):  # attempts 2 and 3, each when the sweep finds the row due
            db.expire_all()
            current = db.get(Notification, row_id)
            due = stored_sast(current.next_attempt_at)
            waits.append((due - stored_sast(current.failed_at)).total_seconds())
            _sweep_at(db, due + timedelta(seconds=1))

        (final,) = _ledger(db, patient.id)
    assert flaky.attempts == 3 and len(flaky.sent) == 1
    assert final.attempts == 3
    assert final.status == NotificationStatus.SENT.value
    assert final.next_attempt_at is None
    assert waits[0] == pytest.approx(base) and waits[1] == pytest.approx(
        base * 2, abs=2
    )


def test_a_send_that_never_succeeds_stops_at_the_maximum_and_is_dead(
    desk: SimpleNamespace,
) -> None:
    """The budget is documented (``NOTIFICATION_MAX_ATTEMPTS``) and the last state is terminal."""
    maximum = get_settings().notification_max_attempts
    down = NoopTransport(channel=NotificationChannel.SMS, fail_times=1_000)
    moment = now_sast()
    with desk.session() as db, use_transports(down):
        patient = _consenting_patient(db)
        _notify(db, patient, now=moment)
        db.commit()
        for _ in range(
            maximum + 3
        ):  # more sweeps than the budget: the extra ones find nothing
            moment += timedelta(days=1)
            _sweep_at(db, moment)
        (row,) = _ledger(db, patient.id)
    assert down.attempts == maximum
    assert row.attempts == maximum
    assert row.status == NotificationStatus.DEAD.value
    assert row.next_attempt_at is None


def test_a_patient_without_consent_is_recorded_suppressed_and_not_sent(
    desk: SimpleNamespace,
) -> None:
    """Consent (Issue 21) comes before any transport: a terminal row, and nothing sent."""
    sms = NoopTransport(channel=NotificationChannel.SMS)
    with desk.session() as db, use_transports(sms):
        patient = _consenting_patient(db, consent=False)
        _notify(db, patient)
        db.commit()
        (row,) = _ledger(db, patient.id)
    assert row.status == NotificationStatus.SUPPRESSED.value
    assert "no-patient-consent" in (row.last_error or "")
    assert sms.sent == []


def test_a_patient_no_transport_can_reach_is_recorded_not_lost(
    desk: SimpleNamespace,
) -> None:
    """Nothing to send with is a terminal row saying so, never a silent drop."""
    unreachable = NoopTransport(channel=NotificationChannel.SMS, address=None)
    with desk.session() as db, use_transports(unreachable):
        patient = _consenting_patient(db)
        _notify(db, patient)
        db.commit()
        (row,) = _ledger(db, patient.id)
    assert row.status == NotificationStatus.SUPPRESSED.value
    assert row.last_error == "no transport can reach the patient"


def test_a_replayed_event_records_and_sends_one_message(desk: SimpleNamespace) -> None:
    """The same dedupe key twice, in two transactions: one row, one send."""
    sms = NoopTransport(channel=NotificationChannel.SMS)
    with desk.session() as db, use_transports(sms):
        patient = _consenting_patient(db)
        first = _notify(db, patient, dedupe_key="ticket-1:called:09:00")
        db.commit()
        again = _notify(db, patient, dedupe_key="ticket-1:called:09:00")
        db.commit()
        rows = _ledger(db, patient.id)
    assert again.id == first.id
    assert len(rows) == 1 and len(sms.sent) == 1


# --- which transport ---------------------------------------------------------------------


def _chain() -> tuple[NoopTransport, NoopTransport, NoopTransport]:
    """Web push and WhatsApp (free), and SMS (paid), all able to reach the patient."""
    return (
        NoopTransport(channel=NotificationChannel.WEB_PUSH, free=True, address="sub-1"),
        NoopTransport(channel=NotificationChannel.WHATSAPP, free=True, address="wa-1"),
        NoopTransport(
            channel=NotificationChannel.SMS,
            address="+27820000001",
            cost=Decimal("0.2500"),
        ),
    )


def test_a_free_transport_is_tried_before_sms(desk: SimpleNamespace) -> None:
    """With no preference, the chain's first free transport carries the message, at no cost."""
    push, whatsapp, sms = _chain()
    with (
        desk.session() as db,
        use_transports(sms, whatsapp, push),
    ):  # order given is irrelevant
        patient = _consenting_patient(db)
        _notify(db, patient)
        db.commit()
        (row,) = _ledger(db, patient.id)
    assert row.channel == NotificationChannel.WEB_PUSH.value
    assert row.status == NotificationStatus.SENT.value
    assert row.cost == Decimal("0") and row.cost_currency == "ZAR"
    assert len(push.sent) == 1 and whatsapp.sent == [] and sms.sent == []


def test_the_patients_preferred_transport_comes_before_the_chain(
    desk: SimpleNamespace,
) -> None:
    """A patient who chose SMS gets SMS, although two free transports could reach them."""
    push, whatsapp, sms = _chain()
    with desk.session() as db, use_transports(push, whatsapp, sms):
        patient = _consenting_patient(db)
        db.add(
            PatientNotificationPreference(
                patient_id=patient.id,
                preferred_channel=NotificationChannel.SMS.value,
            )
        )
        db.flush()
        _notify(db, patient)
        db.commit()
        (row,) = _ledger(db, patient.id)
    assert row.channel == NotificationChannel.SMS.value
    assert row.cost == Decimal("0.2500")
    assert len(sms.sent) == 1 and push.sent == [] and whatsapp.sent == []


def test_a_dead_free_transport_falls_back_down_the_chain_and_the_ledger_shows_it(
    desk: SimpleNamespace,
) -> None:
    """A revoked push subscription, WhatsApp down: SMS delivers, and all three rows are terminal."""
    push, whatsapp, sms = _chain()
    push.fail_permanently = True
    whatsapp.fail_times = 1
    with desk.session() as db, use_transports(push, whatsapp, sms):
        patient = _consenting_patient(db)
        _notify(db, patient)
        db.commit()
        rows = _ledger(db, patient.id)

    by_channel = {row.channel: row for row in rows}
    assert len(rows) == 3
    first, second, third = (by_channel[t.channel.value] for t in (push, whatsapp, sms))
    assert (first.status, second.status, third.status) == (
        NotificationStatus.DEAD.value,
        NotificationStatus.DEAD.value,
        NotificationStatus.SENT.value,
    )
    # Each free transport gets one attempt, because a fallback is waiting behind it.
    assert first.attempts == 1 and second.attempts == 1
    assert second.fallback_of_id == first.id and third.fallback_of_id == second.id
    assert third.cost == Decimal("0.2500")
    assert len(sms.sent) == 1


def test_call_next_tells_the_called_patient_and_the_one_now_first(
    desk: SimpleNamespace,
) -> None:
    """The queue's one call, end to end: "please come in" to the called, "you are next" to the next."""
    sms = NoopTransport(channel=NotificationChannel.SMS)
    with desk.session() as db, use_transports(sms):
        queue = db.get(Queue, desk.triage.id)
        first, second, third = (_consenting_patient(db) for _ in range(3))
        for patient in (first, second, third):
            _waiting_ticket(db, queue, patient)
        db.commit()
        call_next(db, queue, actor=_DESK)
        db.commit()
        call_next(db, queue, actor=_DESK)  # second is called; third is now first
        db.commit()
        first_told, second_told, third_told = (
            [row.template_key for row in _ledger(db, patient_id)]
            for patient_id in [first.id, second.id, third.id]
        )
    assert first_told == [NotificationTemplate.TICKET_CALLED.value]
    # Told both, first that they were next and then to come in (SQLite's clock has one-second
    # resolution, so the two rows are compared as a set rather than by creation time).
    assert sorted(second_told) == sorted(
        [
            NotificationTemplate.TICKET_NEXT.value,
            NotificationTemplate.TICKET_CALLED.value,
        ]
    )
    assert third_told == [NotificationTemplate.TICKET_NEXT.value]
    assert len(sms.sent) == 4
