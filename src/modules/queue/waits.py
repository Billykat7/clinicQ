"""Where the wait estimator's samples come from, and where its estimates are read (Issue 42).

:mod:`src.modules.queue.estimate` is a pure function; this module is its database side:

* :func:`record_visit` writes one ``wait_time_sample`` when a ticket reaches ``done``. The lifecycle
  calls it inside the same transaction as the move (Issue 41), so the sample and the ``done`` commit
  together, and the **next** estimate read anywhere already includes the visit: the estimate updates
  as tickets complete, with no job in between.
* :func:`recent_samples` reads the most recent N samples of many queues in **one** query (a window
  function per queue), newest first.
* :func:`estimates_for` turns those samples, each queue's expected minutes and how many people are
  ahead into a :class:`~src.modules.queue.estimate.WaitEstimate` per queue. Discovery's live read and
  the queue snapshot use it for somebody joining now; the join response uses it for the ticket just
  issued.

The call-interval rule is written once, in :func:`_interval_minutes`, and the fixture replay
(``scripts/evaluate_wait_estimator.py``) mirrors it.
"""

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.commons.time import now_sast, stored_sast
from src.database.models.queue import Queue
from src.database.models.ticket import Ticket
from src.database.models.wait_time_sample import WaitTimeSample
from src.modules.queue.estimate import (
    DEFAULT_SETTINGS,
    EstimatorSettings,
    VisitSample,
    WaitEstimate,
    estimate_wait,
)
from src.modules.queue.tickets import waiting_ahead

#: A call interval longer than this is not throughput: the room was closed, or the day stopped.
MAX_INTERVAL_MINUTES: Final = 180.0


def _minutes(start: datetime, end: datetime) -> float:
    """Minutes from ``start`` to ``end``, both read back from storage."""
    return (stored_sast(end) - stored_sast(start)).total_seconds() / 60


def _interval_minutes(db: Session, ticket: Ticket) -> float | None:
    """This ticket's call interval, or ``None`` when the queue was not busy before it.

    The gap since the previous call in the same queue and day, counted only when this patient had
    already joined by that earlier call (so somebody was waiting the whole time), and ignored above
    :data:`MAX_INTERVAL_MINUTES`.
    """
    if ticket.called_at is None:
        return None
    previous = db.execute(
        select(func.max(Ticket.called_at)).where(
            Ticket.queue_id == ticket.queue_id,
            Ticket.service_day == ticket.service_day,
            Ticket.called_at < ticket.called_at,
            Ticket.id != ticket.id,
        )
    ).scalar_one_or_none()
    if previous is None or stored_sast(ticket.joined_at) > stored_sast(previous):
        return None
    interval = _minutes(previous, ticket.called_at)
    return interval if 0 <= interval <= MAX_INTERVAL_MINUTES else None


def record_visit(
    db: Session, ticket: Ticket, *, moment: datetime | None = None
) -> WaitTimeSample | None:
    """Write the sample for a visit that has just finished. The caller commits.

    Returns ``None`` (and writes nothing) for a ticket that was never called, which a ``done`` ticket
    always has been; the guard keeps a malformed row out of the estimate rather than raising inside
    somebody's transition.
    """
    if ticket.called_at is None:
        return None
    finished = moment or now_sast()
    sample = WaitTimeSample(
        site_id=ticket.site_id,
        queue_id=ticket.queue_id,
        ticket_id=ticket.id,
        service_day=ticket.service_day,
        called_hour=stored_sast(ticket.called_at).hour,
        wait_minutes=max(0.0, _minutes(ticket.joined_at, ticket.called_at)),
        service_minutes=(
            max(0.0, _minutes(ticket.started_at, finished))
            if ticket.started_at is not None
            else None
        ),
        interval_minutes=_interval_minutes(db, ticket),
        recorded_at=finished,
    )
    db.add(sample)
    db.flush()
    return sample


def recent_samples(
    db: Session, queue_ids: Collection[str], *, window: int = DEFAULT_SETTINGS.window
) -> dict[str, list[VisitSample]]:
    """Each queue's most recent ``window`` samples with an interval, newest first, in one query."""
    if not queue_ids:
        return {}
    rank = (
        func.row_number()
        .over(
            partition_by=WaitTimeSample.queue_id,
            order_by=WaitTimeSample.recorded_at.desc(),
        )
        .label("rank")
    )
    ranked = (
        select(
            WaitTimeSample.queue_id,
            WaitTimeSample.interval_minutes,
            WaitTimeSample.called_hour,
            rank,
        )
        .where(
            WaitTimeSample.queue_id.in_(list(queue_ids)),
            WaitTimeSample.interval_minutes.is_not(None),
        )
        .subquery()
    )
    rows = db.execute(
        select(ranked.c.queue_id, ranked.c.interval_minutes, ranked.c.called_hour)
        .where(ranked.c.rank <= window)
        .order_by(ranked.c.queue_id, ranked.c.rank)
    ).all()
    out: dict[str, list[VisitSample]] = {queue_id: [] for queue_id in queue_ids}
    for queue_id, interval, hour in rows:
        out[queue_id].append(VisitSample(float(interval), int(hour)))
    return out


def estimates_for(
    db: Session,
    queues: Collection[Queue],
    people_ahead: Mapping[str, int],
    *,
    moment: datetime | None = None,
    settings: EstimatorSettings = DEFAULT_SETTINGS,
) -> dict[str, WaitEstimate]:
    """A wait estimate per queue, for somebody with ``people_ahead[queue.id]`` people before them."""
    local = stored_sast(moment or now_sast())
    at_hour = local.hour + local.minute / 60
    samples = recent_samples(db, [queue.id for queue in queues], window=settings.window)
    return {
        queue.id: estimate_wait(
            samples.get(queue.id, []),
            people_ahead=people_ahead.get(queue.id, 0),
            at_hour=at_hour,
            expected_service_minutes=queue.expected_service_minutes,
            settings=settings,
        )
        for queue in queues
    }


def ticket_wait(
    db: Session, ticket: Ticket, queue: Queue, *, moment: datetime
) -> tuple[int, WaitEstimate]:
    """How many are waiting ahead of ``ticket``, and the wait that means: **the** estimate for one ticket.

    The ticket page, the answer to a join and the virtual waiting room's "time to leave" alert (Issue 86)
    all read it here, so the number a patient is shown and the number an alert is timed by are one
    computation from one read, and cannot disagree.
    """
    ahead = waiting_ahead(db, ticket)
    return ahead, estimates_for(db, [queue], {queue.id: ahead}, moment=moment)[queue.id]
