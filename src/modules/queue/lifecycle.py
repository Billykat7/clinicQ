"""The ticket lifecycle: the one place a ticket's status changes (Issue 41, non-negotiable 2).

Four consumers derive their behaviour from ``ticket.status``: the board, the notifications, the
reports and the patient's own screen. If any code path wrote it directly they would disagree, and
nobody would notice until a patient was called twice or never. So:

* **The legal moves are one table**, :data:`TRANSITIONS`, over :class:`~src.commons.enums.TicketStatus`
  from Issue 4. Nothing else in the codebase knows which moves are legal.
* **One function applies them**, :func:`transition_ticket`. It locks the ticket's row
  (``SELECT … FOR UPDATE``), refuses a move the table does not allow with
  :class:`IllegalTransitionError` (HTTP 409) and changes nothing when it does, stamps the time the
  move happened, records the visit's wait sample when it reaches ``done`` (Issue 42), writes one
  audit row naming the actor and whether it was a person or the system, and writes the queue
  snapshot through.
* **Nothing else may write the status.** The model refuses an assignment made outside
  :func:`~src.database.models.ticket.status_write_permitted` at runtime, and
  ``tests/unit/queue/test_status_written_only_by_lifecycle.py`` fails the build on one in the source.

The table::

    waiting ──▶ called ──▶ in_progress ──▶ done
       │          │  │            │
       │          │  └─▶ recalled ─┼──▶ in_progress
       │          │        │       └──▶ transferred
       │          ├─▶ no_show ◀────┘ (from recalled too)
       ├─▶ cancelled ◀─ called, recalled
       └─▶ transferred

**Terminal statuses never change** (:data:`~src.commons.enums.TICKET_TERMINAL_STATUSES`: ``done``,
``no_show``, ``cancelled``, ``transferred``). A mistake is corrected with a **new ticket**, never by
reopening one: the old ticket's history stays true, and the patient's new ticket is in the queue in
the order it was issued. A patient whose ticket is terminal may join the same queue again, because
the one-active-ticket rule (Issue 40) counts only tickets still in the day.

**Concurrent moves on one ticket are serialised by the row lock.** Two receptionists pressing
*Call* on the same ticket: the first moves it to ``called``; the second waits for the lock, then
finds ``called → called`` illegal and gets a 409. A caller that knows what it saw passes
``expected_status``, so a move decided on a stale screen is refused as :class:`StaleTransitionError`
(also 409) even when the new move would be legal from the current status.

**Two moves are more than a status change**, and the transitions route refuses them
(:func:`staff_move`, :data:`DEDICATED_MOVES`). A ``transferred`` ticket must have its ticket in the
next queue (Issue 45), and a ``cancelled`` one records the channel it was cancelled through
(Issue 44), so each is made by its own operation, which calls :func:`transition_ticket` itself. The
property tests of Issue 47 found both reachable as bare status changes before this rule.

The diagram in ``docs/PRODUCT/03-booking-and-queue.md`` is checked against :data:`TRANSITIONS` by
``tests/unit/queue/test_ticket_state_machine.py``, so the documentation cannot drift from the code.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import (
    TICKET_TERMINAL_STATUSES,
    ActorKind,
    AuditAction,
    AuditEntityType,
    TicketStatus,
)
from src.commons.exceptions import ConflictError, NotFoundError
from src.commons.time import business_date, now_sast
from src.core.audit import SYSTEM_ACTOR, record_audit_event
from src.database.models.queue import Queue
from src.database.models.ticket import Ticket, status_write_permitted
from src.modules.queue.snapshot import on_queue_changed
from src.modules.queue.tickets import CALL_ORDER
from src.modules.queue.waits import record_visit

_W, _C, _R, _P = (
    TicketStatus.WAITING,
    TicketStatus.CALLED,
    TicketStatus.RECALLED,
    TicketStatus.IN_PROGRESS,
)
_D, _N, _X, _T = (
    TicketStatus.DONE,
    TicketStatus.NO_SHOW,
    TicketStatus.CANCELLED,
    TicketStatus.TRANSFERRED,
)

#: Every legal move, and nothing else. The one definition (see the module docstring).
TRANSITIONS: Final[Mapping[TicketStatus, frozenset[TicketStatus]]] = MappingProxyType(
    {
        # Waiting in the line: called to a room, withdrawn, or moved to another queue.
        _W: frozenset({_C, _X, _T}),
        # Called: they arrive (in progress), do not (recalled once, or a no-show at once when
        # staff say so), or the ticket is withdrawn.
        _C: frozenset({_P, _R, _N, _X}),
        # Recalled once: they arrive after all, are marked a no-show, or the ticket is withdrawn.
        # Never back to called: a recall happens exactly once (Issue 43).
        _R: frozenset({_P, _N, _X}),
        # Being seen: finished, or sent on to the next queue of the visit (Issue 45).
        _P: frozenset({_D, _T}),
        # Terminal: nothing leaves them. A correction is a new ticket.
        **{status: frozenset() for status in TICKET_TERMINAL_STATUSES},
    }
)

#: The code on the wire for a move the table refuses, and for one decided on a stale screen.
ILLEGAL_TRANSITION_CODE: Final = "ticket.transition.illegal"
STALE_TRANSITION_CODE: Final = "ticket.transition.stale"
DEDICATED_MOVE_CODE: Final = "ticket.transition.dedicated_route"
NOBODY_WAITING_CODE: Final = "ticket.call_next.empty"


class IllegalTransitionError(ConflictError):
    """The table does not allow this move from the ticket's current status: HTTP 409."""

    def __init__(self, current: TicketStatus, requested: TicketStatus) -> None:
        verb = (
            "cannot change any more"
            if current in TICKET_TERMINAL_STATUSES
            else "cannot move"
        )
        super().__init__(
            f"A {current.value} ticket {verb}"
            + ("" if current in TICKET_TERMINAL_STATUSES else f" to {requested.value}")
            + ".",
            code=ILLEGAL_TRANSITION_CODE,
        )
        self.current = current
        self.requested = requested


