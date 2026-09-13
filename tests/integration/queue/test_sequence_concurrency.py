"""Ticket numbers under real concurrency, on a real PostgreSQL (Issue 39).

The criteria are about what the **database** guarantees, so every test here runs on PostgreSQL 18 in
a throwaway database brought to ``head`` by the real migrations, never on SQLite, whose single writer
would make any concurrency test pass by construction:

* **100 concurrent joins give 1–100 with no gaps and no repeats.** A hundred threads are released
  at once by a barrier and each issues a ticket in its own transaction through
  :func:`~src.modules.queue.sequence.issue_ticket`. The test also records how many of those
  transactions were open at the same moment, so a pass cannot come from the threads happening to run
  one after another.
* **The naive allocator fails the same harness.** ``COUNT(*) + 1`` under the same barrier repeats
  numbers, and ``uq_ticket_queue_id_service_day_sequence`` refuses every repeat. This is the
  negative control: it proves the harness can see the bug the allocator exists to prevent, and that
  the constraint is what stops it reaching a patient.
* **A duplicate inserted by hand is refused** by the constraint, whatever the application does.
* **A rolled-back join leaves no gap**, and **joins on different queues do not wait** for each other
  while joins on one queue do.
* **The number restarts at 1 at Johannesburg midnight**, including at 01:59 SAST, which is still the
  previous day in UTC.
* **A walk-in needs no patient; a remote join cannot lack one**, enforced by a check constraint.
"""

import logging
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from src.commons.enums import DbSchema, TicketSource
from src.commons.time import APP_TIMEZONE, business_date, now_sast
from src.database.models import Ticket, TicketSequence
from src.database.schema import apply_postgres_search_path
from src.modules.queue.sequence import (
    allocate_sequence,
    format_ticket_number,
    issue_ticket,
    new_reference_code,
)
from tests.factories import PatientFactory, QueueFactory, SiteFactory

pytestmark = pytest.mark.postgres

#: Measurements a reviewer reads: ``pytest --log-cli-level=INFO`` prints them.
logger = logging.getLogger(__name__)

SCHEMA = DbSchema.CLINICQ.value
#: The criterion's number of simultaneous joins.
JOINS = 100
#: Connections the joins share. Fewer than the joins on purpose: that is how the app runs (a pool
#: behind many requests), and CI's PostgreSQL has 100 connections for every parallel test worker.
POOL = 20
#: How long each join holds its transaction open after allocating, to widen the window in which a
#: naive allocator would read a stale count. Real joins also audit and snapshot before committing.
HOLD_SECONDS = 0.01


