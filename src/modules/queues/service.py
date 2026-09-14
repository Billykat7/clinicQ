"""Queue persistence and the rules around it (Issue 25).

Everything that touches the database for a clinic's queues, and the two rules that outlive this
issue:

* **Deactivation is not deletion.** :func:`deactivate_queue` clears ``is_active``; the row and every
  ticket that ever pointed at it stay. "How long did the pharmacy take last month" has to be
  answerable about a line the clinic has since closed, so :func:`list_queues` takes
  ``include_inactive`` and the history reads never filter on it at all.
* **A walk-in-only queue is enforced here.** :func:`ensure_remote_join_allowed` is the server-side
  check every join path calls (Issue 40 brings the routes). Hiding the button is not enforcement;
  a USSD session does not have buttons.

Every query is built by the site guard, so another clinic's queues are unreachable rather than
merely unasked-for.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from src.commons.enums import BoundedContext, QueueKind, TicketSource
from src.commons.schemas import ModuleInfo
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models.queue import Queue
from src.modules.queues.schemas import QueueIn, QueueListOut, QueueOut

#: The sources that are **not** in the building. A queue with ``allows_remote_join`` false accepts
#: only a ticket issued at the desk, so this set is what :func:`ensure_remote_join_allowed` refuses.
REMOTE_SOURCES: frozenset[TicketSource] = frozenset(
    {TicketSource.WEB, TicketSource.USSD, TicketSource.WHATSAPP}
)

#: What a patient is told when they try to join a walk-in-only queue from a phone.
WALK_IN_ONLY = (
    "This queue takes walk-in patients only. Please join at the clinic's front desk."
)
#: And when the queue is not taking anyone at all.
QUEUE_CLOSED = "This queue is not taking patients at the moment."

#: The queues a newly onboarded clinic starts with (Issue 25, used by Issue 29's onboarding).
#: A real visit in order: triage, a consulting room, the pharmacy window. A clinic edits, renames,
#: reorders or deactivates any of them; what matters is that it does not start with an empty board.
#: ``(slug, name, kind, prefix, minutes, allows remote joins)``.
DEFAULT_QUEUES: tuple[tuple[str, str, QueueKind, str, int, bool], ...] = (
    ("triage", "Triage", QueueKind.TRIAGE, "T", 5, True),
    ("general", "General consultation", QueueKind.CONSULTATION, "A", 15, True),
    # The pharmacy is walk-in only by default: you cannot collect medicine from a phone, and a
    # remote ticket for it would put somebody in a line they cannot reach the front of.
    ("pharmacy", "Pharmacy", QueueKind.PHARMACY, "P", 5, False),
)


class QueueNameTakenError(ValueError):
    """This clinic already has a queue with that name or slug."""


class RemoteJoinNotAllowedError(PermissionError):
    """This queue takes walk-ins only, and the join came from a phone."""


class QueueNotJoinableError(PermissionError):
    """This queue is not accepting new patients at all."""


def get_module_info() -> ModuleInfo:
    """Return this module's metadata for its ``/info`` endpoint."""
    return ModuleInfo(
        context=BoundedContext.QUEUES,
        summary="A clinic's queues: triage, the consulting rooms and the pharmacy window.",
    )


def _live(access: SiteAccess) -> Select[tuple[Queue]]:
    """The base query every read starts from: this clinic's queues that have not been removed.

    ``is_active`` is **not** filtered here on purpose. Deactivating a queue hides it from new
    joins, not from history, and a base query that dropped inactive rows would quietly make last
    month's pharmacy report empty.
    """
    return scoped_select(Queue, access).where(Queue.is_deleted.is_(False))


def list_queues(
    db: Session, access: SiteAccess, *, include_inactive: bool = True
) -> QueueListOut:
    """This clinic's queues, in the order the clinic put them in.

    Args:
        db: The session.
        access: The clinic, from the site guard.
        include_inactive: ``True`` (the default) for a manager's screen, which has to show a
            deactivated queue in order to reactivate it. A join surface passes ``False``.
    """
    statement = _live(access)
    if not include_inactive:
        statement = statement.where(Queue.is_active.is_(True))
    rows = (
        db.execute(statement.order_by(Queue.display_order, Queue.name)).scalars().all()
    )
    return QueueListOut(
        site_id=access.site_id,
        total=len(rows),
        items=[QueueOut.model_validate(row) for row in rows],
    )


def get_queue(db: Session, access: SiteAccess, queue_id: str) -> Queue | None:
    """One of this clinic's queues by id, or ``None`` — including a deactivated one."""
    return db.execute(_live(access).where(Queue.id == queue_id)).scalar_one_or_none()


def name_is_free(
    db: Session, access: SiteAccess, payload: QueueIn, *, excluding: str | None = None
) -> bool:
    """Whether this clinic may use that name and slug, ignoring the queue being renamed."""
    statement = _live(access).where(
        (Queue.slug == payload.slug) | (Queue.name == payload.name)
    )
    if excluding is not None:
        statement = statement.where(Queue.id != excluding)
    return db.execute(statement).first() is None


