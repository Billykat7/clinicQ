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

from sqlalchemy import Select, select

from src.commons.enums import TICKET_ACTIVE_STATUSES, TicketStatus
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models.ticket import Ticket


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
        .order_by(Ticket.sequence)
    )


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