@pytest.fixture
def world(migrated_database: URL) -> Iterator[SimpleNamespace]:
    """A clinic with two queues and a patient in a migrated database, and a pooled engine on it."""
    engine = create_engine(
        migrated_database, pool_size=POOL, max_overflow=0, pool_timeout=60
    )
    apply_postgres_search_path(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        site = SiteFactory.create(db)
        triage = QueueFactory.create(db, site_id=site.id, ticket_prefix="T")
        pharmacy = QueueFactory.create(db, site_id=site.id, ticket_prefix="P")
        patient = PatientFactory.create(db)
        db.commit()
    watch_engine = create_engine(migrated_database, poolclass=NullPool)
    yield SimpleNamespace(
        engine=engine,
        watch_engine=watch_engine,
        session=factory,
        site=site,
        queue=triage,
        other_queue=pharmacy,
        patient=patient,
    )
    watch_engine.dispose()
    engine.dispose()


@dataclass
class Contention:
    """What PostgreSQL saw during a race: the proof the joins really ran at the same time.

    Sampled from ``pg_stat_activity`` on a separate connection every few milliseconds, so it counts
    what the **database** had open, not what Python threads believed they were doing.
    """

    most_in_transaction: int = 0
    most_waiting_on_a_lock: int = 0


class _Watch(threading.Thread):
    """Samples this database's other backends until stopped."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(daemon=True)
        self._engine = engine
        self._stop = threading.Event()
        self.seen = Contention()

    def run(self) -> None:
        with self._engine.connect() as conn:
            while not self._stop.is_set():
                in_transaction, waiting = conn.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE xact_start IS NOT NULL), "
                        "count(*) FILTER (WHERE wait_event_type = 'Lock') "
                        "FROM pg_stat_activity "
                        "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                    )
                ).one()
                conn.rollback()
                self.seen.most_in_transaction = max(
                    self.seen.most_in_transaction, in_transaction
                )
                self.seen.most_waiting_on_a_lock = max(
                    self.seen.most_waiting_on_a_lock, waiting
                )
                self._stop.wait(0.002)

    def stop(self) -> Contention:
        self._stop.set()
        self.join()
        return self.seen


def _race(
    engine: Engine, factory: sessionmaker[Session], work: Callable[[Session], int]
) -> tuple[list[int], list[BaseException], Contention]:
    """Run ``work`` in ``JOINS`` threads released together; return results, errors and contention."""
    barrier = threading.Barrier(JOINS)

    def one() -> int:
        barrier.wait()
        with factory() as db:
            value = work(db)
            threading.Event().wait(HOLD_SECONDS)
            db.commit()
            return value

    results: list[int] = []
    errors: list[BaseException] = []
    watch = _Watch(engine)
    watch.start()
    try:
        with ThreadPoolExecutor(max_workers=JOINS) as pool:
            for future in [pool.submit(one) for _ in range(JOINS)]:
                try:
                    results.append(future.result())
                except Exception as exc:  # collected: the negative control expects some
                    errors.append(exc)
    finally:
        seen = watch.stop()
    return results, errors, seen


def test_100_concurrent_joins_get_1_to_100_with_no_gaps_or_repeats(
    world: SimpleNamespace,
) -> None:
    """The criterion. A hundred joins at once on one queue: exactly 1–100, each once."""

    def join(db: Session) -> int:
        return issue_ticket(db, queue=world.queue, source=TicketSource.WALK_IN).sequence

    sequences, errors, seen = _race(world.watch_engine, world.session, join)

    assert errors == []
    assert sorted(sequences) == list(range(1, JOINS + 1))
    # Not one after another: PostgreSQL had many join transactions open, queued on the counter.
    assert seen.most_in_transaction > 1, (
        "the joins did not overlap, so this proved nothing"
    )
    assert seen.most_waiting_on_a_lock > 1, "no join waited for the counter's lock"
    with world.session() as db:
        stored = db.execute(
            select(Ticket.sequence, Ticket.number).where(
                Ticket.queue_id == world.queue.id
            )
        ).all()
        counter = db.get(TicketSequence, (world.queue.id, business_date()))
    assert sorted(sequence for sequence, _ in stored) == list(range(1, JOINS + 1))
    assert {number for _, number in stored} == {
        format_ticket_number("T", n) for n in range(1, JOINS + 1)
    }
    assert counter is not None and counter.last_value == JOINS
    logger.info(
        "%d joins: 1–%d, no gaps, no repeats; PostgreSQL saw up to %d join transactions open "
        "at once and up to %d waiting on the counter's lock",
        JOINS,
        JOINS,
        seen.most_in_transaction,
        seen.most_waiting_on_a_lock,
    )


def test_count_plus_one_repeats_numbers_under_the_same_load_and_the_constraint_refuses_them(
    world: SimpleNamespace,
) -> None:
    """The negative control: the classic ``COUNT(*) + 1`` allocator, in the same harness, breaks.

    Every repeated number it computes is refused by the unique constraint, so the failure is an
    error at the database rather than two patients holding the same ticket.
    """

    def naive_join(db: Session) -> int:
        today = business_date()
        sequence = (
            db.execute(
                select(func.count())
                .select_from(Ticket)
                .where(Ticket.queue_id == world.queue.id, Ticket.service_day == today)
            ).scalar_one()
            + 1
        )
        # The read-then-write window, as in a real request.
        threading.Event().wait(HOLD_SECONDS)
        db.add(
            Ticket(
                site_id=world.site.id,
                queue_id=world.queue.id,
                service_day=today,
                sequence=sequence,
                number=format_ticket_number("T", sequence),
                reference_code=new_reference_code(),
                source=TicketSource.WALK_IN.value,
                joined_at=now_sast(),
            )
        )
        db.flush()
        return sequence

    sequences, errors, seen = _race(world.watch_engine, world.session, naive_join)

    duplicates = [
        error
        for error in errors
        if isinstance(error, IntegrityError)
        and "uq_ticket_queue_id_service_day_sequence" in str(error.orig)
    ]
    assert duplicates, (
        "COUNT(*) + 1 did not collide; the harness is not concurrent enough"
    )
    assert len(duplicates) == len(errors)
    assert len(set(sequences)) == len(sequences)  # what did commit is still unique
    logger.info(
        "COUNT(*)+1: %d of %d joins computed a number already taken and were refused by the "
        "constraint (PostgreSQL saw up to %d join transactions open at once)",
        len(duplicates),
        JOINS,
        seen.most_in_transaction,
    )


def test_a_duplicate_inserted_by_hand_is_refused_by_the_constraint(
    world: SimpleNamespace,
) -> None:
    """Criterion 2: SQL that bypasses the application cannot create a second ``T001`` today."""
    with world.session() as db:
        first = issue_ticket(db, queue=world.queue, source=TicketSource.WALK_IN)
        db.commit()

    with world.engine.connect() as conn, pytest.raises(IntegrityError) as refused:
        conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.ticket (id, site_id, queue_id, service_day, sequence, "
                "number, reference_code, source, status, joined_at) VALUES (:id, :site, :queue, "
                ":day, :sequence, 'T001', 'ZZZZZZ', 'walk_in', 'waiting', now())"
            ),
            {
                "id": "0199b0c0-0000-7000-8000-000000000dup",
                "site": world.site.id,
                "queue": world.queue.id,
                "day": first.service_day,
                "sequence": first.sequence,
            },
        )
    assert 'unique constraint "uq_ticket_queue_id_service_day_sequence"' in str(
        refused.value.orig
    )


def test_a_rolled_back_join_leaves_no_gap(world: SimpleNamespace) -> None:
    """A join that fails after allocating gives its number back: the next join takes it."""
    with world.session() as db:
        assert (
            issue_ticket(db, queue=world.queue, source=TicketSource.WALK_IN).sequence
            == 1
        )
        db.commit()
    with world.session() as db:
        assert allocate_sequence(db, world.queue.id, business_date()) == 2
        db.rollback()  # the queue was full, a check failed, the request died
    with world.session() as db:
        assert (
            issue_ticket(db, queue=world.queue, source=TicketSource.WALK_IN).sequence
            == 2
        )
        db.commit()


def test_joins_wait_only_for_their_own_queue(world: SimpleNamespace) -> None:
    """The counter's lock is one queue's day: the pharmacy does not wait for triage."""
    today = business_date()
    holder = world.session()
    try:
        allocate_sequence(
            holder, world.queue.id, today
        )  # open, holding triage's counter

        with world.session() as other:
            other.execute(text("SET LOCAL lock_timeout = '1s'"))
            assert allocate_sequence(other, world.other_queue.id, today) == 1
            other.commit()

        with world.session() as same, pytest.raises(OperationalError) as waited:
            same.execute(text("SET LOCAL lock_timeout = '300ms'"))
            allocate_sequence(same, world.queue.id, today)
        assert "lock timeout" in str(waited.value.orig)
    finally:
        holder.rollback()
        holder.close()


def _sast(day: date, hour: int, minute: int) -> datetime:
    """An aware Johannesburg wall-clock moment."""
    return datetime.combine(day, time(hour, minute), tzinfo=APP_TIMEZONE)


def test_numbering_restarts_at_johannesburg_midnight_not_utc_midnight(
    world: SimpleNamespace,
) -> None:
    """How to verify, step 3: 23:59 then 00:01 Johannesburg time, and the second ticket is #001.

    01:59 SAST is 23:59 UTC on the **previous** date, so a service day taken from the UTC date
    would put that ticket back on yesterday's counter and number it #004.
    """
    day = date(2026, 9, 14)
    with world.session() as db:
        late = [
            issue_ticket(
                db,
                queue=world.queue,
                source=TicketSource.WALK_IN,
                moment=_sast(day, 23, m),
            )
            for m in (57, 58, 59)
        ]
        after_midnight = issue_ticket(
            db,
            queue=world.queue,
            source=TicketSource.WALK_IN,
            moment=_sast(day + timedelta(days=1), 0, 1),
        )
        before_utc_midnight = issue_ticket(
            db,
            queue=world.queue,
            source=TicketSource.WALK_IN,
            moment=_sast(day + timedelta(days=1), 1, 59),
        )
        db.commit()

    assert [t.number for t in late] == ["T001", "T002", "T003"]
    assert {t.service_day for t in late} == {day}
    assert after_midnight.number == "T001"
    assert after_midnight.service_day == day + timedelta(days=1)
    # Still the 14th in UTC, the 15th in Johannesburg: the Johannesburg date is the one used.
    assert before_utc_midnight.joined_at.astimezone(UTC).date() == day
    assert before_utc_midnight.number == "T002"
    assert before_utc_midnight.service_day == day + timedelta(days=1)


def test_a_walk_in_needs_no_patient_and_a_remote_join_cannot_lack_one(
    world: SimpleNamespace,
) -> None:
    """Criterion 5, both halves, at the database: no placeholder patient is ever needed or allowed."""
    with world.session() as db:
        walk_in = issue_ticket(
            db, queue=world.queue, source=TicketSource.WALK_IN, display_name="Gogo M."
        )
        remote = issue_ticket(
            db, queue=world.queue, source=TicketSource.USSD, patient_id=world.patient.id
        )
        db.commit()
        assert walk_in.patient_id is None
        assert remote.patient_id == world.patient.id
        patients = db.execute(
            text(f"SELECT count(*) FROM {SCHEMA}.patient")
        ).scalar_one()
    assert patients == 1  # the walk-in created no patient row

    with world.engine.connect() as conn, pytest.raises(IntegrityError) as refused:
        conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.ticket (id, site_id, queue_id, service_day, sequence, "
                "number, reference_code, source, joined_at) VALUES (:id, :site, :queue, "
                "CURRENT_DATE, 99, 'T099', 'YYYYYY', 'web', now())"
            ),
            {
                "id": "0199b0c0-0000-7000-8000-00000000web0",
                "site": world.site.id,
                "queue": world.queue.id,
            },
        )
    assert 'check constraint "ck_ticket_patient_or_walk_in"' in str(refused.value.orig)
