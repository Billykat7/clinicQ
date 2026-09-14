"""The ticket lifecycle over HTTP and under concurrency (Issue 41, non-negotiable 2).

``tests/unit/queue/test_ticket_state_machine.py`` drives every ``(from, to)`` pair through the
service. This file drives them through the route a dashboard will call, and covers what only a real
request or a real database shows:

* every illegal pair answers **409** with ``ticket.transition.illegal`` and leaves the row as it was,
  and a cancellation or transfer asked for as a bare status change answers **409** with
  ``ticket.transition.dedicated_route`` (Issue 47): each has its own route;
* each move writes an **audit row** with the actor, what they were acting as, and the time;
* a move decided on a **stale** screen is refused, and another clinic's ticket is a **404**;
* a **terminal** ticket cannot be reopened, and a correction is a **new** ticket;
* *Call next* calls in sequence order and says so when nobody is waiting;
* on PostgreSQL, **concurrent moves on one ticket are serialised** and the loser gets 409, and two
  staff pressing *Call next* at once call **two different** tickets.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable, Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from starlette import status

from src.commons.enums import (
    ActorKind,
    AuditAction,
    AuditEntityType,
    SiteStatus,
    TicketStatus,
)
from src.commons.exceptions import ConflictError
from src.commons.time import stored_sast
from src.database.models import AuditEvent, Queue, SiteQueueSnapshot, Ticket
from src.database.schema import apply_postgres_search_path
from src.modules.queue.lifecycle import (
    DEDICATED_MOVE_CODE,
    DEDICATED_MOVES,
    ILLEGAL_TRANSITION_CODE,
    STALE_TRANSITION_CODE,
    Actor,
    call_next,
    is_legal,
    transition_ticket,
)
from tests.factories import (
    QueueFactory,
    SiteFactory,
    TicketFactory,
    drive_ticket_to,
)
from tests.integration.queue.conftest import open_all_day


def _move(desk: SimpleNamespace, ticket_id: str, to: TicketStatus, **extra: object):
    return desk.staff("desk.a").post(
        f"/api/v1/sites/{desk.triage.site_id}/tickets/{ticket_id}/transitions",
        json={"to": to.value, **extra},
    )


def _walk_in_at(desk: SimpleNamespace, current: TicketStatus) -> str:
    """A new walk-in in Triage, driven to ``current`` through the lifecycle; its id."""
    with desk.session() as db:
        ticket = drive_ticket_to(
            db, TicketFactory.create(db, queue=db.get(Queue, desk.triage.id)), current
        )
        db.commit()
        return ticket.id


def test_every_illegal_pair_is_a_409_over_http_and_changes_nothing(
    desk: SimpleNamespace,
) -> None:
    """All 64 pairs through the route: 200 and moved, or 409 with the row read back unchanged.

    ``cancelled`` and ``transferred`` are refused from every status here, legal or not: the cancel
    and transfer routes make them, with the channel and the next queue's ticket (Issue 47).
    """
    front = desk.staff("desk.a")
    refused = dedicated = 0
    for current, requested in itertools.product(TicketStatus, TicketStatus):
        ticket_id = _walk_in_at(desk, current)
        with desk.session() as db:
            before = db.get(Ticket, ticket_id)
            snapshot = (
                before.status,
                before.called_at,
                before.started_at,
                before.completed_at,
            )
        response = front.post(
            f"/api/v1/sites/{desk.triage.site_id}/tickets/{ticket_id}/transitions",
            json={"to": requested.value},
        )
        with desk.session() as db:
            after = db.get(Ticket, ticket_id)
            now = (after.status, after.called_at, after.started_at, after.completed_at)
        if requested in DEDICATED_MOVES:
            dedicated += 1
            assert response.status_code == status.HTTP_409_CONFLICT, (
                current,
                requested,
            )
            assert response.json()["code"] == DEDICATED_MOVE_CODE
            assert now == snapshot, (current, requested)
        elif is_legal(current, requested):
            assert response.status_code == status.HTTP_200_OK, (current, requested)
            assert response.json()["status"] == requested.value
        else:
            refused += 1
            assert response.status_code == status.HTTP_409_CONFLICT, (
                current,
                requested,
            )
            assert response.json()["code"] == ILLEGAL_TRANSITION_CODE
            assert now == snapshot, (current, requested)
    assert (refused, dedicated) == (41, 16)


def test_each_move_writes_an_audit_row_with_actor_role_and_time(
    desk: SimpleNamespace,
) -> None:
    """Criterion 3: who (the email and ``staff``), what (before and after), and when."""
    ticket_id = _walk_in_at(desk, TicketStatus.WAITING)

    called = _move(desk, ticket_id, TicketStatus.CALLED)

    assert called.status_code == status.HTTP_200_OK
    with desk.session() as db:
        row = (
            db.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.entity_type == AuditEntityType.TICKET.value,
                    AuditEvent.entity_id == ticket_id,
                    AuditEvent.action == AuditAction.UPDATE.value,
                )
                .order_by(AuditEvent.created_at.desc())
            )
            .scalars()
            .first()
        )
        ticket = db.get(Ticket, ticket_id)
    assert row is not None and ticket is not None
    assert row.actor == "desk.a@clinicq.example"
    assert row.actor_role == ActorKind.STAFF.value
    assert row.diff == {"status": {"before": "waiting", "after": "called"}}
    assert row.context == f"{ticket.number}: waiting → called"
    assert row.site_id == desk.triage.site_id and row.created_at is not None
    assert stored_sast(ticket.called_at).utcoffset() is not None


def test_a_move_decided_on_a_stale_screen_is_refused(desk: SimpleNamespace) -> None:
    """The screen said waiting, the ticket is now called: 409 stale, even though called → in progress is legal."""
    ticket_id = _walk_in_at(desk, TicketStatus.CALLED)

    stale = _move(desk, ticket_id, TicketStatus.IN_PROGRESS, expected_status="waiting")

    assert stale.status_code == status.HTTP_409_CONFLICT
    assert stale.json()["code"] == STALE_TRANSITION_CODE
    assert (
        stale.json()["detail"]
        == "This ticket is now called, not waiting. Refresh and try again."
    )


def test_another_clinics_ticket_is_not_found(desk: SimpleNamespace) -> None:
    """Non-negotiable 3 on the transition route: Clinic B's ticket is a 404 to Clinic A's desk."""
    with desk.session() as db:
        theirs = TicketFactory.create(db, queue=db.get(Queue, desk.other_triage.id))
        db.commit()
    response = _move(desk, theirs.id, TicketStatus.CALLED)
    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_a_terminal_ticket_cannot_be_reopened_and_a_correction_is_a_new_ticket(
    desk: SimpleNamespace,
) -> None:
    """Criterion 4. A patient marked done by mistake is not reopened: they join again, as T002."""
    patient, _ = desk.patient()
    first = patient.post(desk.join_path(desk.triage), json={}).json()["ticket"]
    with desk.session() as db:
        drive_ticket_to(db, db.get(Ticket, first["id"]), TicketStatus.DONE)
        db.commit()

    reopen = _move(desk, first["id"], TicketStatus.WAITING)
    again = patient.post(desk.join_path(desk.triage), json={})

    assert reopen.status_code == status.HTTP_409_CONFLICT
    assert reopen.json()["detail"] == "A done ticket cannot change any more."
    assert again.status_code == status.HTTP_201_CREATED
    assert again.json()["ticket"]["number"] == "T002"
    with desk.session() as db:
        assert db.get(Ticket, first["id"]).status == TicketStatus.DONE.value


def test_call_next_calls_in_sequence_and_says_when_nobody_is_waiting(
    desk: SimpleNamespace,
) -> None:
    """T001 then T002; the peek shows who is next; an empty queue is a 409 with a plain sentence."""
    front = desk.staff("desk.a")
    for _ in range(2):
        front.post(desk.walk_in_path(desk.triage), json={})
    base = f"/api/v1/sites/{desk.triage.site_id}/queues/{desk.triage.id}/tickets"

    peek = front.get(f"{base}/next").json()
    calls = [front.post(f"{base}/call-next") for _ in range(3)]

    assert peek["number"] == "T001"
    assert [call.json().get("number") for call in calls[:2]] == ["T001", "T002"]
    assert calls[2].status_code == status.HTTP_409_CONFLICT
    assert calls[2].json()["detail"] == "Nobody is waiting in this queue."
    assert front.get(f"{base}/next").json() is None
    with desk.session() as db:
        assert db.get(SiteQueueSnapshot, desk.triage.id).waiting == 0


# --- concurrency, on PostgreSQL ---------------------------------------------------------------


@pytest.fixture
def pg_queue(migrated_database: URL) -> Iterator[SimpleNamespace]:
    """An open, listed clinic with one queue on PostgreSQL, and a pooled session factory."""
    engine = create_engine(migrated_database, pool_size=8, max_overflow=0)
    apply_postgres_search_path(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        site = SiteFactory.create(db, status=SiteStatus.VERIFIED)
        open_all_day(db, site.id)
        queue = QueueFactory.create(db, site_id=site.id, ticket_prefix="T")
        db.commit()
    yield SimpleNamespace(session=factory, queue=queue)
    engine.dispose()


def _race(*jobs: Callable[[], object]) -> list[object]:
    """Run the jobs in threads released together; each result, or the exception it raised."""
    barrier = threading.Barrier(len(jobs))
    results: list[object] = [None] * len(jobs)

    def run(index: int, job: Callable[[], object]) -> None:
        barrier.wait()
        try:
            results[index] = job()
        except Exception as exc:  # the loser's 409 is the point
            results[index] = exc

    threads = [threading.Thread(target=run, args=pair) for pair in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def _staff(name: str) -> Actor:
    return Actor(kind=ActorKind.STAFF, label=f"{name}@clinicq.example")


@pytest.mark.postgres
def test_concurrent_moves_on_one_ticket_are_serialised_and_the_loser_gets_409(
    pg_queue: SimpleNamespace,
) -> None:
    """How to verify, step 3, ten times: two staff move one ticket at the same instant."""
    for round_ in range(10):
        with pg_queue.session() as db:
            ticket = TicketFactory.create(db, queue=db.get(Queue, pg_queue.queue.id))
            db.commit()

        def move(
            to: TicketStatus,
            who: str,
            expected: TicketStatus | None = None,
            ticket_id: str = ticket.id,
        ) -> str:
            with pg_queue.session() as db:
                moved = transition_ticket(
                    db, ticket_id, to, actor=_staff(who), expected_status=expected
                )
                db.commit()
                return moved.status

        # The same move by two people: the second finds called → called illegal.
        same = _race(
            lambda: move(TicketStatus.CALLED, "a"),
            lambda: move(TicketStatus.CALLED, "b"),
        )
        # Two different moves decided on the same screen: the second finds it no longer called.
        different = _race(
            lambda: move(TicketStatus.IN_PROGRESS, "a", TicketStatus.CALLED),
            lambda: move(TicketStatus.CANCELLED, "b", TicketStatus.CALLED),
        )
        assert sorted(type(r).__name__ for r in same) == [
            "IllegalTransitionError",
            "str",
        ], (
            round_,
            same,
        )
        assert sorted(type(r).__name__ for r in different) == [
            "StaleTransitionError",
            "str",
        ], (
            round_,
            different,
        )
        assert all(
            r.status_code == 409
            for r in (*same, *different)
            if isinstance(r, ConflictError)
        )
        with pg_queue.session() as db:
            audits = db.scalars(
                select(AuditEvent.id).where(
                    AuditEvent.entity_id == ticket.id,
                    AuditEvent.action == AuditAction.UPDATE.value,
                )
            ).all()
        assert len(audits) == 2  # only the two winners' moves are on the trail


@pytest.mark.postgres
def test_two_staff_pressing_call_next_at_once_call_two_different_tickets(
    pg_queue: SimpleNamespace,
) -> None:
    """``FOR UPDATE SKIP LOCKED``: ten rounds, never the same ticket twice, never a spurious 409."""
    with pg_queue.session() as db:
        queue = db.get(Queue, pg_queue.queue.id)
        for _ in range(20):
            TicketFactory.create(db, queue=queue)
        db.commit()

    def press(who: str) -> str:
        with pg_queue.session() as db:
            called = call_next(db, db.get(Queue, pg_queue.queue.id), actor=_staff(who))
            db.commit()
            return called.number

    called: list[object] = []
    for _ in range(10):
        pair = _race(lambda: press("a"), lambda: press("b"))
        assert all(isinstance(number, str) for number in pair), pair
        assert pair[0] != pair[1]
        called.extend(pair)
    assert sorted(called) == [f"T{n:03d}" for n in range(1, 21)]
