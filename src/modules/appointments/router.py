"""HTTP routes for a clinic's appointment book (Issue 80).

Every route names a clinic and goes through :func:`~src.core.site_scope.require_site_access`, so
another clinic's id, or a queue that is not this clinic's, is the guard's **404**. Reading the book is
the front desk's and the nurses' (``appointments:read``); shaping it is the clinic manager's
(``appointments:update``). Every change writes an audit row in the same transaction.

Booking a place for a patient is not here: it arrives with Issue 81, on the capacity guard in
:mod:`src.modules.appointments.capacity`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from src.commons.enums import AuditAction, AuditEntityType
from src.commons.time import business_date, now_sast
from src.core.audit import record_audit_event
from src.core.client_ip import resolve_client_ip
from src.core.site_scope import SiteAccess, require_site_access, site_not_found
from src.database.models import AppointmentDayOverride, AppointmentTemplateWindow, Queue
from src.database.session import get_db
from src.modules.appointments import service
from src.modules.appointments.schemas import (
    BlockCreatedOut,
    BlockIn,
    BlockListOut,
    BlockOut,
    DayAvailabilityOut,
    DayOverrideIn,
    DayOverrideOut,
    GenerateIn,
    GenerateOut,
    PolicyIn,
    PolicyOut,
    WeeklyTemplateIn,
    WeeklyTemplateOut,
    WeeklyWindowOut,
)
from src.modules.queues import service as queues_service

router = APIRouter(prefix="/sites", tags=["appointments"])

DbSession = Annotated[Session, Depends(get_db)]
#: Reading the book: the front desk, the nurses and the manager.
AppointmentsRead = Annotated[
    SiteAccess, Depends(require_site_access("appointments", "read"))
]
#: Shaping the book: the clinic manager.
AppointmentsManage = Annotated[
    SiteAccess, Depends(require_site_access("appointments", "update"))
]


def _audit(
    db: Session,
    request: Request,
    access: SiteAccess,
    *,
    action: AuditAction,
    entity_type: AuditEntityType,
    entity_id: str,
    context: str,
) -> None:
    """Record one change to the book, before the commit, so both land together."""
    record_audit_event(
        db,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=access.user.email,
        actor_id=str(access.user.id),
        site_id=access.site_id,
        ip_address=resolve_client_ip(request),
        context=context,
    )


def _queue_or_404(db: Session, access: SiteAccess, queue_id: str) -> Queue:
    """One of this clinic's queues, or the guard's 404, the same one another clinic's id gets."""
    queue = queues_service.get_queue(db, access, queue_id)
    if queue is None:
        raise site_not_found()
    return queue


def _unprocessable(exc: Exception) -> HTTPException:
    """A 422 carrying the service's sentence."""
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get(
    "/{site_id}/appointments/policy",
    response_model=PolicyOut,
    operation_id="appointmentsGetPolicy",
)
def get_policy(access: AppointmentsRead, db: DbSession) -> PolicyOut:
    """How far ahead, and how late, the clinic takes bookings (the defaults until a manager sets them)."""
    policy, is_default = service.policy_for(db, service.staff_rows(access))
    return PolicyOut(
        site_id=access.site_id,
        horizon_days=policy.horizon_days,
        min_lead_minutes=policy.min_lead_minutes,
        convert_lead_minutes=policy.convert_lead_minutes,
        is_default=is_default,
    )


@router.put(
    "/{site_id}/appointments/policy",
    response_model=PolicyOut,
    operation_id="appointmentsSetPolicy",
)
def set_policy(
    payload: PolicyIn, request: Request, access: AppointmentsManage, db: DbSession
) -> PolicyOut:
    """Set the booking horizon and the minimum lead time. Offered slots follow on the next read."""
    policy = service.set_policy(db, access, payload)
    _audit(
        db,
        request,
        access,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.APPOINTMENT_SCHEDULE,
        entity_id=access.site_id,
        context=(
            f"set the booking policy: {policy.horizon_days} days ahead, "
            f"{policy.min_lead_minutes} minutes' notice, a ticket {policy.convert_lead_minutes} "
            "minutes before"
        ),
    )
    db.commit()
    return PolicyOut(
        site_id=access.site_id,
        horizon_days=policy.horizon_days,
        min_lead_minutes=policy.min_lead_minutes,
        convert_lead_minutes=policy.convert_lead_minutes,
        is_default=False,
    )


