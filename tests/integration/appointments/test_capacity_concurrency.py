"""The appointment capacity guard under real concurrency, on a real PostgreSQL (Issue 80).

Every test here runs on PostgreSQL 18 in a throwaway database brought to ``head`` by the real
migrations, as Issue 39's ticket-number tests do: SQLite's single writer would make any of them pass
by construction. Threads are released together by a barrier, each books or joins in its own
transaction, and ``pg_stat_activity`` is sampled on a separate connection so a pass cannot come from
the threads happening to run one after another.

* **Two sessions book the last place in a slot at once: one succeeds, the other is told it is full.**
  Then the same with twenty sessions and three places left.
* **The check constraint refuses an overbooked slot** written by hand, whatever the application does
  (the negative control: it proves the database, not only the code path, holds the line).
* **Walk-ins and bookings racing for one queue's day never pass its limit together**, and every
  refusal is the right one: ``queue.join.queue_full`` for a walk-in, ``day_full`` for a booking.
* **Different days of one queue do not wait for each other**: a booking holding next week's lock does
  not block a walk-in today.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import time, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from src.commons.enums import (
    AppointmentStatus,
    JoinRefusal,
    SiteStatus,
    SlotRefusal,
    TicketSource,
)
from src.commons.time import business_date, now_sast
from src.database.models import Appointment, AppointmentSlot, Queue, Site
from src.database.schema import apply_postgres_search_path
from src.modules.appointments import capacity
from src.modules.queue.service import JoinRefusedError, join_queue
from src.modules.sites.hours import OpeningSchedule, TimeSpan
from tests.factories import PatientFactory, QueueFactory, SiteFactory

pytestmark = pytest.mark.postgres

logger = logging.getLogger(__name__)

#: Connections for the racers: one each, checked out before the barrier, so every racer is ready to
#: act when it is released. The largest race here is 20; CI's PostgreSQL has 100 connections.
POOL = 20
#: How long each racer holds its transaction open after acting, to widen the window a naive check
#: would lose in. A real booking also audits before it commits.
HOLD_SECONDS = 0.05

#: Open around the clock, so the race is never refused by the hour the suite runs at.
ALWAYS_OPEN = OpeningSchedule(
    weekly={weekday: (TimeSpan(time(0), time(0)),) for weekday in range(7)}
)


@pytest.fixture
def world(migrated_database: URL) -> Iterator[SimpleNamespace]:
    """A listed clinic with one queue and a pooled engine on a migrated database."""
    engine = create_engine(
        migrated_database, pool_size=POOL, max_overflow=0, pool_timeout=60
    )
    apply_postgres_search_path(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        site = SiteFactory.create(db, status=SiteStatus.VERIFIED)
        queue = QueueFactory.create(db, site_id=site.id, ticket_prefix="C")
        db.commit()
    watch_engine = create_engine(migrated_database, poolclass=NullPool)
    yield SimpleNamespace(
        engine=engine,
        watch_engine=watch_engine,
        session=factory,
        site=site,
        queue=queue,
    )
    watch_engine.dispose()
    engine.dispose()


@dataclass
class Contention:
    """The most transactions PostgreSQL had open, and waiting on a lock, at one moment."""

    most_in_transaction: int = 0
    most_waiting_on_a_lock: int = 0


class _Watch(threading.Thread):
    """Samples this database's other backends until stopped."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(daemon=True)
        self._engine = engine
        self._stop = threading.Event()
        #: Set once the first sample is taken, so a race is never over before it is watched.
        self.ready = threading.Event()
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
                # A new transaction per sample: pg_stat_activity is a snapshot per transaction.
                conn.rollback()
                self.seen.most_in_transaction = max(
                    self.seen.most_in_transaction, int(in_transaction)
                )
                self.seen.most_waiting_on_a_lock = max(
                    self.seen.most_waiting_on_a_lock, int(waiting)
                )
                self.ready.set()
                self._stop.wait(0.002)

    def stop(self) -> Contention:
        self._stop.set()
        self.join()
        return self.seen


