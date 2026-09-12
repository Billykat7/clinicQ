"""How long each queue is right now, as every patient-facing surface reads it (Issue 31).

Discovery (Issue 31), the clinic detail page (Issue 35) and the channel menus (M10) all show a live
queue length, and they must all show the **same** one. This module is the single read they go
through, so there is one answer to "how many people are waiting in the pharmacy queue at Zola".

**There is no ticket table yet, and this module says so rather than inventing a number.** Tickets
arrive with Issue 39 and joining with Issue 40 (M6). Until then nobody can be waiting, and the
honest answer to "how many are waiting" is *not measured*, not ``0``: a clinic showing "0 waiting"
at 07:30 on a Monday sends a patient on a trip on the strength of a figure nobody counted. So
:func:`read_waiting_counts` returns ``None`` for every queue, and every surface renders ``None`` as
"not reported yet". Issue 39 replaces that one function's body with a count of ``waiting`` tickets
per queue for the service day, and nothing above it changes.

**Nor is there a wait estimate, and the same rule holds.** The estimator is Issue 42's. Until it
exists :attr:`LiveQueue.wait_range` is ``None`` and a surface shows the length alone; when it does
arrive it is a :class:`WaitRange`, which cannot hold a single number, because a wait promised as
"12 minutes" is a promise nobody can keep.

**The reader is a parameter, not a global.** :func:`published_live_queues` takes the counting
function as an argument (defaulting to the direct read), which is the seam two later issues plug
into without touching a caller: Issue 36's snapshot cache wraps the direct read, and the tests
hand in a reader with real numbers to prove that a count travels intact from here to the API.
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.commons.enums import QueueKind
from src.core.site_scope import published_select
from src.database.models.queue import Queue

#: Given the queues asked about, return ``{queue id: waiting count}``, where ``None`` means the
#: count is not measured. A reader must answer for every queue it is given.
WaitingCountReader = Callable[[Session, Collection[Queue]], Mapping[str, int | None]]


@dataclass(frozen=True, slots=True)
class WaitRange:
    """An expected wait, always as a range: "15–25 min", never "20 min" (Issues 35, 42).

    Built only by the estimator (Issue 42). The invariant is checked on construction, so a surface
    handed one can render it without deciding what to do with a degenerate range.
    """

    low_minutes: int
    high_minutes: int

    def __post_init__(self) -> None:
        """Refuse a negative bound, and a "range" that is really one number."""
        if self.low_minutes < 0:
            raise ValueError("A wait cannot be negative.")
        if self.high_minutes <= self.low_minutes:
            raise ValueError(
                f"A wait is a range: {self.low_minutes}–{self.high_minutes} min is not one."
            )


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
    #: The estimator's range (Issue 42). ``None`` until it exists: show the length, never a guess.
    wait_range: WaitRange | None = None


def read_waiting_counts(
    db: Session, queues: Collection[Queue]
) -> Mapping[str, int | None]:
    """The direct read of each queue's length: ``None`` for every queue until Issue 39 lands.

    Issue 39 creates the ticket table and replaces this body with one grouped ``COUNT`` of
    ``waiting`` tickets per queue for today's service day. The signature is the contract: it takes
    the queues rather than their ids so the count can stay a single query however many clinics a
    page shows.

    Args:
        db: The session. Unused until tickets exist; part of the contract every reader keeps.
        queues: The queues to count.

    Returns:
        ``{queue id: None}`` for each queue: not measured.
    """
    del db  # read by Issue 39's implementation
    return dict.fromkeys((queue.id for queue in queues), None)


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
    counts = reader(db, rows)
    by_site: dict[str, list[LiveQueue]] = {}
    for queue in rows:
        by_site.setdefault(queue.site_id, []).append(
            LiveQueue(
                queue_id=queue.id,
                site_id=queue.site_id,
                name=queue.name,
                kind=queue.kind_enum,
                display_order=queue.display_order,
                allows_remote_join=queue.allows_remote_join,
                waiting=counts.get(queue.id),
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