def _template_out(
    access: SiteAccess, queue: Queue, rows: Sequence[AppointmentTemplateWindow]
) -> WeeklyTemplateOut:
    """A queue's stored week as the API returns it."""
    return WeeklyTemplateOut(
        site_id=access.site_id,
        queue_id=queue.id,
        windows=[
            WeeklyWindowOut(
                id=row.id,
                weekday=row.weekday,
                **service.window_fields(row).model_dump(),
            )
            for row in rows
        ],
    )


@router.get(
    "/{site_id}/appointments/queues/{queue_id}/template",
    response_model=WeeklyTemplateOut,
    operation_id="appointmentsGetTemplate",
)
def get_template(
    queue_id: str, access: AppointmentsRead, db: DbSession
) -> WeeklyTemplateOut:
    """A queue's ordinary week of appointment windows."""
    queue = _queue_or_404(db, access, queue_id)
    return _template_out(
        access, queue, service.weekly_windows(db, service.staff_rows(access), queue.id)
    )


@router.put(
    "/{site_id}/appointments/queues/{queue_id}/template",
    response_model=WeeklyTemplateOut,
    operation_id="appointmentsReplaceTemplate",
)
def replace_template(
    queue_id: str,
    payload: WeeklyTemplateIn,
    request: Request,
    access: AppointmentsManage,
    db: DbSession,
) -> WeeklyTemplateOut:
    """Replace a queue's whole week. Stored slots change when slots are next generated."""
    queue = _queue_or_404(db, access, queue_id)
    try:
        rows = service.replace_weekly_windows(db, access, queue, payload)
    except service.UnknownServiceError as exc:
        raise _unprocessable(exc) from exc
    _audit(
        db,
        request,
        access,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.APPOINTMENT_SCHEDULE,
        entity_id=queue.id,
        context=f"set {queue.name}'s weekly appointment windows ({len(rows)})",
    )
    db.commit()
    return _template_out(access, queue, rows)


def _override_out(
    access: SiteAccess, queue: Queue, day: date, rows: Sequence[AppointmentDayOverride]
) -> DayOverrideOut:
    """One date's override as the API returns it."""
    return DayOverrideOut(
        site_id=access.site_id,
        queue_id=queue.id,
        day=day,
        windows=[
            service.window_fields(row) for row in rows if row.starts_at is not None
        ],
    )


@router.put(
    "/{site_id}/appointments/queues/{queue_id}/days/{day}",
    response_model=DayOverrideOut,
    operation_id="appointmentsSetDayOverride",
)
def set_day_override(
    queue_id: str,
    day: date,
    payload: DayOverrideIn,
    request: Request,
    access: AppointmentsManage,
    db: DbSession,
) -> DayOverrideOut:
    """Replace one date's windows. An empty list means no appointments that day."""
    queue = _queue_or_404(db, access, queue_id)
    try:
        rows = service.set_day_override(db, access, queue, day, payload)
    except service.UnknownServiceError as exc:
        raise _unprocessable(exc) from exc
    _audit(
        db,
        request,
        access,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.APPOINTMENT_SCHEDULE,
        entity_id=queue.id,
        context=(
            f"set {queue.name}'s appointment windows for {day.isoformat()} "
            f"({len(payload.windows) or 'none'})"
        ),
    )
    db.commit()
    return _override_out(access, queue, day, rows)


@router.delete(
    "/{site_id}/appointments/queues/{queue_id}/days/{day}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="appointmentsClearDayOverride",
)
def clear_day_override(
    queue_id: str,
    day: date,
    request: Request,
    access: AppointmentsManage,
    db: DbSession,
) -> None:
    """Remove one date's override, so the ordinary week applies again."""
    queue = _queue_or_404(db, access, queue_id)
    if service.clear_day_override(db, access, queue, day) == 0:
        raise site_not_found()
    _audit(
        db,
        request,
        access,
        action=AuditAction.DELETE,
        entity_type=AuditEntityType.APPOINTMENT_SCHEDULE,
        entity_id=queue.id,
        context=f"cleared {queue.name}'s appointment override for {day.isoformat()}",
    )
    db.commit()