def _race[T](
    world: SimpleNamespace, racers: list[Callable[[Session], T]]
) -> tuple[list[T | BaseException], Contention]:
    """Release every racer at once, each in its own transaction; return what each did and the contention."""
    barrier = threading.Barrier(len(racers))

    def run(act: Callable[[Session], T]) -> T | BaseException:
        with world.session() as db:
            # Check a connection out first, so the barrier releases racers that are ready to act
            # rather than ones still connecting.
            db.connection()
            barrier.wait()
            try:
                outcome = act(db)
                db.execute(text("SELECT pg_sleep(:s)"), {"s": HOLD_SECONDS})
                db.commit()
                return outcome
            except Exception as exc:
                db.rollback()
                return exc

    watch = _Watch(world.watch_engine)
    watch.start()
    watch.ready.wait(timeout=10)
    with ThreadPoolExecutor(max_workers=len(racers)) as pool:
        outcomes = list(pool.map(run, racers))
    return outcomes, watch.stop()


def _slot(
    world: SimpleNamespace, *, capacity_: int, booked: int, days_ahead: int = 1
) -> str:
    """A slot with ``booked`` of its ``capacity_`` places already taken."""
    starts = now_sast().replace(microsecond=0) + timedelta(days=days_ahead)
    with world.session() as db:
        slot = AppointmentSlot(
            site_id=world.site.id,
            queue_id=world.queue.id,
            starts_at=starts,
            ends_at=starts + timedelta(minutes=15),
            service_day=business_date(starts),
            capacity=capacity_,
        )
        db.add(slot)
        db.flush()
        queue = db.get(Queue, world.queue.id)
        assert queue is not None
        for _ in range(booked):
            capacity.claim_place(
                db, slot=slot, queue=queue, patient_id=PatientFactory.create(db).id
            )
        db.commit()
        return slot.id


def _booker(world: SimpleNamespace, slot_id: str) -> Callable[[Session], str]:
    """A racer that books one place in ``slot_id`` for a new patient."""
    with world.session() as db:
        patient_id = PatientFactory.create(db).id
        db.commit()

    def act(db: Session) -> str:
        slot = db.get(AppointmentSlot, slot_id)
        queue = db.get(Queue, world.queue.id)
        assert slot is not None and queue is not None
        return capacity.claim_place(
            db, slot=slot, queue=queue, patient_id=patient_id
        ).id

    return act


def test_two_sessions_booking_the_last_place_one_wins_and_one_is_told_it_is_full(
    world: SimpleNamespace,
) -> None:
    """Capacity 2 with 1 booked: of two simultaneous bookings, exactly one succeeds."""
    slot_id = _slot(world, capacity_=2, booked=1)

    outcomes, seen = _race(world, [_booker(world, slot_id), _booker(world, slot_id)])

    won = [o for o in outcomes if isinstance(o, str)]
    refused = [o for o in outcomes if isinstance(o, capacity.SlotRefusedError)]
    logger.info(
        "last place: %s won, %s refused, contention %s", len(won), len(refused), seen
    )
    assert len(won) == 1 and len(refused) == 1, outcomes
    assert refused[0].refusal is SlotRefusal.SLOT_FULL
    assert str(refused[0]) == capacity.REFUSAL_MESSAGES[SlotRefusal.SLOT_FULL]
    assert seen.most_in_transaction >= 2
    assert seen.most_waiting_on_a_lock >= 1  # the loser really waited on the winner
    with world.session() as db:
        slot = db.get(AppointmentSlot, slot_id)
        assert slot is not None and slot.booked_count == slot.capacity == 2


def test_twenty_sessions_on_three_places_give_exactly_three_bookings(
    world: SimpleNamespace,
) -> None:
    """Capacity 5 with 2 booked, 20 racers: three succeed, seventeen are full, the counter agrees."""
    slot_id = _slot(world, capacity_=5, booked=2)

    outcomes, seen = _race(world, [_booker(world, slot_id) for _ in range(20)])

    won = [o for o in outcomes if isinstance(o, str)]
    refused = [o for o in outcomes if isinstance(o, capacity.SlotRefusedError)]
    logger.info("twenty on three: %s won, contention %s", len(won), seen)
    assert (len(won), len(refused)) == (3, 17), outcomes
    assert seen.most_waiting_on_a_lock >= 1
    with world.session() as db:
        slot = db.get(AppointmentSlot, slot_id)
        assert slot is not None and slot.booked_count == 5
        held = db.execute(
            select(func.count(Appointment.id)).where(
                Appointment.slot_id == slot_id,
                Appointment.status == AppointmentStatus.BOOKED.value,
            )
        ).scalar_one()
        assert held == 5


