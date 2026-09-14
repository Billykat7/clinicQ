"""Reading tickets: the board's query and a patient's own tickets (Issue 39).

Two reads the rest of M6 and every surface after it are built on, written once so their shape (and
the index each one runs on) is decided here rather than rediscovered by each caller:

* :func:`board_select` is **one queue's tickets on one service day, in sequence order**, through
  the site guard (:func:`~src.core.site_scope.scoped_select`). It leads with the columns of
  ``ix_clinicq_ticket_board`` (``queue_id, service_day, status, sequence``), and
  ``tests/integration/queue/test_ticket_indexes.py`` asserts with ``EXPLAIN`` that PostgreSQL
  answers it from that index rather than by scanning the day's tickets.
* :func:`patient_tickets_select` is **a patient's own tickets on a day**, on the partial index
  ``ix_clinicq_ticket_patient``. It is scoped by who the patient is, not by a clinic: a patient
  reads their own tickets wherever they joined, the way they read their own consent.

Neither orders by anything but the sequence. Priority and transfers (Issues 45 and 46) change the
order a queue is *called* in, and they change it in one ordering function, not here.
"""

from collections.abc import Collection
from datetime import date
from typing import Final

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.orm import Session

from src.commons.enums import TICKET_ACTIVE_STATUSES, TicketStatus
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models.ticket import Ticket

#: The order a queue is called in, and so the order positions are counted in: by ``order_key``, which
#: is the sequence (arrival order across every channel, non-negotiable 1) unless a transfer placed the
#: ticket by its visit's arrival (Issue 45) or a priority override moved it (Issue 46); the sequence
#: breaks a tie. One definition, so the board, call next and a patient's position cannot disagree.
CALL_ORDER: Final = (Ticket.order_key.asc(), Ticket.sequence.asc())


def board_select(
    access: SiteAccess,
    queue_id: str,
    service_day: date,
    statuses: Collection[TicketStatus] = TICKET_ACTIVE_STATUSES,
) -> Select[tuple[Ticket]]:
    """One queue's tickets on a service day, in the given statuses, in sequence order.

    Args:
        access: The clinic, from the site guard; a queue at another clinic reads as empty.
        queue_id: The queue.
        service_day: The Johannesburg calendar date.
        statuses: Which tickets; by default every one still in the day (not terminal).

    Returns:
        The statement; the caller executes it.
    """
    return (
        scoped_select(Ticket, access)
        .where(
            Ticket.queue_id == queue_id,
            Ticket.service_day == service_day,
            Ticket.status.in_(sorted(status.value for status in statuses)),
        )
        .order_by(*CALL_ORDER)
    )


def site_day_select(
    access: SiteAccess,
    service_day: date,
    *,
    queue_id: str | None = None,
    statuses: Collection[TicketStatus] = TICKET_ACTIVE_STATUSES,
) -> Select[tuple[Ticket]]:
    """A clinic's tickets on a service day across its queues (or one), queue by queue in sequence.

    The front desk's read: every line at once. Through the site guard, like :func:`board_select`.
    """
    statement = scoped_select(Ticket, access).where(
        Ticket.service_day == service_day,
        Ticket.status.in_(sorted(status.value for status in statuses)),
    )
    if queue_id is not None:
        statement = statement.where(Ticket.queue_id == queue_id)
    return statement.order_by(Ticket.queue_id, Ticket.sequence)


def patient_tickets_select(patient_id: str, service_day: date) -> Select[tuple[Ticket]]:
    """A patient's own tickets on a service day, at any clinic, earliest first.

    Scoped by the patient rather than by a site (see the module docstring): only a route that has
    authenticated this patient may call it.
    """
    return (
        select(Ticket)
        .where(Ticket.patient_id == patient_id, Ticket.service_day == service_day)
        .order_by(Ticket.joined_at)
    )


def waiting_ahead(db: Session, ticket: Ticket) -> int:
    """How many ``waiting`` tickets are ahead of ``ticket`` in its queue and service day.

    Derived from the order every time it is asked, never stored on a ticket, so a cancellation or a
    call ahead is reflected on the next read without rewriting anyone else's row.
    """
    return int(
        db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.queue_id == ticket.queue_id,
                Ticket.service_day == ticket.service_day,
                Ticket.status == TicketStatus.WAITING.value,
                ahead_of(ticket),
            )
        ).scalar_one()
    )


def ahead_of(ticket: Ticket) -> ColumnElement[bool]:
    """The condition "called before ``ticket``" in :data:`CALL_ORDER`, for counting a position."""
    return or_(
        Ticket.order_key < ticket.order_key,
        and_(Ticket.order_key == ticket.order_key, Ticket.sequence < ticket.sequence),
    )


def behind(ticket: Ticket) -> ColumnElement[bool]:
    """The condition "called after ``ticket``" in :data:`CALL_ORDER`."""
    return or_(
        Ticket.order_key > ticket.order_key,
        and_(Ticket.order_key == ticket.order_key, Ticket.sequence > ticket.sequence),
    )