@router.post(
    "/{site_id}/appointments/queues/{queue_id}/slots/generate",
    response_model=GenerateOut,
    operation_id="appointmentsGenerateSlots",
)
def generate_slots(
    queue_id: str,
    payload: GenerateIn,
    request: Request,
    access: AppointmentsManage,
    db: DbSession,
) -> GenerateOut:
    """Generate (or bring up to date) a queue's slots, by default from today through the horizon.

    Idempotent: a second run with nothing changed creates nothing. Holidays, closures and the hours
    the clinic is shut produce no slots.
    """
    queue = _queue_or_404(db, access, queue_id)
    moment = now_sast()
    rows = service.staff_rows(access)
    policy, _ = service.policy_for(db, rows)
    first_day, last_day = service.generation_range(
        policy, moment, payload.from_day, payload.days
    )
    opening = service.staff_opening(db, access, first_day, last_day)
    result = service.generate_slots(
        db, rows, queue, opening, first_day, last_day, moment=moment
    )
    _audit(
        db,
        request,
        access,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.APPOINTMENT_SCHEDULE,
        entity_id=queue.id,
        context=(
            f"generated {queue.name}'s slots {first_day.isoformat()} to {last_day.isoformat()}: "
            f"{result.created} created, {result.removed} removed, {result.withdrawn} withdrawn, "
            f"{result.restored} restored"
        ),
    )
    db.commit()
    return result.as_out(access.site_id, queue.id)


@router.get(
    "/{site_id}/appointments/availability",
    response_model=DayAvailabilityOut,
    operation_id="appointmentsDayAvailability",
)
def day_availability(
    access: AppointmentsRead,
    db: DbSession,
    day: Annotated[date | None, Query()] = None,
    queue_id: Annotated[str | None, Query()] = None,
    include_unavailable: Annotated[bool, Query()] = False,
) -> DayAvailabilityOut:
    """One service day's bookable slots per queue, capped by each queue's shared daily limit.

    ``day`` defaults to today in Johannesburg. ``include_unavailable`` also lists the slots that cannot
    be booked, each with its reason, for the front desk's view of the whole book.
    """
    moment = now_sast()
    day = day or business_date(moment)
    rows = service.staff_rows(access)
    queues = (
        [_queue_or_404(db, access, queue_id)]
        if queue_id is not None
        else service.bookable_queues(db, rows)
    )
    opening = service.staff_opening(db, access, day, day)
    return service.day_availability(
        db,
        rows,
        site_id=access.site_id,
        queues=queues,
        opening=opening,
        day=day,
        include_unavailable=include_unavailable,
        moment=moment,
    )


@router.get(
    "/{site_id}/appointments/blocks",
    response_model=BlockListOut,
    operation_id="appointmentsListBlocks",
)
def list_blocks(
    access: AppointmentsRead,
    db: DbSession,
    include_lifted: Annotated[bool, Query()] = False,
) -> BlockListOut:
    """The ranges taken out of the book, most recent first."""
    blocks = service.list_blocks(db, access, include_lifted=include_lifted)
    return BlockListOut(
        site_id=access.site_id,
        total=len(blocks),
        items=[BlockOut.model_validate(block) for block in blocks],
    )


@router.post(
    "/{site_id}/appointments/blocks",
    response_model=BlockCreatedOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="appointmentsAddBlock",
)
def add_block(
    payload: BlockIn, request: Request, access: AppointmentsManage, db: DbSession
) -> BlockCreatedOut:
    """Take a range out of the book for a staff absence: its slots stop being offered at once.

    Places already booked inside it are counted in the answer and left standing.
    """
    queue = (
        _queue_or_404(db, access, payload.queue_id)
        if payload.queue_id is not None
        else None
    )
    block, booked = service.add_block(db, access, payload, queue)
    _audit(
        db,
        request,
        access,
        action=AuditAction.CREATE,
        entity_type=AuditEntityType.APPOINTMENT_BLOCK,
        entity_id=block.id,
        context=(
            f"blocked {queue.name if queue else 'every queue'} from "
            f"{block.starts_at.isoformat()} to {block.ends_at.isoformat()}"
            f" ({booked} booked place(s) inside)"
        ),
    )
    db.commit()
    db.refresh(block)
    return BlockCreatedOut(
        **BlockOut.model_validate(block).model_dump(), booked_places_inside=booked
    )


@router.delete(
    "/{site_id}/appointments/blocks/{block_id}",
    response_model=BlockOut,
    operation_id="appointmentsLiftBlock",
)
def lift_block(
    block_id: str, request: Request, access: AppointmentsManage, db: DbSession
) -> BlockOut:
    """Lift a block early: its slots are offered again. The row stays."""
    try:
        block = service.lift_block(db, access, block_id)
    except service.BlockNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    _audit(
        db,
        request,
        access,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.APPOINTMENT_BLOCK,
        entity_id=block.id,
        context="lifted the appointment block early",
    )
    db.commit()
    db.refresh(block)
    return BlockOut.model_validate(block)