class StaleTransitionError(ConflictError):
    """The ticket changed after the caller looked at it: HTTP 409, and nothing was moved."""

    def __init__(self, current: TicketStatus, expected: TicketStatus) -> None:
        super().__init__(
            f"This ticket is now {current.value}, not {expected.value}. Refresh and try again.",
            code=STALE_TRANSITION_CODE,
        )
        self.current = current
        self.expected = expected


#: Moves the transitions route refuses, and the operation that makes each one whole: a transfer
#: issues the ticket in the next queue, a cancellation records its channel.
DEDICATED_MOVES: Final[Mapping[TicketStatus, str]] = MappingProxyType(
    {TicketStatus.CANCELLED: "Cancel", TicketStatus.TRANSFERRED: "Transfer"}
)


class DedicatedMoveError(ConflictError):
    """A status change asked for on the transitions route that has its own operation: HTTP 409."""

    def __init__(self, requested: TicketStatus) -> None:
        super().__init__(
            f"A ticket is {requested.value} with {DEDICATED_MOVES[requested]}, "
            "not by changing its status.",
            code=DEDICATED_MOVE_CODE,
        )
        self.requested = requested


class NobodyWaitingError(ConflictError):
    """Call next found no waiting ticket in the queue today: HTTP 409."""

    def __init__(self) -> None:
        super().__init__("Nobody is waiting in this queue.", code=NOBODY_WAITING_CODE)


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is moving a ticket: a person with a name and a role, or the system.

    ``label`` is what the audit row's ``actor`` says (an email, ``patient:<id>``, ``system``);
    ``kind`` becomes its ``actor_role``, so the trail distinguishes a timer from a receptionist
    without parsing names.
    """

    kind: ActorKind
    label: str
    user_id: str | None = None

    @classmethod
    def system(cls) -> Actor:
        """A scheduled job. Which job is said in the move's ``note``; the actor is the system."""
        return cls(kind=ActorKind.SYSTEM, label=SYSTEM_ACTOR)


def is_legal(current: TicketStatus, requested: TicketStatus) -> bool:
    """Whether :data:`TRANSITIONS` allows ``current → requested``."""
    return requested in TRANSITIONS[current]


def lock_ticket(db: Session, ticket_id: str) -> Ticket:
    """The ticket's row, locked for this transaction and re-read fresh: for a rule that must look at
    the current status before deciding which move to ask for (Issue 44's cancellation window).

    Moving it still takes :func:`transition_ticket`, which re-locks the same row in the same
    transaction at no cost.

    Raises:
        NotFoundError: No such ticket.
    """
    return _locked(db, ticket_id)


