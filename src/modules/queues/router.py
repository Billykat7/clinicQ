"""HTTP routes for a clinic's queues (Issue 25).

Every route names a clinic, so every one goes through
:func:`~src.core.site_scope.require_site_access`: another clinic's id is a **404**, with the same
body an id that never existed gets, and the verb is resolved with the roles the caller holds *at
that clinic*. Reading is the front desk's grant (``queues:read``); configuring a queue is the
manager's (``queues:delete``, the top of the ladder, per the module's manifest).

The routes hang off ``/sites/{site_id}/queues`` rather than a ``/queues`` prefix of their own,
because a queue does not exist outside a clinic and a URL that implied otherwise would invite a
handler that forgot the site filter.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from src.commons.enums import AuditAction, AuditEntityType, TicketSource
from src.core.audit import record_audit_event
from src.core.client_ip import resolve_client_ip
from src.core.site_scope import SiteAccess, require_site_access, site_not_found
from src.database.models.queue import Queue
from src.database.session import get_db
from src.modules.queues import service
from src.modules.queues.schemas import (
    JoinableQueueListOut,
    JoinableQueueOut,
    QueueIn,
    QueueListOut,
    QueueOrderIn,
    QueueOut,
)

router = APIRouter(prefix="/sites", tags=["queues"])

DbSession = Annotated[Session, Depends(get_db)]

#: Reading the clinic's queues: the front desk, the nurses and the manager all hold this.
QueuesRead = Annotated[SiteAccess, Depends(require_site_access("queues", "read"))]
#: Adding, editing, reordering and deactivating: the clinic manager's grant.
QueuesManage = Annotated[SiteAccess, Depends(require_site_access("queues", "delete"))]


def _audit(
    db: Session,
    request: Request,
    access: SiteAccess,
    action: AuditAction,
    queue_id: str,
    context: str,
) -> None:
    """Record one queue mutation. Called before the commit so both land in one transaction."""
    record_audit_event(
        db,
        action=action,
        entity_type=AuditEntityType.QUEUE,
        entity_id=queue_id,
        actor=access.user.email,
        actor_id=str(access.user.id),
        ip_address=resolve_client_ip(request),
        context=context,
    )


def _queue_or_404(db: Session, access: SiteAccess, queue_id: str) -> Queue:
    """One of this clinic's queues, or the guard's 404 — the same one another clinic's id gets."""
    queue = service.get_queue(db, access, queue_id)
    if queue is None:
        raise site_not_found()
    return queue


@router.get("/queues/info", summary="Module metadata", operation_id="queuesInfo")
def queues_info() -> dict[str, str]:
    """Return queues module metadata (unauthenticated, like every other ``/info``)."""
    info = service.get_module_info()
    return {"context": info.context.value, "summary": info.summary}


@router.get("/{site_id}/queues", response_model=QueueListOut, operation_id="queuesList")
def list_queues(
    access: QueuesRead,
    db: DbSession,
    include_inactive: Annotated[bool, Query()] = True,
) -> QueueListOut:
    """This clinic's queues, in the order the clinic put them in.

    Deactivated queues are included by default, because a manager's screen has to show one in order
    to bring it back; a join surface asks for ``include_inactive=false``.
    """
    return service.list_queues(db, access, include_inactive=include_inactive)


@router.get(
    "/{site_id}/queues/joinable",
    response_model=JoinableQueueListOut,
    operation_id="queuesJoinable",
)
def joinable_queues(
    access: QueuesRead,
    db: DbSession,
    source: Annotated[TicketSource, Query()] = TicketSource.WEB,
) -> JoinableQueueListOut:
    """What the given channel may join here, with the **server's** reason where it may not.

    The refusal travels with the queue rather than the queue being dropped from the list, so a
    patient on USSD is told the pharmacy is walk-in only instead of being left to wonder whether
    the clinic has one.
    """
    return JoinableQueueListOut(
        site_id=access.site_id,
        source=source,
        items=[
            JoinableQueueOut(
                id=queue.id,
                name=queue.name,
                slug=queue.slug,
                kind=queue.kind_enum,
                room_label=queue.room_label,
                expected_service_minutes=queue.expected_service_minutes,
                joinable=refusal is None,
                refusal=refusal,
            )
            for queue, refusal in service.joinable_queues(db, access, source)
        ],
    )


@router.post(
    "/{site_id}/queues",
    response_model=QueueOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="queuesCreate",
)
def create_queue(
    payload: QueueIn, request: Request, access: QueuesManage, db: DbSession
) -> QueueOut:
    """Add a queue to this clinic; records a CREATE audit event."""
    try:
        queue = service.create_queue(db, access, payload)
    except service.QueueNameTakenError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    _audit(
        db,
        request,
        access,
        AuditAction.CREATE,
        queue.id,
        f"added the queue {queue.name!r} ({queue.kind})",
    )
    db.commit()
    db.refresh(queue)
    return QueueOut.model_validate(queue)


@router.get(
    "/{site_id}/queues/{queue_id}",
    response_model=QueueOut,
    operation_id="queuesGet",
)
def get_queue(queue_id: str, access: QueuesRead, db: DbSession) -> QueueOut:
    """One queue, including a deactivated one — its history is still the clinic's."""
    return QueueOut.model_validate(_queue_or_404(db, access, queue_id))


@router.put(
    "/{site_id}/queues/{queue_id}",
    response_model=QueueOut,
    operation_id="queuesUpdate",
)
def update_queue(
    queue_id: str,
    payload: QueueIn,
    request: Request,
    access: QueuesManage,
    db: DbSession,
) -> QueueOut:
    """Replace a queue's editable fields; records an UPDATE audit event."""
    queue = _queue_or_404(db, access, queue_id)
    try:
        service.update_queue(db, access, queue, payload)
    except service.QueueNameTakenError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    _audit(db, request, access, AuditAction.UPDATE, queue.id, f"updated {queue.name!r}")
    db.commit()
    db.refresh(queue)
    return QueueOut.model_validate(queue)


@router.put(
    "/{site_id}/queues",
    response_model=QueueListOut,
    operation_id="queuesReorder",
)
def reorder_queues(
    payload: QueueOrderIn, request: Request, access: QueuesManage, db: DbSession
) -> QueueListOut:
    """Put this clinic's queues in the given order; records one UPDATE audit event for the set."""
    ordered = service.reorder_queues(db, access, payload.queue_ids)
    _audit(
        db,
        request,
        access,
        AuditAction.UPDATE,
        access.site_id,
        f"reordered {len(payload.queue_ids)} queue(s)",
    )
    db.commit()
    return ordered


@router.delete(
    "/{site_id}/queues/{queue_id}",
    response_model=QueueOut,
    operation_id="queuesDeactivate",
)
def deactivate_queue(
    queue_id: str, request: Request, access: QueuesManage, db: DbSession
) -> QueueOut:
    """Take a queue out of new joins. **Its history stays**, which is why this is not a delete.

    Returns the queue rather than 204, so the screen that called it can show the new state without
    a second request — and so it is visible that the row is still there.
    """
    queue = _queue_or_404(db, access, queue_id)
    service.deactivate_queue(db, queue)
    _audit(
        db,
        request,
        access,
        AuditAction.UPDATE,
        queue.id,
        f"deactivated {queue.name!r}; its tickets are kept",
    )
    db.commit()
    db.refresh(queue)
    return QueueOut.model_validate(queue)
