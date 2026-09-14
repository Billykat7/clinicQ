"""How long each queue is right now, as every patient-facing surface reads it (Issue 31).

Discovery (Issue 31), the clinic detail page (Issue 35) and the channel menus (M10) all show a live
queue length, and they must all show the **same** one. This module is the single read they go
through, so there is one answer to "how many people are waiting in the pharmacy queue at Zola".

**The count is of tickets, for the Johannesburg service day** (Issue 39). :func:`read_waiting_counts`
answers, in one grouped query, how many tickets in each queue are ``waiting`` today. A queue with no
ticket today is a measured ``0``, not "not measured": the ticket table is the queue, so an empty
result is a fact. ``None`` is still what a surface shows when a reader could not count at all (a
reader that failed, or a queue a reader was not asked about), and it still renders as "not
reported yet", never as zero.

**The wait is always a range** (Issue 42). The direct read estimates, for somebody joining now, how
long they would wait (:mod:`src.modules.queue.estimate`), and :attr:`LiveQueue.wait` carries a
:class:`~src.modules.queue.estimate.WaitEstimate`: a :class:`~src.modules.queue.estimate.WaitRange`
that cannot hold a single number, a confidence, and whether it is approximate. A wait promised as
"12 minutes" is a promise nobody can keep.

**The reader is a parameter, not a global.** :func:`published_live_queues` takes the counting
function as an argument, which is the seam later issues plug into without touching a caller: Issue
36's snapshot cache (:func:`src.modules.queue.snapshot.cached_waiting_counts`, what discovery reads
by default) wraps this direct read, and the tests hand in a reader with real numbers to prove that
a count travels intact from here to the API. A reader answers :class:`QueueReading`, the count and
when it was taken, so a patient can be told how old a figure is.
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.commons.enums import QueueKind, TicketStatus
from src.commons.time import business_date, now_sast
from src.core.site_scope import published_select
from src.database.models.queue import Queue
from src.database.models.ticket import Ticket
from src.modules.queue.estimate import WaitEstimate
from src.modules.queue.waits import estimates_for


@dataclass(frozen=True, slots=True)
class QueueReading:
    """One queue's length as a reader reports it, and when it was counted.

    ``waiting`` is ``None`` when the length is not measured. ``as_of`` is when the count was taken
    (Africa/Johannesburg): the moment of the read for a direct count, the snapshot's timestamp for a
    cached one (Issue 36), so a surface can say how old a figure is. ``None`` when nothing was
    counted.
    """

    waiting: int | None
    as_of: datetime | None = None
    #: The wait for somebody joining now (Issue 42). ``None`` when nothing was estimated.
    wait: WaitEstimate | None = None


#: The reading that means "nobody counted".
NOT_MEASURED = QueueReading(waiting=None, as_of=None)

#: Given the queues asked about, return ``{queue id: reading}``. A reader must answer for every
#: queue it is given; a queue missing from the answer is treated as not measured.
WaitingCountReader = Callable[[Session, Collection[Queue]], Mapping[str, QueueReading]]


@dataclass(frozen=True, slots=True)
class LiveQueue:
    """One queue at a publicly listed clinic, with its current length.

    Plain data, never markup, so the web page, the USSD menu and the WhatsApp bot render it their
    own way. ``waiting`` is ``None`` when the length is not measured, which a surface must show as
    such rather than as zero.
    """

    queue_id: str
    site_id: str
    name: str
    kind: QueueKind
    display_order: int
    allows_remote_join: bool
    waiting: int | None
    #: The expected wait for somebody joining now, always a range (Issue 42). ``None`` when the
    #: reader did not estimate it: show the length alone, never a guess.
    wait: WaitEstimate | None = None
    #: When ``waiting`` was counted, so a surface can show its age in seconds (Issue 36).
    as_of: datetime | None = None


def read_waiting_counts(
    db: Session, queues: Collection[Queue]
) -> Mapping[str, QueueReading]:
    """The direct read of each queue's length and wait: today's ``waiting`` tickets, and the estimate.

    One grouped ``COUNT`` whatever the number of queues, on the board index
    (``ix_clinicq_ticket_board`` leads with ``queue_id, service_day, status``). The signature is the
    contract every reader keeps: it takes the queues rather than their ids, so a page of twenty
    clinics is still one query. Only ``waiting`` counts: a called or recalled patient has left the
    line for a room, and counting them would tell the next patient the wait is longer than it is.

    Args:
        db: The session.
        queues: The queues to count, already narrowed by the caller (the published directory or
            the reconciliation sweep).

    Returns:
        ``{queue id: reading}`` for each queue, ``0`` for a queue with nobody waiting, each dated
        with the moment of the count.
    """
    ids = [queue.id for queue in queues]
    if not ids:
        return {}
    counted_at = now_sast()
    rows = db.execute(
        select(Ticket.queue_id, func.count())
        .where(
            Ticket.queue_id.in_(ids),
            Ticket.service_day == business_date(counted_at),
            Ticket.status == TicketStatus.WAITING.value,
        )
        .group_by(Ticket.queue_id)
    ).all()
    counts = {queue_id: int(count) for queue_id, count in rows}
    waiting = {queue_id: counts.get(queue_id, 0) for queue_id in ids}
    # Somebody joining now has everyone already waiting ahead of them.
    estimates = estimates_for(db, queues, waiting, moment=counted_at)
    return {
        queue_id: QueueReading(
            waiting=waiting[queue_id],
            as_of=counted_at,
            wait=estimates.get(queue_id),
        )
        for queue_id in ids
    }


def published_live_queues(
    db: Session,
    site_ids: Collection[str],
    *,
    reader: WaitingCountReader = read_waiting_counts,
) -> dict[str, tuple[LiveQueue, ...]]:
    """Each publicly visible clinic's active queues, in display order, with their lengths.

    Two reads whatever the number of clinics: the queue rows, through
    :func:`~src.core.site_scope.published_select` (so a clinic a patient may not see contributes
    nothing), and one call to ``reader`` for all of them together.

    Args:
        db: The session.
        site_ids: The clinics to read; typically one page of search results.
        reader: Where the counts come from. The direct read by default; Issue 36's snapshot cache
            passes its own.

    Returns:
        ``{site_id: queues}``. A visible clinic with no active queue maps to an empty tuple only if
        it appears in the rows; callers use ``.get(site_id, ())``.
    """
    if not site_ids:
        return {}
    rows = list(
        db.execute(
            published_select(Queue, site_ids)
            .where(Queue.is_deleted.is_(False), Queue.is_active.is_(True))
            .order_by(Queue.site_id, Queue.display_order, Queue.name)
        ).scalars()
    )
    readings = reader(db, rows)
    by_site: dict[str, list[LiveQueue]] = {}
    for queue in rows:
        reading = readings.get(queue.id, NOT_MEASURED)
        by_site.setdefault(queue.site_id, []).append(
            LiveQueue(
                queue_id=queue.id,
                site_id=queue.site_id,
                name=queue.name,
                kind=queue.kind_enum,
                display_order=queue.display_order,
                allows_remote_join=queue.allows_remote_join,
                waiting=reading.waiting,
                as_of=reading.as_of,
                wait=reading.wait,
            )
        )
    return {site_id: tuple(queues) for site_id, queues in by_site.items()}


def total_waiting(queues: Collection[LiveQueue]) -> int | None:
    """The clinic-wide length a list shows: the sum, or ``None`` if any queue is not measured.

    A partial sum is refused on purpose. "3 waiting" when the doctor queue was simply not counted
    is exactly the misleading figure this module exists to keep off a patient's screen.
    """
    if not queues or any(queue.waiting is None for queue in queues):
        return None
    return sum(queue.waiting for queue in queues if queue.waiting is not None)
