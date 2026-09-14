"""HTTP routes for joining a queue and reading tickets (Issue 40).

Two doors over HTTP, one service behind both. The routes only work out **who is asking and where**,
then call :func:`src.modules.queue.service.join_queue`, which makes every decision. USSD and
WhatsApp (M10) call the same function from their adapters; they do not get routes of their own
here.

* **A patient joins** at ``POST /clinics/{site_id}/queues/{queue_id}/tickets`` with their own
  session. The clinic must be publicly listed (:func:`~src.core.site_scope.published_select`), so
  an unverified clinic's queue is a 404 to a patient exactly as it is in discovery. The per-address
  guard applies here, and nowhere else.
* **The front desk issues a walk-in** at ``POST /sites/{site_id}/queues/{queue_id}/tickets``,
  through the site guard with the ``queues.tickets`` grant, so another clinic's queue is a 404.
* **Reading:** the desk reads the clinic's tickets still in the day; a patient reads their own.

A join that finds the patient's existing ticket answers ``200`` with ``created: false``; a new ticket
answers ``201``. A refusal is the error envelope with ``code`` ``queue.join.<reason>`` and a
sentence the patient can be shown: ``409``, or ``429`` with ``Retry-After`` when rate limited.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.rbac_deps import require_patient
from src.commons.enums import JoinRefusal, PatientChannel, TicketSource
from src.commons.phone import normalize_phone
from src.commons.time import business_date
from src.core.client_ip import resolve_client_ip
from src.core.config import Settings, get_settings
from src.core.rate_limit_deps import DISCOVERY_SESSION_COOKIE
from src.core.site_scope import (
    SiteAccess,
    publicly_visible_site_clauses,
    published_select,
    require_site_access,
    site_not_found,
)
from src.database.models import Patient, Queue, Site
from src.database.session import get_db
from src.modules.patients.service import get_or_create_patient
from src.modules.queue import service
from src.modules.queue.schemas import (
    JoinIn,
    JoinOut,
    TicketListOut,
    TicketOut,
    WalkInIn,
)
from src.modules.queue.service import JoinRefusedError, JoinResult
from src.modules.queue.tickets import patient_tickets_select, site_day_select
from src.modules.queues.service import get_queue
from src.modules.sites.hours import published_schedules, schedule_for

router = APIRouter(tags=["queue"])

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

#: Issuing a walk-in: the front desk's grant, and a nurse's on their own queues (Issue 18).
TicketsIssue = Annotated[
    SiteAccess, Depends(require_site_access("queues.tickets", "update"))
]
#: Reading the clinic's tickets: everyone who works the queues.
TicketsRead = Annotated[
    SiteAccess, Depends(require_site_access("queues.tickets", "read"))
]
#: A patient acting for themselves: joining a queue and reading their own tickets.
PatientJoining = Annotated[Patient, Depends(require_patient("patients.self", "update"))]
PatientReading = Annotated[Patient, Depends(require_patient("patients.self", "read"))]


def _answer(result: JoinResult, response: Response) -> JoinOut:
    """``201`` for a new ticket, ``200`` for the one the patient already held, and what to say."""
    number = result.ticket.number
    if result.created:
        response.status_code = status.HTTP_201_CREATED
        message = f"You are {number}."
    else:
        response.status_code = status.HTTP_200_OK
        message = f"You already hold {number} in this queue."
    return JoinOut(
        ticket=TicketOut.of(result.ticket),
        created=result.created,
        waiting_ahead=result.waiting_ahead,
        message=message,
    )


def _too_many(error: JoinRefusedError) -> HTTPException:
    """Rate limiting answers ``429`` with ``Retry-After``; every other refusal keeps its ``409``."""
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail=str(error),
        headers={"Retry-After": str(error.retry_after_seconds or 60)},
    )


@router.post(
    "/clinics/{site_id}/queues/{queue_id}/tickets",
    response_model=JoinOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="queueJoin",
    summary="Join a queue as the signed-in patient",
)
def join_as_patient(
    site_id: str,
    queue_id: str,
    payload: JoinIn,
    request: Request,
    response: Response,
    patient: PatientJoining,
    db: DbSession,
    settings: SettingsDep,
) -> JoinOut:
    """Join a queue from the web. Returns the patient's existing ticket if they already hold one."""
    site = db.execute(
        select(Site).where(Site.id == site_id, *publicly_visible_site_clauses())
    ).scalar_one_or_none()
    queue = db.execute(
        published_select(Queue, [site_id]).where(
            Queue.id == queue_id, Queue.is_deleted.is_(False)
        )
    ).scalar_one_or_none()
    schedule = published_schedules(db, [site_id]).get(site_id)
    if site is None or queue is None or schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such queue.")
    try:
        result = service.join_queue(
            db,
            site=site,
            queue=queue,
            schedule=schedule,
            source=TicketSource.WEB,
            patient=patient,
            actor=f"patient:{patient.id}",
            reason_text=payload.reason_text,
            comment_consent=payload.comment_consent,
            client_ip=resolve_client_ip(request, settings),
            discovery_session=request.cookies.get(DISCOVERY_SESSION_COOKIE),
            settings=settings,
        )
    except JoinRefusedError as error:
        if error.refusal is JoinRefusal.RATE_LIMITED:
            raise _too_many(error) from error
        raise
    db.commit()
    return _answer(result, response)


