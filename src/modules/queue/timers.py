"""Recall and no-show timers: a called patient who does not arrive (Issue 43).

A called patient who is in the bathroom should not lose their place, and one who left an hour ago
should not hold a room up. So a called ticket is given a timeout to arrive:

1. **Not attended within the timeout**: it is **recalled**, exactly once, and the patient is told
   they have been called again and how long they have.
2. **Still not attended within the timeout again**: it becomes a **no-show**, which frees the room
   (nothing is ``called`` or ``recalled`` there any more, so *Call next* moves on), and the patient
   is told the ticket was marked missed and how to join again.

**Open decision 1, settled here: no ``arq``.** The spec said ``arq`` workers; the kernel already runs
its scheduled jobs on APScheduler under a PostgreSQL advisory lock (:mod:`src.core.scheduler`), which
already gives the two properties this needs. The job is :func:`run_recall_timers`, registered as
``run_recall_timer_sweep`` every ``QUEUE_RECALL_SWEEP_SECONDS``.

* **It survives a restart**: there is no timer in memory to lose. A deadline is ``called_at`` (or
  ``recalled_at``) plus the timeout, both in the database, so a sweep in a process that started a
  second ago sees exactly what the last one saw.
* **It never double-fires**: the advisory lock lets one instance sweep at a time; each candidate row
  is locked (``FOR UPDATE SKIP LOCKED``, so a ticket a receptionist is moving is left for the next
  sweep rather than waited on); and the move goes through
  :func:`~src.modules.queue.lifecycle.transition_ticket` with ``expected_status``, whose table has no
  way back from ``recalled`` to ``called``. A second sweep over the same instant finds nothing due.

Every automatic move is made by :meth:`Actor.system() <src.modules.queue.lifecycle.Actor.system>`,
so the audit trail records ``actor = system`` and ``actor_role = system`` with the timer named in the
context: a no-show marked by this job and one marked by a receptionist are different facts. Staff
can recall or mark a no-show at once through the transition route (Issue 41); the timer then starts
the no-show clock from the staff member's recall.

**The timeout** is the queue's ``recall_timeout_minutes``, else the clinic's, else
``QUEUE_RECALL_TIMEOUT_MINUTES`` (5).

**The message** is recorded in the notification ledger and sent through the configured SMS provider
(the logging provider until M9 brings real delivery). It is gated by the patient's notification
consent like every patient message (Issue 21), so a patient who has not agreed gets a
``suppressed`` ledger row, not an SMS. A walk-in with no phone number has nobody to text: the board
and the desk call them.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import NotificationTemplate, TicketStatus
from src.commons.exceptions import ConflictError
from src.commons.time import now_sast, stored_sast
from src.core.config import Settings, get_settings
from src.database.models.patient import Patient
from src.database.models.queue import Queue
from src.database.models.site import Site
from src.database.models.ticket import Ticket
from src.modules.notifications import service as notifications
from src.modules.notifications.sms import SmsProvider
from src.modules.queue.lifecycle import Actor, transition_ticket

#: What each timed status becomes when its timeout passes, and the message that goes with it.
_NEXT: dict[TicketStatus, tuple[TicketStatus, NotificationTemplate, str]] = {
    TicketStatus.CALLED: (
        TicketStatus.RECALLED,
        NotificationTemplate.TICKET_RECALLED,
        "recall timer",
    ),
    TicketStatus.RECALLED: (
        TicketStatus.NO_SHOW,
        NotificationTemplate.TICKET_NO_SHOW,
        "no-show timer",
    ),
}


@dataclass(frozen=True, slots=True)
class RecallSweep:
    """What one sweep moved: ticket ids recalled, and ticket ids marked no-show."""

    recalled: list[str] = field(default_factory=list)
    no_shows: list[str] = field(default_factory=list)

    @property
    def moved(self) -> int:
        """How many tickets this sweep moved."""
        return len(self.recalled) + len(self.no_shows)


def timeout_minutes(
    queue_minutes: int | None, site_minutes: int | None, default_minutes: int
) -> int:
    """The timeout that applies: the queue's own, else the clinic's, else the platform default."""
    if queue_minutes is not None:
        return queue_minutes
    if site_minutes is not None:
        return site_minutes
    return default_minutes


@dataclass(frozen=True, slots=True)
class _Due:
    ticket_id: str
    current: TicketStatus
    minutes: int


def _due(db: Session, moment: datetime, default_minutes: int) -> Sequence[_Due]:
    """Called and recalled tickets whose timeout has passed at ``moment``, their rows locked.

    Few rows by nature (one per room at a time), so the per-queue timeout is resolved in Python
    rather than in dialect-specific interval arithmetic. ``SKIP LOCKED`` leaves a ticket that staff
    are moving right now for the next sweep.
    """
    rows = db.execute(
        select(
            Ticket.id,
            Ticket.status,
            Ticket.called_at,
            Ticket.recalled_at,
            Queue.recall_timeout_minutes,
            Site.recall_timeout_minutes,
        )
        .join(Queue, Queue.id == Ticket.queue_id)
        .join(Site, Site.id == Ticket.site_id)
        .where(
            Ticket.status.in_([TicketStatus.CALLED.value, TicketStatus.RECALLED.value]),
            Ticket.service_day >= (moment - timedelta(days=1)).date(),
        )
        .order_by(Ticket.called_at)
        .with_for_update(skip_locked=True, of=Ticket)
    ).all()
    due: list[_Due] = []
    for ticket_id, status, called_at, recalled_at, queue_minutes, site_minutes in rows:
        minutes = timeout_minutes(queue_minutes, site_minutes, default_minutes)
        current = TicketStatus(status)
        started = recalled_at if current is TicketStatus.RECALLED else called_at
        if (
            started is not None
            and stored_sast(started) + timedelta(minutes=minutes) <= moment
        ):
            due.append(_Due(ticket_id, current, minutes))
    return due


def _notify(
    db: Session,
    ticket: Ticket,
    template: NotificationTemplate,
    minutes: int,
    provider: SmsProvider | None,
    moment: datetime,
) -> None:
    """Tell the patient, through the notification service (ledger, consent, provider)."""
    if ticket.patient_id is None:
        return  # a walk-in with no number: the board and the desk call them
    patient = db.get(Patient, ticket.patient_id)
    queue = db.get(Queue, ticket.queue_id)
    site = db.get(Site, ticket.site_id)
    if patient is None or queue is None or site is None:
        return
    notifications.send_sms(
        db,
        to=patient.phone_e164,
        template=template,
        context={
            "number": ticket.number,
            "clinic": site.name,
            "queue": queue.name,
            "room": queue.room_label,
            "minutes": minutes,
        },
        provider=provider,
        now=moment,
    )


def run_recall_timers(
    db: Session,
    *,
    moment: datetime | None = None,
    settings: Settings | None = None,
    sms_provider: SmsProvider | None = None,
) -> RecallSweep:
    """Recall or mark a no-show every called ticket whose timeout has passed. The caller commits.

    Each move is its own savepoint: a ticket that staff moved first (a stale or illegal move, 409)
    is skipped and the rest of the sweep carries on.

    Args:
        db: The session.
        moment: When the sweep runs (aware); ``None`` means now in Johannesburg. Tests pass a
            moment to advance the clock.
        settings: Settings; ``None`` reads the application's.
        sms_provider: The SMS provider; ``None`` builds the configured one.

    Returns:
        The tickets recalled and the tickets marked no-show.
    """
    cfg = settings or get_settings()
    moment = moment or now_sast()
    swept = RecallSweep()
    for due in _due(db, moment, cfg.queue_recall_timeout_minutes):
        after, template, job = _NEXT[due.current]
        try:
            with db.begin_nested():
                ticket = transition_ticket(
                    db,
                    due.ticket_id,
                    after,
                    actor=Actor.system(),
                    expected_status=due.current,
                    note=f"{job}: not attended within {due.minutes} min",
                    moment=moment,
                )
        except ConflictError:
            continue  # staff moved it first; nothing is due any more
        (swept.recalled if after is TicketStatus.RECALLED else swept.no_shows).append(
            ticket.id
        )
        _notify(db, ticket, template, due.minutes, sms_provider, moment)
    return swept