def test_the_database_refuses_an_overbooked_slot_whatever_writes_it(
    world: SimpleNamespace,
) -> None:
    """The negative control: ``booked_count = capacity + 1`` written by hand is refused by the constraint."""
    slot_id = _slot(world, capacity_=2, booked=2)

    with (
        world.session() as db,
        pytest.raises(IntegrityError, match="ck_appointment_slot_not_overbooked"),
    ):
        db.execute(
            update(AppointmentSlot)
            .where(AppointmentSlot.id == slot_id)
            .values(booked_count=AppointmentSlot.booked_count + 1)
        )
        db.flush()


def test_walk_ins_and_bookings_racing_for_one_day_never_pass_its_limit(
    world: SimpleNamespace,
) -> None:
    """A day of 6 with 2 walk-ins in: 8 walk-ins and 8 bookings race; exactly 4 more places are taken."""
    today = business_date(now_sast())
    with world.session() as db:
        queue = db.get(Queue, world.queue.id)
        site = db.get(Site, world.site.id)
        assert queue is not None and site is not None
        queue.max_daily_capacity = 6
        db.commit()
    slot_id = _slot(world, capacity_=20, booked=0, days_ahead=0)
    for _ in range(2):
        with world.session() as db:
            _walk_in(world)(db)
            db.commit()

    racers: list[Callable[[Session], object]] = []
    for index in range(16):
        racers.append(_walk_in(world) if index % 2 else _booker(world, slot_id))
    outcomes, seen = _race(world, racers)

    taken = [o for o in outcomes if not isinstance(o, BaseException)]
    walk_ins_refused = [o for o in outcomes if isinstance(o, JoinRefusedError)]
    bookings_refused = [o for o in outcomes if isinstance(o, capacity.SlotRefusedError)]
    unexpected = [
        o
        for o in outcomes
        if isinstance(o, BaseException)
        and not isinstance(o, JoinRefusedError | capacity.SlotRefusedError)
    ]
    logger.info(
        "shared day: %s taken, %s walk-ins refused, %s bookings refused, contention %s",
        len(taken),
        len(walk_ins_refused),
        len(bookings_refused),
        seen,
    )
    assert not unexpected, unexpected
    assert len(taken) == 4
    assert all(o.refusal is JoinRefusal.QUEUE_FULL for o in walk_ins_refused)
    assert all(o.refusal is SlotRefusal.DAY_FULL for o in bookings_refused)
    with world.session() as db:
        queue = db.get(Queue, world.queue.id)
        assert queue is not None
        usage = capacity.day_usage(db, queue, today)
        assert usage.taken == 6 and usage.remaining == 0


def test_a_booking_holding_next_week_does_not_block_a_walk_in_today(
    world: SimpleNamespace,
) -> None:
    """The lock is one queue's one day: next week's booking and today's walk-in do not wait."""
    with world.session() as db:
        queue = db.get(Queue, world.queue.id)
        assert queue is not None
        queue.max_daily_capacity = 10
        db.commit()
    next_week = business_date(now_sast()) + timedelta(days=7)
    with world.session() as holder:
        capacity.lock_day(holder, world.queue.id, next_week)
        with world.session() as db:
            db.execute(text("SET LOCAL lock_timeout = '2s'"))
            result = _walk_in(world)(db)
            db.commit()
        holder.rollback()
    assert result is not None


def _walk_in(world: SimpleNamespace) -> Callable[[Session], object]:
    """A racer that issues one walk-in ticket through the join service."""

    def act(db: Session) -> object:
        site = db.get(Site, world.site.id)
        queue = db.get(Queue, world.queue.id)
        assert site is not None and queue is not None
        result = join_queue(
            db,
            site=site,
            queue=queue,
            schedule=ALWAYS_OPEN,
            source=TicketSource.WALK_IN,
            patient=None,
            actor="desk@clinicq.example",
        )
        return result.ticket.id

    return act