@router.post(
    "/sites/{site_id}/queues/{queue_id}/tickets",
    response_model=JoinOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="queueIssueWalkIn",
    summary="Issue a walk-in ticket at the front desk",
)
def issue_walk_in(
    queue_id: str,
    payload: WalkInIn,
    request: Request,
    response: Response,
    access: TicketsIssue,
    db: DbSession,
    settings: SettingsDep,
) -> JoinOut:
    """Issue a walk-in in the same sequence as every remote join. A phone number is optional."""
    site = db.get(Site, access.site_id)
    queue = get_queue(db, access, queue_id)
    if site is None or site.is_deleted or queue is None:
        raise site_not_found()
    patient = None
    if payload.phone is not None:
        patient, _ = get_or_create_patient(db, normalize_phone(payload.phone))
        patient.last_channel = PatientChannel.WALK_IN.value
    try:
        result = service.join_queue(
            db,
            site=site,
            queue=queue,
            schedule=schedule_for(db, access),
            source=TicketSource.WALK_IN,
            patient=patient,
            actor=access.user.email,
            actor_id=str(access.user.id),
            walk_in_name=payload.name,
            reason_text=payload.reason_text,
            comment_consent=payload.comment_consent,
            client_ip=resolve_client_ip(request, settings),
            settings=settings,
        )
    except JoinRefusedError as error:
        if error.refusal is JoinRefusal.RATE_LIMITED:
            raise _too_many(error) from error
        raise
    db.commit()
    return _answer(result, response)


@router.get(
    "/sites/{site_id}/tickets",
    response_model=TicketListOut,
    operation_id="queueSiteTickets",
    summary="The clinic's tickets still in today's queues",
)
def site_tickets(
    access: TicketsRead,
    db: DbSession,
    queue_id: Annotated[str | None, Query()] = None,
) -> TicketListOut:
    """Every ticket not yet finished today, queue by queue in sequence order (or one queue's)."""
    today = business_date()
    rows = db.execute(site_day_select(access, today, queue_id=queue_id)).scalars().all()
    return TicketListOut(
        site_id=access.site_id,
        service_day=today,
        total=len(rows),
        items=[TicketOut.of(row) for row in rows],
    )


@router.get(
    "/patients/me/tickets",
    response_model=list[TicketOut],
    operation_id="queueMyTickets",
    summary="The signed-in patient's tickets today",
)
def my_tickets(patient: PatientReading, db: DbSession) -> list[TicketOut]:
    """The patient's own tickets today, at any clinic, earliest first."""
    rows = (
        db.execute(patient_tickets_select(patient.id, business_date())).scalars().all()
    )
    return [TicketOut.of(row) for row in rows]
