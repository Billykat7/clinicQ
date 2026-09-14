"""The queue engine under concurrency, on PostgreSQL (Issue 47).

An independent re-check of what M6 promised, written against the shipped services rather than copied
from the issues' own tests. Every race here is real: threads released together by a barrier, each
with its own connection and transaction, against a migrated PostgreSQL 18 database. The suite is meant
to be run many times in a row (the PR shows 20 consecutive runs); nothing in it depends on timing
luck, only on the database's locks.

* **Parallel joins** across all four channels and four queues: every queue's numbers are 1..n with
  no gap and no repeat, and every channel drew from the same sequence.
* **Simultaneous Call next**: several staff pressing at once, round after round, never call the same
  ticket twice and never miss one; and **one press sent several times at once** with the same
  ``Idempotency-Key`` (Issue 50) calls exactly one patient.
* **Transfer during call**, **cancel during call**: a ticket raced by two moves ends in exactly one
  state, with exactly one winner, and the loser changed nothing.
* **One patient, two channels, one instant**: one ticket.
* **The 07:30 rush**: 200 joins in ten minutes over four queues, with staff calling and the board
  reading throughout, measured against :data:`RUSH_BUDGET` (see its note).
"""

from __future__ import annotations

import logging
import os
import random
import statistics
import threading
import time
import traceback
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from src.commons.enums import (
    ActorKind,
    PatientChannel,
    SiteStatus,
    TicketSource,
    TicketStatus,
    TransferReason,
)
from src.commons.exceptions import ConflictError
from src.core.site_scope import SiteAccess
from src.database.models import Patient, Queue, QueueRequestKey, Site, Ticket, User
from src.database.schema import apply_postgres_search_path
from src.modules.patients.service import patient_for_gateway
from src.modules.queue.cancellation import cancel_own_ticket
from src.modules.queue.lifecycle import Actor, NobodyWaitingError, call_next
from src.modules.queue.request_keys import run_once
from src.modules.queue.service import join_queue
from src.modules.queue.transfer import transfer_ticket
from src.modules.queues.live import read_waiting_counts
from src.modules.sites.hours import published_schedules
from tests.factories import (
    PatientFactory,
    QueueFactory,
    SiteFactory,
    StaffFactory,
    TicketFactory,
)
from tests.integration.queue.conftest import open_all_day, queue_settings

pytestmark = pytest.mark.postgres

#: Measurements a reviewer reads: ``pytest --log-cli-level=INFO`` prints them.
logger = logging.getLogger(__name__)

#: Budgets generous enough that only a real bug trips them; the limits are the locks, not the clock.
_SETTINGS = queue_settings(
    queue_join_rate_limit_per_phone=10_000,
    queue_join_rate_limit_per_ip=10_000,
    queue_join_site_daily_cap=10_000,
)


def _staff(name: str) -> Actor:
    return Actor(kind=ActorKind.STAFF, label=f"{name}@clinicq.example")


