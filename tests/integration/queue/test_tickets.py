"""Issuing tickets and counting a queue, on the in-memory database (Issue 39).

What PostgreSQL alone can prove (concurrency, the constraint refusing hand-written SQL, the plans)
is in ``test_sequence_concurrency.py`` and ``test_ticket_indexes.py``. This file covers the behaviour
that holds on any database, fast enough to run on every save: numbering per queue and service day,
the Johannesburg midnight, walk-ins without a patient, reference-code collisions, and the live
queue length discovery reads.
"""

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import TicketSource, TicketStatus
from src.commons.time import APP_TIMEZONE, business_date, now_sast
from src.database.models import Queue, TicketSequence
from src.modules.queue import sequence
from src.modules.queue.sequence import ReferenceCodeExhaustedError, issue_ticket
from src.modules.queues.live import read_waiting_counts
from tests.factories import PatientFactory, QueueFactory, SiteFactory, TicketFactory


def _queues(db: Session) -> tuple[Queue, Queue]:
    site = SiteFactory.create(db)
    return (
        QueueFactory.create(db, site_id=site.id, ticket_prefix="T"),
        QueueFactory.create(db, site_id=site.id, ticket_prefix="P"),
    )


def _sast(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=APP_TIMEZONE)


def test_each_queue_numbers_its_own_day_from_one(
    session_factory: sessionmaker[Session],
) -> None:
    """Two queues at one clinic keep separate sequences, each with its own prefix."""
    with session_factory() as db:
        triage, pharmacy = _queues(db)
        numbers = [
            issue_ticket(db, queue=queue, source=TicketSource.WALK_IN).number
            for queue in (triage, triage, pharmacy, triage, pharmacy)
        ]
        db.commit()
        assert numbers == ["T001", "T002", "P001", "T003", "P002"]


def test_the_service_day_is_the_johannesburg_date(
    session_factory: sessionmaker[Session],
) -> None:
    """23:59 then 00:01 Johannesburg time: the second ticket is #001 on the next service day."""
    day = date(2026, 9, 14)
    with session_factory() as db:
        triage, _ = _queues(db)
        before = issue_ticket(
            db, queue=triage, source=TicketSource.WALK_IN, moment=_sast(day, 23, 59)
        )
        after = issue_ticket(
            db,
            queue=triage,
            source=TicketSource.WALK_IN,
            moment=_sast(day + timedelta(days=1), 0, 1),
        )
        db.commit()
        counters = (
            db.execute(select(TicketSequence).order_by(TicketSequence.service_day))
            .scalars()
            .all()
        )
        assert (before.number, before.service_day) == ("T001", day)
        assert (after.number, after.service_day) == ("T001", day + timedelta(days=1))
        assert [(c.service_day, c.last_value) for c in counters] == [
            (day, 1),
            (day + timedelta(days=1), 1),
        ]


def test_a_walk_in_is_issued_without_a_patient_and_a_remote_join_is_not(
    session_factory: sessionmaker[Session],
) -> None:
    """No placeholder patient either way: a walk-in has none, a phone join must bring its own."""
    with session_factory() as db:
        triage, _ = _queues(db)
        walk_in = issue_ticket(
            db, queue=triage, source=TicketSource.WALK_IN, display_name="Gogo M."
        )
        with pytest.raises(ValueError, match="only a walk-in may have none"):
            issue_ticket(db, queue=triage, source=TicketSource.WHATSAPP)
        patient = PatientFactory.create(db)
        remote = issue_ticket(
            db, queue=triage, source=TicketSource.WEB, patient_id=patient.id
        )
        db.commit()
        assert walk_in.patient_id is None and walk_in.display_name == "Gogo M."
        assert remote.patient_id == patient.id
        assert walk_in.status == remote.status == TicketStatus.WAITING.value
        # The refused WhatsApp join allocated nothing: the next number is still 2.
        assert remote.number == "T002"


def test_a_colliding_reference_code_is_redrawn_and_keeps_the_number(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reference code already taken is drawn again inside a savepoint; the ticket keeps #002."""
    with session_factory() as db:
        triage, _ = _queues(db)
        first = issue_ticket(db, queue=triage, source=TicketSource.WALK_IN)
        draws = iter([first.reference_code, first.reference_code, "K7M4QP"])
        monkeypatch.setattr(sequence, "new_reference_code", lambda: next(draws))
        second = issue_ticket(db, queue=triage, source=TicketSource.WALK_IN)
        db.commit()
        assert (second.number, second.reference_code) == ("T002", "K7M4QP")


def test_a_broken_random_source_raises_rather_than_looping(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five collisions in a row is not chance: it stops with an error that says so."""
    with session_factory() as db:
        triage, _ = _queues(db)
        taken = issue_ticket(
            db, queue=triage, source=TicketSource.WALK_IN
        ).reference_code
        monkeypatch.setattr(sequence, "new_reference_code", lambda: taken)
        with pytest.raises(ReferenceCodeExhaustedError):
            issue_ticket(db, queue=triage, source=TicketSource.WALK_IN)


def test_the_live_queue_length_counts_todays_waiting_tickets(
    session_factory: sessionmaker[Session],
) -> None:
    """Discovery's direct read: today's waiting tickets per queue, and a measured 0 for an empty one.

    Yesterday's tickets and tickets in another queue are not counted.
    """
    with session_factory() as db:
        triage, pharmacy = _queues(db)
        for _ in range(3):
            TicketFactory.create(db, queue=triage)
        TicketFactory.create(db, queue=triage, moment=now_sast() - timedelta(days=1))
        db.commit()
        readings = read_waiting_counts(db, [triage, pharmacy])
        assert readings[triage.id].waiting == 3
        assert readings[pharmacy.id].waiting == 0
        assert readings[triage.id].as_of is not None
        assert business_date(readings[triage.id].as_of) == business_date()
