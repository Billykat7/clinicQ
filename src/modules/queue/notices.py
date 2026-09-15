"""What the queue tells a patient, and when (Issue 63). The queue's only door to notifications.

The queue engine decides that something happened to a ticket; it does not decide how the patient
hears about it. Every message goes through one call, :func:`src.modules.notifications.service.notify`,
with an event name and the words the message needs. No transport, provider, template or retry is
visible from here, and nothing here can fail a queue move: the ledger row is written in the move's
transaction and delivered only after it commits.

Each message carries a **dedupe key** built from the ticket and the event, so a replayed move (a
sweep that runs twice, a retried request) records one message, never two:

* ``called``: the call's own moment is part of the key, because an undone call that is made again is
  a second call the patient should hear about;
* ``next``, ``recalled``, ``no_show``, ``transferred`` and ``cancelled`` happen once per ticket.
"""

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import PatientEvent, TicketStatus
from src.database.models.queue import Queue
from src.database.models.site import Site
from src.database.models.ticket import Ticket
from src.modules.notifications import service as notifications
from src.modules.queue.ticket_page import page_url_for
from src.modules.queue.tickets import CALL_ORDER


def tell(
    db: Session,
    ticket: Ticket,
    event: PatientEvent,
    *,
    moment: datetime,
    occurrence: str | None = None,
    **details: object,
) -> None:
    """Tell ``ticket``'s patient about ``event``. A walk-in with no patient is told at the desk.

    Args:
        db: The move's session; the message is recorded in its transaction.
        ticket: The ticket the event is about.
        event: What happened.
        moment: When it happened (aware).
        occurrence: What tells two occurrences of the same event apart, when one ticket can have
            more than one (a call made again after an undo); ``None`` for a once-per-ticket event.
        details: Words the message needs beyond the ticket's number, clinic, queue and room
            (``minutes`` for a recall, ``wait`` for a transfer).
    """
    if ticket.patient_id is None:
        return
    queue = db.get(Queue, ticket.queue_id)
    site = db.get(Site, ticket.site_id)
    if queue is None or site is None:
        return
    key = f"{ticket.id}:{event.value}" + (f":{occurrence}" if occurrence else "")
    notifications.notify(
        db,
        patient_id=ticket.patient_id,
        event=event,
        context={
            "number": ticket.number,
            "clinic": site.name,
            "queue": queue.name,
            "room": queue.room_label,
            # Where tapping a push opens (Issue 64): the ticket's own page, never an id.
            "page_url": page_url_for(ticket),
            **details,
        },
        site_id=ticket.site_id,
        dedupe_key=key,
        now=moment,
    )


def tell_next_in_line(
    db: Session, queue_id: str, service_day: date, *, moment: datetime
) -> None:
    """Tell whoever is now first in the waiting line of ``queue_id`` that they are next.

    Called whenever a waiting ticket leaves the line (called, cancelled, transferred). The first
    waiting ticket is told once: the dedupe key makes a second look at an unchanged line a no-op.
    """
    head = db.execute(
        select(Ticket)
        .where(
            Ticket.queue_id == queue_id,
            Ticket.service_day == service_day,
            Ticket.status == TicketStatus.WAITING.value,
        )
        .order_by(*CALL_ORDER)
        .limit(1)
    ).scalar_one_or_none()
    if head is not None:
        tell(db, head, PatientEvent.NEXT, moment=moment)
