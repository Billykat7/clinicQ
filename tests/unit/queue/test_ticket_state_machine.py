"""The ticket state machine, every pair of statuses, and the diagram that documents it (Issue 41).

Not a sample: every ``(from, to)`` pair of :class:`~src.commons.enums.TicketStatus`, 64 of them. A
real ticket is driven to ``from`` through :func:`~src.modules.queue.lifecycle.transition_ticket`
along the table's own shortest path, then asked to move to ``to``:

* a **legal** move lands, stamps its time and writes exactly one audit row;
* an **illegal** move raises :class:`~src.modules.queue.lifecycle.IllegalTransitionError` (HTTP 409)
  and changes **nothing**: after committing the session, the row read back fresh has the same status
  and timestamps, and the audit trail has no new row.

The same pairs are driven over HTTP in ``tests/integration/queue/test_ticket_lifecycle.py``. The
table's shape (terminal statuses have no way out, every status is reachable) and the diagram in
``docs/PRODUCT/03-booking-and-queue.md`` are checked here too.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from src.commons.enums import (
    TICKET_TERMINAL_STATUSES,
    ActorKind,
    AuditEntityType,
    TicketStatus,
)
from src.database.models import AuditEvent, Ticket
from src.modules.queue.lifecycle import (
    ILLEGAL_TRANSITION_CODE,
    TRANSITIONS,
    Actor,
    IllegalTransitionError,
    is_legal,
    transition_ticket,
)
from tests.factories import (
    QueueFactory,
    SiteFactory,
    TicketFactory,
    drive_ticket_to,
    status_path,
)

_DOC = (
    Path(__file__).resolve().parents[3] / "docs" / "PRODUCT" / "03-booking-and-queue.md"
)
_PAIRS = list(itertools.product(TicketStatus, TicketStatus))
_ACTOR = Actor(kind=ActorKind.STAFF, label="desk@clinicq.example", user_id="u-1")


def _audit_rows(db: Session, ticket_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.entity_type == AuditEntityType.TICKET.value,
                AuditEvent.entity_id == ticket_id,
            )
        )
    )


def _row(db: Session, ticket_id: str) -> tuple[object, ...]:
    """Everything a move could change, read fresh from the database."""
    db.expire_all()
    ticket = db.get(Ticket, ticket_id)
    assert ticket is not None
    return (ticket.status, ticket.called_at, ticket.started_at, ticket.completed_at)


@pytest.mark.parametrize(
    ("current", "requested"), _PAIRS, ids=[f"{a.value}->{b.value}" for a, b in _PAIRS]
)
def test_every_pair_is_either_a_legal_move_or_a_409_that_changes_nothing(
    session_factory: sessionmaker[Session],
    current: TicketStatus,
    requested: TicketStatus,
) -> None:
    """The criterion, exhaustively."""
    with session_factory() as db:
        queue = QueueFactory.create(db, site_id=SiteFactory.create(db).id)
        ticket = drive_ticket_to(db, TicketFactory.create(db, queue=queue), current)
        db.commit()
        before = _row(db, ticket.id)
        audited = _audit_rows(db, ticket.id)

        if is_legal(current, requested):
            moved = transition_ticket(db, ticket.id, requested, actor=_ACTOR)
            db.commit()
            assert _row(db, ticket.id)[0] == requested.value == moved.status
            assert _audit_rows(db, ticket.id) == audited + 1
            return

        with pytest.raises(IllegalTransitionError) as refused:
            transition_ticket(db, ticket.id, requested, actor=_ACTOR)
        db.commit()  # nothing was staged: committing now must not change the row either
        assert refused.value.status_code == 409
        assert refused.value.code == ILLEGAL_TRANSITION_CODE
        assert _row(db, ticket.id) == before
        assert _audit_rows(db, ticket.id) == audited


def test_the_table_speaks_for_every_status_and_nothing_leaves_a_terminal_one() -> None:
    """Terminal statuses have no exits; every other status has at least one; every one is reachable."""
    assert set(TRANSITIONS) == set(TicketStatus)
    for status, exits in TRANSITIONS.items():
        assert (not exits) == (status in TICKET_TERMINAL_STATUSES), status
    for status in TicketStatus:
        assert status is TicketStatus.WAITING or status_path(status)
    # A recall happens exactly once: nothing leads back to called from recalled.
    assert TicketStatus.CALLED not in TRANSITIONS[TicketStatus.RECALLED]
    # Of 64 pairs, the table allows these 13 and refuses the other 51.
    assert sum(is_legal(a, b) for a, b in _PAIRS) == 13
    # Back to waiting only from called: an undone call (Issue 50), never from further along.
    assert [a for a, b in _PAIRS if b is TicketStatus.WAITING and is_legal(a, b)] == [
        TicketStatus.CALLED
    ]


def test_every_move_is_timestamped(session_factory: sessionmaker[Session]) -> None:
    """Called, started and finished each land on their own column, in Johannesburg time."""
    with session_factory() as db:
        queue = QueueFactory.create(db, site_id=SiteFactory.create(db).id)
        ticket = drive_ticket_to(
            db, TicketFactory.create(db, queue=queue), TicketStatus.DONE
        )
        db.commit()
        assert ticket.called_at and ticket.started_at and ticket.completed_at
        assert ticket.called_at <= ticket.started_at <= ticket.completed_at


def _diagram_edges() -> tuple[set[tuple[str, str]], set[str], set[str]]:
    """``(moves, starts, ends)`` read from the mermaid block between the lifecycle markers."""
    text = _DOC.read_text(encoding="utf-8")
    block = re.search(
        r"<!-- ticket-lifecycle:start -->(.*?)<!-- ticket-lifecycle:end -->", text, re.S
    )
    assert block, (
        "the lifecycle diagram's markers are missing from 03-booking-and-queue.md"
    )
    moves: set[tuple[str, str]] = set()
    starts: set[str] = set()
    ends: set[str] = set()
    for left, right in re.findall(r"^\s*(\S+)\s*-->\s*(\S+)\s*$", block.group(1), re.M):
        if left == "[*]":
            starts.add(right)
        elif right == "[*]":
            ends.add(left)
        else:
            moves.add((left, right))
    return moves, starts, ends


def test_the_documented_state_diagram_matches_the_transition_table() -> None:
    """``docs/PRODUCT/03-booking-and-queue.md`` draws exactly the table: every arrow, no extra one."""
    moves, starts, ends = _diagram_edges()
    table = {(a.value, b.value) for a, exits in TRANSITIONS.items() for b in exits}
    assert moves == table
    assert starts == {TicketStatus.WAITING.value}
    assert ends == {status.value for status in TICKET_TERMINAL_STATUSES}