def _locked(db: Session, ticket_id: str) -> Ticket:
    """The ticket's row, locked for this transaction and re-read from the database.

    ``populate_existing`` matters: a ticket already in the session could hold a status another
    transaction has since changed, and deciding on that would defeat the lock.
    """
    ticket = db.execute(
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if ticket is None:
        raise NotFoundError("No such ticket.", code="http.not_found")
    return ticket


def _stamp(ticket: Ticket, requested: TicketStatus, moment: datetime) -> None:
    """Record when the move happened on the column that moment belongs to."""
    if requested is TicketStatus.CALLED and ticket.called_at is None:
        ticket.called_at = moment
    elif requested is TicketStatus.RECALLED:
        ticket.recalled_at = moment
    elif requested is TicketStatus.IN_PROGRESS:
        ticket.started_at = moment
    if requested in TICKET_TERMINAL_STATUSES:
        ticket.completed_at = moment


def transition_ticket(
    db: Session,
    ticket_id: str,
    requested: TicketStatus,
    *,
    actor: Actor,
    expected_status: TicketStatus | None = None,
    note: str | None = None,
    moment: datetime | None = None,
) -> Ticket:
    """Move one ticket to ``requested``, or refuse and change nothing. The caller commits.

    Args:
        db: The session. The lock is held until the caller's transaction ends.
        ticket_id: The ticket; the caller has already checked it belongs to its clinic.
        requested: Where to move it.
        actor: Who is moving it, for the audit row.
        expected_status: The status the caller last saw. When given and the ticket has moved on
            since, the move is refused as stale.
        note: Why, in a few words, for the audit context (``recall timer``, ``patient cancelled``).
        moment: When the move happens (aware); ``None`` means now in Johannesburg.

    Returns:
        The moved ticket, flushed.

    Raises:
        NotFoundError: No such ticket.
        StaleTransitionError: ``expected_status`` no longer matches.
        IllegalTransitionError: :data:`TRANSITIONS` does not allow the move.
    """
    ticket = _locked(db, ticket_id)
    current = ticket.status_enum
    if expected_status is not None and current is not expected_status:
        raise StaleTransitionError(current, expected_status)
    if not is_legal(current, requested):
        raise IllegalTransitionError(current, requested)

    moment = moment or now_sast()
    with status_write_permitted():
        ticket.status = requested.value
    _stamp(ticket, requested, moment)
    if requested is TicketStatus.DONE:
        # The visit's sample commits with the move, so the next estimate already includes it.
        record_visit(db, ticket, moment=moment)
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.TICKET,
        entity_id=ticket.id,
        actor=actor.label,
        actor_id=actor.user_id,
        actor_role=actor.kind.value,
        site_id=ticket.site_id,
        diff={"status": {"before": current.value, "after": requested.value}},
        context=f"{ticket.number}: {current.value} → {requested.value}"
        + (f" ({note})" if note else ""),
    )
    db.flush()
    queue = db.get(Queue, ticket.queue_id)
    if queue is not None:
        on_queue_changed(db, queue)
    return ticket


def staff_move(
    db: Session,
    ticket_id: str,
    requested: TicketStatus,
    *,
    actor: Actor,
    expected_status: TicketStatus | None = None,
    moment: datetime | None = None,
) -> Ticket:
    """A status change a staff member asks for on the transitions route. The caller commits.

    :func:`transition_ticket`, except for the moves in :data:`DEDICATED_MOVES`, which are refused
    before the ticket is read: they are made by :func:`~src.modules.queue.transfer.transfer_ticket`
    and :func:`~src.modules.queue.cancellation.cancel_ticket`.

    Raises:
        DedicatedMoveError: ``requested`` is ``cancelled`` or ``transferred``.
        NotFoundError, StaleTransitionError, IllegalTransitionError: As :func:`transition_ticket`.
    """
    if requested in DEDICATED_MOVES:
        raise DedicatedMoveError(requested)
    return transition_ticket(
        db,
        ticket_id,
        requested,
        actor=actor,
        expected_status=expected_status,
        moment=moment,
    )


def call_next(
    db: Session, queue: Queue, *, actor: Actor, moment: datetime | None = None
) -> Ticket:
    """Call the next waiting ticket in ``queue`` today, in call order. The caller commits.

    The candidate is taken with ``FOR UPDATE SKIP LOCKED``: two staff pressing *Call next* at the
    same instant lock **different** rows, so they call two different patients instead of both
    calling one and one of them getting a 409. The move itself still goes through
    :func:`transition_ticket`.

    Raises:
        NobodyWaitingError: Nobody is waiting in this queue today.
    """
    moment = moment or now_sast()
    candidate = db.execute(
        select(Ticket.id)
        .where(
            Ticket.queue_id == queue.id,
            Ticket.service_day == business_date(moment),
            Ticket.status == TicketStatus.WAITING.value,
        )
        .order_by(*CALL_ORDER)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if candidate is None:
        raise NobodyWaitingError
    return transition_ticket(
        db,
        candidate,
        TicketStatus.CALLED,
        actor=actor,
        expected_status=TicketStatus.WAITING,
        note="call next",
        moment=moment,
    )