@pytest.fixture
def world(migrated_database: URL) -> Iterator[SimpleNamespace]:
    """An open, listed clinic with four queues and a doctor's queue, on PostgreSQL."""
    engine = create_engine(
        migrated_database, pool_size=20, max_overflow=0, pool_timeout=60
    )
    apply_postgres_search_path(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        site = SiteFactory.create(db, status=SiteStatus.VERIFIED)
        open_all_day(db, site.id)
        queues = [
            QueueFactory.create(db, site_id=site.id, ticket_prefix=prefix)
            for prefix in "TACI"
        ]
        doctor = QueueFactory.create(db, site_id=site.id, ticket_prefix="D")
        db.commit()
    yield SimpleNamespace(session=factory, site=site, queues=queues, doctor=doctor)
    engine.dispose()


def _race(*jobs: Callable[[], object]) -> list[object]:
    """Run jobs in threads released together; each job's result, or the exception it raised."""
    barrier = threading.Barrier(len(jobs))
    results: list[object] = [None] * len(jobs)

    def run(index: int, job: Callable[[], object]) -> None:
        barrier.wait()
        try:
            results[index] = job()
        except Exception as exc:
            results[index] = exc

    threads = [threading.Thread(target=run, args=pair) for pair in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def _join(
    world: SimpleNamespace, queue_id: str, source: TicketSource, n: int
) -> tuple[str, int]:
    """One join through ``join_queue``, as the channel ``source`` would make it."""
    with world.session() as db:
        site = db.get(Site, world.site.id)
        queue = db.get(Queue, queue_id)
        patient = None
        if source in (TicketSource.USSD, TicketSource.WHATSAPP):
            patient = patient_for_gateway(
                db,
                msisdn=f"+2710555{7000 + n:04d}",
                channel=PatientChannel(source.value),
            )
        elif source is TicketSource.WEB:
            patient = PatientFactory.create(db, phone_e164=f"+2710555{5000 + n:04d}")
        result = join_queue(
            db,
            site=site,
            queue=queue,
            schedule=published_schedules(db, [site.id])[site.id],
            source=source,
            patient=patient,
            actor="concurrency-test",
            settings=_SETTINGS,
        )
        db.commit()
        return queue_id, result.ticket.sequence


def test_parallel_joins_from_every_channel_into_four_queues_are_gapless_per_queue(
    world: SimpleNamespace,
) -> None:
    """80 joins at once, spread over four queues and four channels: each queue reads 1..n exactly."""
    rng = random.Random(47)
    plan = [
        (rng.choice(world.queues).id, rng.choice(list(TicketSource)), n)
        for n in range(80)
    ]

    results = _race(*(lambda q=q, s=s, n=n: _join(world, q, s, n) for q, s, n in plan))

    errors = [result for result in results if isinstance(result, Exception)]
    assert errors == []
    by_queue: dict[str, list[int]] = {}
    for queue_id, sequence in results:  # type: ignore[misc]
        by_queue.setdefault(queue_id, []).append(sequence)
    for queue_id, sequences in by_queue.items():
        assert sorted(sequences) == list(range(1, len(sequences) + 1)), queue_id
    with world.session() as db:
        channels = Counter(db.scalars(select(Ticket.source)).all())
    assert set(channels) == {source.value for source in TicketSource}


def test_the_first_joins_on_a_new_queue_at_once_all_succeed(
    world: SimpleNamespace,
) -> None:
    """Ten new queues, eight first joins on each at the same instant: every join gets its ticket.

    The first join on a queue creates its ``site_queue_snapshot`` row. The 07:30 rush found a web
    join failing on that row's primary key twice in 20 runs. The web join's analytics event had
    committed its ticket halfway through, releasing the number lock that serialises a queue's joins,
    so a second join read "no snapshot row" before the first had written one. The event now stays in
    the join's transaction, and the snapshot write is one upsert either way.
    """
    for round_ in range(10):
        with world.session() as db:
            queue = QueueFactory.create(db, site_id=world.site.id, ticket_prefix="N")
            db.commit()

        results = _race(
            *(
                lambda n=n, q=queue.id, r=round_: _join(
                    world, q, TicketSource.WEB, 1000 + r * 10 + n
                )
                for n in range(8)
            )
        )

        assert [r for r in results if isinstance(r, Exception)] == [], round_
        assert sorted(sequence for _, sequence in results) == list(range(1, 9))  # type: ignore[misc]


def test_staff_pressing_call_next_at_once_never_call_the_same_ticket(
    world: SimpleNamespace,
) -> None:
    """How to verify, step 2, harder: five staff at once, six rounds, thirty tickets, each called once."""
    queue_id = world.queues[0].id
    with world.session() as db:
        queue = db.get(Queue, queue_id)
        waiting = {TicketFactory.create(db, queue=queue).id for _ in range(30)}
        db.commit()

    def press(who: str) -> str:
        with world.session() as db:
            ticket = call_next(db, db.get(Queue, queue_id), actor=_staff(who))
            db.commit()
            return ticket.id

    called: list[object] = []
    for _ in range(6):
        called.extend(_race(*(lambda w=w: press(f"staff{w}") for w in range(5))))

    assert all(isinstance(ticket_id, str) for ticket_id in called), called
    assert len(called) == len(set(called)) == 30
    assert set(called) == waiting


def test_one_press_sent_twice_at_the_same_instant_calls_one_patient(
    world: SimpleNamespace,
) -> None:
    """Issue 50's double tap, raced: one Idempotency-Key, four requests at once, ten rounds.

    Every request answers with the same ticket, exactly one ticket is called per round, and one key row
    is kept: the second insert of the key waits on the unique constraint for the first transaction and
    replays its answer, so the requests that lost the race never call anyone.
    """
    queue_id = world.queues[1].id
    with world.session() as db:
        queue = db.get(Queue, queue_id)
        for _ in range(10):
            TicketFactory.create(db, queue=queue)
        staff = StaffFactory.create(db, site_id=world.site.id)
        db.commit()
        staff_id = staff.id

    for round_ in range(10):
        key = f"double-tap-{round_:04d}"

        def press(key: str = key) -> str:
            with world.session() as db:
                access = SiteAccess(site_id=world.site.id, user=db.get(User, staff_id))
                ticket = run_once(
                    db,
                    access,
                    key,
                    operation="call_next",
                    target=queue_id,
                    act=lambda: call_next(
                        db, db.get(Queue, queue_id), actor=_staff("desk")
                    ),
                )
                return ticket.id

        answers = _race(*(press for _ in range(4)))
        assert all(isinstance(answer, str) for answer in answers), answers
        assert len(set(answers)) == 1, (round_, answers)

    with world.session() as db:
        called = db.scalar(
            select(func.count())
            .select_from(Ticket)
            .where(
                Ticket.queue_id == queue_id, Ticket.status == TicketStatus.CALLED.value
            )
        )
        keys = db.scalar(
            select(func.count())
            .select_from(QueueRequestKey)
            .where(QueueRequestKey.user_id == staff_id)
        )
    assert (called, keys) == (10, 10)


def test_a_transfer_racing_a_call_next_ends_in_exactly_one_state(
    world: SimpleNamespace,
) -> None:
    """Ten rounds: one waiting ticket, one nurse calling next, one transferring it to the doctor.

    Exactly one move wins. Called: nothing appears at the doctor. Transferred: exactly one new ticket
    there, and Call next found nobody. Never both, never neither.
    """
    queue_id = world.queues[1].id
    for round_ in range(10):
        with world.session() as db:
            ticket = TicketFactory.create(db, queue=db.get(Queue, queue_id))
            db.commit()

        def call() -> str:
            with world.session() as db:
                called = call_next(db, db.get(Queue, queue_id), actor=_staff("nurse"))
                db.commit()
                return called.status

        def transfer(ticket_id: str = ticket.id) -> str:
            with world.session() as db:
                moved = transfer_ticket(
                    db,
                    ticket_id,
                    db.get(Queue, world.doctor.id),
                    actor=_staff("desk"),
                    reason=TransferReason.WRONG_QUEUE,
                )
                db.commit()
                return moved.from_ticket.status

        called, transferred = _race(call, transfer)
        winners = [r for r in (called, transferred) if isinstance(r, str)]
        losers = [r for r in (called, transferred) if isinstance(r, Exception)]
        assert len(winners) == 1 and len(losers) == 1, (round_, called, transferred)
        assert isinstance(losers[0], (ConflictError, NobodyWaitingError)), losers
        with world.session() as db:
            status = db.get(Ticket, ticket.id).status
            at_doctor = db.scalar(
                select(func.count(Ticket.id)).where(
                    Ticket.transferred_from_id == ticket.id
                )
            )
        assert (status, at_doctor) in {
            (TicketStatus.CALLED.value, 0),
            (TicketStatus.TRANSFERRED.value, 1),
        }, (round_, status, at_doctor)


def test_a_patient_cancelling_while_being_called_ends_in_exactly_one_state(
    world: SimpleNamespace,
) -> None:
    """Ten rounds: the patient cancels on USSD as the desk presses Call next. One wins, cleanly."""
    queue_id = world.queues[2].id
    for round_ in range(10):
        with world.session() as db:
            patient = PatientFactory.create(
                db, phone_e164=f"+2710555{9000 + round_:04d}"
            )
            ticket = TicketFactory.create(
                db,
                queue=db.get(Queue, queue_id),
                source=TicketSource.USSD,
                patient_id=patient.id,
            )
            db.commit()

        def cancel(ticket_id: str = ticket.id, patient_id: str = patient.id) -> str:
            with world.session() as db:
                result = cancel_own_ticket(
                    db, patient_id, ticket_id, channel=PatientChannel.USSD
                )
                db.commit()
                return result.ticket.status

        def call() -> str:
            with world.session() as db:
                called = call_next(db, db.get(Queue, queue_id), actor=_staff("desk"))
                db.commit()
                return called.status

        cancelled, called = _race(cancel, call)
        outcomes = sorted(
            type(r).__name__ if isinstance(r, Exception) else r
            for r in (cancelled, called)
        )
        with world.session() as db:
            status = db.get(Ticket, ticket.id).status
        assert outcomes in (
            ["NobodyWaitingError", "cancelled"],
            ["CalledTicketSelfCancelError", "called"],
        ), (round_, outcomes)
        assert status in {TicketStatus.CANCELLED.value, TicketStatus.CALLED.value}


def test_one_patient_joining_on_two_channels_at_once_holds_one_ticket(
    world: SimpleNamespace,
) -> None:
    """The web and USSD, same number, same queue, same instant, ten times: one ticket each time."""
    queue_id = world.queues[3].id
    for round_ in range(10):
        phone = f"+2710555{8000 + round_:04d}"
        with world.session() as db:
            PatientFactory.create(db, phone_e164=phone)
            db.commit()

        def by(channel: PatientChannel, number: str = phone) -> str:
            with world.session() as db:
                site = db.get(Site, world.site.id)
                patient = (
                    patient_for_gateway(db, msisdn=number, channel=channel)
                    if channel is PatientChannel.USSD
                    else db.execute(
                        select(Patient).where(Patient.phone_e164 == number)
                    ).scalar_one()
                )
                result = join_queue(
                    db,
                    site=site,
                    queue=db.get(Queue, queue_id),
                    schedule=published_schedules(db, [site.id])[site.id],
                    source=TicketSource(channel.value),
                    patient=patient,
                    actor="concurrency-test",
                    settings=_SETTINGS,
                )
                db.commit()
                return result.ticket.id

        answers = _race(lambda: by(PatientChannel.WEB), lambda: by(PatientChannel.USSD))
        assert all(isinstance(answer, str) for answer in answers), answers
        assert len(set(answers)) == 1, (round_, answers)


# --- the 07:30 rush ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RushBudget:
    """What the engine must meet in the rush scenario.

    **Provisional.** No performance budget is written down yet: Issue 105 (M14) owns the real one,
    measured over HTTP on production-sized servers with board and dashboard clients. These figures
    are the engine's share of it at the database layer, set so a regression is caught long before
    a patient would notice: a join a patient waits well under a second for, and a board read fast
    enough to refresh every second. On a laptop a clean rush measures a join p95 of about 50 ms, a
    board read p95 of about 25 ms and a *Call next* p95 of about 25 ms (PR 47); the budget allows
    about six times that for a slower CI runner, not tuned to pass.

    **Why up to three attempts.** Forty local runs showed that about one rush in seven stalled for a
    second or so. During the stall every thread stopped at once, including the ones that only read,
    and PostgreSQL's sessions sat in ``ClientRead``, waiting on the test process. That is the machine
    (a Docker VM on a laptop, a shared CI runner) pausing, not the engine. A latency budget judged on
    one ten-second window would fail on those pauses, so the budget is met if **any** of
    :attr:`attempts` rushes meets it. A real regression slows every attempt and still fails, and every
    attempt's report is logged. Correctness (no errors, gapless numbers, no ticket called twice) gets
    no retry: it is asserted on every attempt.
    """

    join_p95_ms: float = 300.0
    join_max_ms: float = 1_500.0
    board_read_p95_ms: float = 150.0
    call_next_p95_ms: float = 200.0
    attempts: int = 3
    #: The rush's ten minutes are replayed this many times faster, so the scenario takes 10 s and
    #: joins arrive 60 times more densely than they would: a harder test than the real morning.
    compression: int = 60


RUSH_BUDGET = RushBudget()
RUSH_JOINS = 200
RUSH_MINUTES = 10
#: A clinic's four queues and how the morning divides between them.
RUSH_QUEUES = {"T": 0.45, "A": 0.25, "C": 0.20, "I": 0.10}
#: How patients arrive at 07:30: most at the door, the rest by phone.
RUSH_CHANNELS = {
    TicketSource.WALK_IN: 0.40,
    TicketSource.USSD: 0.25,
    TicketSource.WEB: 0.20,
    TicketSource.WHATSAPP: 0.15,
}
#: The app's request workers the joins share.
RUSH_WORKERS = 16


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


@dataclass
class _Timings:
    joins: list[float] = field(default_factory=list)
    queued: list[float] = field(default_factory=list)
    board_reads: list[float] = field(default_factory=list)
    calls: list[float] = field(default_factory=list)
    called: list[str] = field(default_factory=list)
    errors: list[BaseException] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, bucket: list, value: object) -> None:
        with self.lock:
            bucket.append(value)


def _rush(migrated_database: URL, attempt: int) -> dict[str, Any]:
    """One rush at a new clinic; asserts correctness and returns the report, budget unchecked."""
    engine = create_engine(
        migrated_database, pool_size=RUSH_WORKERS + 6, max_overflow=0
    )
    apply_postgres_search_path(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = queue_settings()
    with factory() as db:
        site = SiteFactory.create(db, status=SiteStatus.VERIFIED)
        open_all_day(db, site.id)
        queues = {
            prefix: QueueFactory.create(db, site_id=site.id, ticket_prefix=prefix)
            for prefix in RUSH_QUEUES
        }
        rng = random.Random(730)
        plan = []
        for n in range(RUSH_JOINS):
            prefix = rng.choices(list(RUSH_QUEUES), weights=list(RUSH_QUEUES.values()))[
                0
            ]
            source = rng.choices(
                list(RUSH_CHANNELS), weights=list(RUSH_CHANNELS.values())
            )[0]
            web_patient = None
            if source is TicketSource.WEB:  # signed in before they left home
                web_patient = PatientFactory.create(
                    db, phone_e164=f"+2710{556 + 2 * attempt}{n:04d}"
                ).id
            at = rng.betavariate(1.3, 3.0) * RUSH_MINUTES * 60 / RUSH_BUDGET.compression
            plan.append((at, n, queues[prefix].id, source, web_patient))
        db.commit()
    plan.sort()
    timings = _Timings()
    done = threading.Event()
    started = time.perf_counter()

    def join(
        at: float, n: int, queue_id: str, source: TicketSource, web_patient: str | None
    ):
        delay = at - (time.perf_counter() - started)
        if delay > 0:
            time.sleep(delay)
        begun = time.perf_counter()
        timings.add(timings.queued, (begun - started - at) * 1000)
        try:
            with factory() as db:
                site_row = db.get(Site, site.id)
                patient = None
                if source in (TicketSource.USSD, TicketSource.WHATSAPP):
                    patient = patient_for_gateway(
                        db,
                        msisdn=f"+2710{557 + 2 * attempt}{n:04d}",
                        channel=PatientChannel(source.value),
                    )
                elif web_patient is not None:
                    patient = db.get(Patient, web_patient)
                join_queue(
                    db,
                    site=site_row,
                    queue=db.get(Queue, queue_id),
                    schedule=published_schedules(db, [site.id])[site.id],
                    source=source,
                    patient=patient,
                    actor="rush",
                    walk_in_name="Walk-in" if source is TicketSource.WALK_IN else None,
                    client_ip=f"10.0.{n // 250}.{n % 250}"
                    if source is TicketSource.WEB
                    else None,
                    settings=settings,
                )
                db.commit()
        except BaseException as exc:
            timings.add(timings.errors, exc)
            return
        timings.add(timings.joins, (time.perf_counter() - begun) * 1000)

    def board() -> None:
        while not done.is_set():
            begun = time.perf_counter()
            try:
                with factory() as db:
                    read_waiting_counts(
                        db, [db.get(Queue, q.id) for q in queues.values()]
                    )
            except BaseException as exc:
                timings.add(timings.errors, exc)
                return
            timings.add(timings.board_reads, (time.perf_counter() - begun) * 1000)
            done.wait(0.5)

    def staff(queue_id: str, every: float) -> None:
        while not done.wait(every):
            begun = time.perf_counter()
            try:
                with factory() as db:
                    ticket = call_next(
                        db, db.get(Queue, queue_id), actor=_staff(queue_id[-4:])
                    )
                    db.commit()
            except NobodyWaitingError:
                continue
            except BaseException as exc:
                timings.add(timings.errors, exc)
                return
            timings.add(timings.calls, (time.perf_counter() - begun) * 1000)
            timings.add(timings.called, ticket.id)

    waits: Counter[str] = Counter()

    def watch() -> None:
        """What the database's other sessions were waiting on, sampled every 20 ms: the report's
        answer to "was a slow moment our locks or the machine's disk?"."""
        with engine.connect() as conn:
            while not done.wait(0.02):
                for kind, event in conn.execute(
                    text(
                        "SELECT wait_event_type, wait_event FROM pg_stat_activity "
                        "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                        "AND state = 'active' AND wait_event IS NOT NULL"
                    )
                ):
                    waits[f"{kind}:{event}"] += 1
            conn.rollback()

    side = [threading.Thread(target=board), threading.Thread(target=watch)] + [
        threading.Thread(target=staff, args=(q.id, 180 / RUSH_BUDGET.compression))
        for q in queues.values()
    ]
    for thread in side:
        thread.start()
    with ThreadPoolExecutor(max_workers=RUSH_WORKERS) as pool:
        list(pool.map(lambda job: join(*job), plan))
    elapsed = time.perf_counter() - started
    done.set()
    for thread in side:
        thread.join()
    with factory() as db:
        numbers = {
            prefix: sorted(
                db.scalars(
                    select(Ticket.sequence).where(Ticket.queue_id == queue.id)
                ).all()
            )
            for prefix, queue in queues.items()
        }
    engine.dispose()

    report = {
        "joins": len(timings.joins),
        "errors": len(timings.errors),
        "elapsed_s": round(elapsed, 1),
        "join_p50_ms": round(statistics.median(timings.joins), 1),
        "join_p95_ms": round(_percentile(timings.joins, 0.95), 1),
        "join_max_ms": round(max(timings.joins), 1),
        "worker_wait_p95_ms": round(_percentile(timings.queued, 0.95), 1),
        "board_reads": len(timings.board_reads),
        "board_read_p95_ms": round(_percentile(timings.board_reads, 0.95), 1),
        "calls": len(timings.calls),
        "call_next_p95_ms": round(_percentile(timings.calls, 0.95), 1)
        if timings.calls
        else None,
        "per_queue": {prefix: len(found) for prefix, found in numbers.items()},
        "db_waits": dict(waits.most_common(4)),
    }
    logger.info("07:30 rush, attempt %d: %s", attempt + 1, report)
    assert timings.errors == [], "".join(traceback.format_exception(timings.errors[0]))
    assert report["joins"] == RUSH_JOINS
    for prefix, found in numbers.items():
        assert found == list(range(1, len(found) + 1)), prefix
    assert len(timings.called) == len(set(timings.called))
    return report


def _over_budget(report: dict[str, Any]) -> list[str]:
    """Each figure in ``report`` that is over :data:`RUSH_BUDGET`."""
    limits = {
        "join_p95_ms": RUSH_BUDGET.join_p95_ms,
        "join_max_ms": RUSH_BUDGET.join_max_ms,
        "board_read_p95_ms": RUSH_BUDGET.board_read_p95_ms,
        "call_next_p95_ms": RUSH_BUDGET.call_next_p95_ms,
    }
    return [
        f"{name} {report[name]} > {limit}"
        for name, limit in limits.items()
        if report[name] is not None and report[name] > limit
    ]


def test_the_0730_rush_meets_the_budget(migrated_database: URL) -> None:
    """How to verify, step 3: 200 joins in ten minutes over four queues, staff calling, board reading.

    Arrivals are front-loaded like a real morning (most in the first minutes), spread over the four
    queues and all four channels, with the **real** abuse limits, and replayed 60 times faster on a
    pool of 16 workers. Throughout, one staff member per queue presses *Call next* every few
    (compressed) minutes and a board reads every queue's length twice a second. The budget is
    :data:`RUSH_BUDGET`, met by any of its attempts (see its note); every attempt is logged.

    It measures latency, so it runs **alone**. Under ``pytest -n`` it skips: with every CPU busy with
    other tests, a join p50 measured 190–270 ms against about 30 ms alone, which measures the machine.
    CI runs it in its own step after the parallel run (``.github/workflows/ci.yml``).
    """
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.skip(
            "the 07:30 rush measures latency and runs alone: "
            "pytest tests/integration/queue/test_queue_concurrency.py -k rush"
        )
    misses = []
    for attempt in range(RUSH_BUDGET.attempts):
        over = _over_budget(_rush(migrated_database, attempt))
        if not over:
            return
        misses.append(f"attempt {attempt + 1}: {', '.join(over)}")
    pytest.fail("the rush missed the budget on every attempt:\n" + "\n".join(misses))
