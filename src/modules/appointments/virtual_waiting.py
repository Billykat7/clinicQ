"""The virtual waiting room: telling a travelling patient when to leave, and hearing "on my way" (Issue 86).

Three things, each small, and each keeping a promise the issue makes:

* :func:`check_ticket` decides, for one waiting ticket, whether it is time to leave, from
  :func:`src.modules.queue.waits.ticket_wait` (the estimate the ticket page shows) and
  :func:`~src.modules.appointments.call_forward.call_forward`. When it is, the alert is claimed on the
  ticket's locked row (``leave_alert_at``) and sent through the notification service (Issue 63) as
  :attr:`~src.commons.enums.PatientEvent.LEAVE_NOW`, carrying that same estimate's label. **One alert per
  ticket**: the claim and the notification's dedupe key both say so.
* :func:`run_call_forward` is the sweep's body, over every waiting, travelling, not yet alerted ticket at
  a clinic with the feature on.
* :func:`acknowledge` records the patient's "On my way" and wakes the reception dashboard.

**An alert never costs a place.** Nothing here moves a ticket: no status is written, no order changes. A
patient who ignores the alert keeps their place until staff call them, and from then the recall rule
(Issue 43) applies exactly as it does to everybody else.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import PatientEvent, TicketStatus
from src.commons.exceptions import ConflictError, NotFoundError
from src.commons.time import business_date, now_sast
from src.database.models.queue import Queue
from src.database.models.site import Site
from src.database.models.ticket import Ticket
from src.modules.appointments.call_forward import (
    ON_THE_WAY_STATUSES,
    CallForward,
    call_forward,
)
from src.modules.queue import notices
from src.modules.queue.snapshot import on_queue_changed
from src.modules.queue.waits import ticket_wait

#: The refusal codes "On my way" answers with.
NOT_OFFERED_CODE: Final = "ticket.on_my_way.not_offered"
ENDED_CODE: Final = "ticket.on_my_way.ended"


class OnMyWayRefusedError(ConflictError):
    """ "On my way" does not apply to this ticket: HTTP 409."""


@dataclass(frozen=True, slots=True)
class Checked:
    """What :func:`check_ticket` found and did for one ticket."""

    plan: CallForward | None
    #: Whether this call sent the alert.
    alerted: bool


def check_ticket(
    db: Session, ticket: Ticket, *, moment: datetime | None = None
) -> Checked:
    """Tell ``ticket``'s patient it is time to leave, if it is and they have not been told. The caller commits.

    A no-op for a ticket that is not waiting, has no trip, was already alerted, or is at a clinic whose
    virtual waiting room is off.
    """
    moment = moment or now_sast()
    site = db.get(Site, ticket.site_id)
    if (
        site is None
        or not site.virtual_waiting_enabled
        or ticket.status_enum is not TicketStatus.WAITING
        or not ticket.travel_minutes
    ):
        return Checked(plan=None, alerted=False)
    queue = db.get(Queue, ticket.queue_id)
    if queue is None:
        return Checked(plan=None, alerted=False)
    _, estimate = ticket_wait(db, ticket, queue, moment=moment)
    plan = call_forward(estimate, ticket.travel_minutes, moment)
    if plan is None or not plan.due or ticket.leave_alert_at is not None:
        return Checked(plan=plan, alerted=False)
    # Claim the alert on the locked row, so two sweeps (or a sweep and a join) cannot both send it.
    locked = db.execute(
        select(Ticket)
        .where(Ticket.id == ticket.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if (
        locked.leave_alert_at is not None
        or locked.status_enum is not TicketStatus.WAITING
    ):
        return Checked(plan=plan, alerted=False)
    locked.leave_alert_at = moment
    db.flush()
    notices.tell(
        db,
        ticket,
        PatientEvent.LEAVE_NOW,
        moment=moment,
        wait=estimate.label,
        minutes=ticket.travel_minutes,
    )
    return Checked(plan=plan, alerted=True)


def _awaiting_alert(db: Session, moment: datetime) -> Sequence[Ticket]:
    """Today's waiting, travelling, not yet alerted tickets at clinics with the feature on, in call order."""
    return (
        db.execute(
            select(Ticket)
            .join(Site, Site.id == Ticket.site_id)
            .where(
                Site.virtual_waiting_enabled.is_(True),
                Ticket.service_day == business_date(moment),
                Ticket.status == TicketStatus.WAITING.value,
                Ticket.travel_minutes > 0,
                Ticket.leave_alert_at.is_(None),
            )
            .order_by(Ticket.queue_id, Ticket.order_key, Ticket.sequence)
        )
        .scalars()
        .all()
    )


def run_call_forward(db: Session, *, moment: datetime | None = None) -> int:
    """Check every ticket that may need telling to leave; return how many were told. The caller commits.

    The body of :func:`src.core.scheduler.run_call_forward_sweep`. Each ticket is checked in a savepoint,
    so one ticket's failure does not undo another's alert.
    """
    moment = moment or now_sast()
    told = 0
    for ticket in _awaiting_alert(db, moment):
        with db.begin_nested():
            told += check_ticket(db, ticket, moment=moment).alerted
    return told


def acknowledge(
    db: Session, patient_id: str, ticket_id: str, *, moment: datetime | None = None
) -> tuple[Ticket, bool]:
    """Record that the patient is on their way; the reception dashboard shows it. The caller audits and commits.

    Idempotent: a second tap keeps the first time. Another patient's ticket, or one that does not exist,
    is the same "not found". Returns the ticket and whether this call recorded it.

    Raises:
        NotFoundError: Not this patient's ticket.
        OnMyWayRefusedError: The clinic has no virtual waiting room, or the visit has begun or ended.
    """
    moment = moment or now_sast()
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.patient_id != patient_id:
        raise NotFoundError("No such ticket.", code="http.not_found")
    site = db.get(Site, ticket.site_id)
    if (
        site is None
        or not site.virtual_waiting_enabled
        or ticket.travel_minutes is None
    ):
        raise OnMyWayRefusedError(
            "This clinic is not waiting for patients to travel in.",
            code=NOT_OFFERED_CODE,
        )
    if ticket.status_enum not in ON_THE_WAY_STATUSES:
        raise OnMyWayRefusedError(
            "This ticket is no longer in the queue.", code=ENDED_CODE
        )
    if ticket.on_my_way_at is not None:
        return ticket, False
    ticket.on_my_way_at = moment
    db.flush()
    queue = db.get(Queue, ticket.queue_id)
    if queue is not None:
        on_queue_changed(db, queue)
    return ticket, True