def create_queue(db: Session, access: SiteAccess, payload: QueueIn) -> Queue:
    """Add a queue to this clinic. The caller commits.

    Raises:
        QueueNameTakenError: If the clinic already uses that name or slug.
    """
    if not name_is_free(db, access, payload):
        raise QueueNameTakenError(
            f"This clinic already has a queue called {payload.name!r}."
        )
    queue = Queue(
        site_id=access.site_id,
        name=payload.name,
        slug=payload.slug,
        kind=payload.kind.value,
        room_label=payload.room_label,
        ticket_prefix=payload.ticket_prefix,
        display_order=payload.display_order,
        expected_service_minutes=payload.expected_service_minutes,
        max_daily_capacity=payload.max_daily_capacity,
        recall_timeout_minutes=payload.recall_timeout_minutes,
        allows_remote_join=payload.allows_remote_join,
        is_active=payload.is_active,
    )
    db.add(queue)
    db.flush()
    return queue


def update_queue(
    db: Session, access: SiteAccess, queue: Queue, payload: QueueIn
) -> Queue:
    """Replace a queue's editable fields. The caller commits.

    Raises:
        QueueNameTakenError: If the new name or slug belongs to another queue at this clinic.
    """
    if not name_is_free(db, access, payload, excluding=queue.id):
        raise QueueNameTakenError(
            f"This clinic already has a queue called {payload.name!r}."
        )
    queue.name = payload.name
    queue.slug = payload.slug
    queue.kind = payload.kind.value
    queue.room_label = payload.room_label
    queue.ticket_prefix = payload.ticket_prefix
    queue.display_order = payload.display_order
    queue.expected_service_minutes = payload.expected_service_minutes
    queue.max_daily_capacity = payload.max_daily_capacity
    queue.recall_timeout_minutes = payload.recall_timeout_minutes
    queue.allows_remote_join = payload.allows_remote_join
    queue.is_active = payload.is_active
    db.flush()
    return queue


def deactivate_queue(db: Session, queue: Queue) -> Queue:
    """Hide a queue from new joins, keeping every ticket that ever pointed at it. Caller commits."""
    queue.is_active = False
    db.flush()
    return queue


def reorder_queues(
    db: Session, access: SiteAccess, queue_ids: Sequence[str]
) -> QueueListOut:
    """Put this clinic's queues in the given order. The caller commits.

    Ids that do not belong to this clinic are ignored rather than refused: the site guard has
    already decided what this caller may touch, and a stale id in a browser's list must not stop a
    manager reordering the rest.
    """
    by_id = {queue.id: queue for queue in db.execute(_live(access)).scalars().all()}
    for position, queue_id in enumerate(queue_ids):
        queue = by_id.get(queue_id)
        if queue is not None:
            queue.display_order = position
    db.flush()
    return list_queues(db, access)


def create_default_queues(db: Session, site_id: str) -> list[Queue]:
    """Give a newly onboarded clinic the queue set in :data:`DEFAULT_QUEUES`. Caller commits.

    Idempotent: a clinic that already has any queue is left exactly as it is, so re-running
    onboarding (or a seed) never duplicates a line or resurrects one the clinic deleted.
    """
    already = db.execute(select(Queue.id).where(Queue.site_id == site_id)).first()
    if already is not None:
        return []
    created = [
        Queue(
            site_id=site_id,
            slug=slug,
            name=name,
            kind=kind.value,
            ticket_prefix=prefix,
            display_order=position,
            expected_service_minutes=minutes,
            allows_remote_join=remote,
        )
        for position, (slug, name, kind, prefix, minutes, remote) in enumerate(
            DEFAULT_QUEUES
        )
    ]
    db.add_all(created)
    db.flush()
    return created


# --------------------------------------------------------------------------------------
# The server-side join rules (Issue 25; the join routes themselves are Issue 40)
# --------------------------------------------------------------------------------------


def refusal_for(queue: Queue, source: TicketSource) -> str | None:
    """Why ``source`` may not join ``queue``, or ``None`` when it may.

    A function rather than a flag on the queue, because the answer depends on where the patient is
    standing. It is the **server's** answer: a channel renders it, and never decides it.
    """
    if not queue.is_active or queue.is_deleted:
        return QUEUE_CLOSED
    if source in REMOTE_SOURCES and not queue.allows_remote_join:
        return WALK_IN_ONLY
    return None


def ensure_remote_join_allowed(queue: Queue, source: TicketSource) -> None:
    """Raise unless ``source`` may join ``queue``. The check every join path calls.

    Raises:
        QueueNotJoinableError: If the queue is not taking patients at all.
        RemoteJoinNotAllowedError: If the queue takes walk-ins only and the join came from a phone.
    """
    refusal = refusal_for(queue, source)
    if refusal is None:
        return
    if refusal == WALK_IN_ONLY:
        raise RemoteJoinNotAllowedError(refusal)
    raise QueueNotJoinableError(refusal)


def joinable_queues(
    db: Session, access: SiteAccess, source: TicketSource
) -> list[tuple[Queue, str | None]]:
    """This clinic's queues with the server's join answer for ``source``, in display order.

    What a channel menu renders. The refusal travels with the queue rather than the queue being
    dropped, so a patient on USSD is told *why* the pharmacy is not on the list instead of being
    left to wonder whether the clinic has one.
    """
    rows = (
        db.execute(_live(access).order_by(Queue.display_order, Queue.name))
        .scalars()
        .all()
    )
    return [(queue, refusal_for(queue, source)) for queue in rows]


def count_queues(db: Session, access: SiteAccess) -> int:
    """How many live queues this clinic has. Used by the onboarding check and by tests."""
    return int(
        db.execute(
            select(func.count()).select_from(_live(access).subquery())
        ).scalar_one()
    )
