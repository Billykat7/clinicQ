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
* **Moving a ticket** (Issue 41): ``POST /sites/{site_id}/tickets/{ticket_id}/transitions`` and
  *Call next* both go through :func:`src.modules.queue.lifecycle.transition_ticket`, the only writer
  of a ticket's status. An illegal or stale move is a ``409`` and changes nothing.

A join that finds the patient's existing ticket answers ``200`` with ``created: false``; a new ticket
answers ``201``. A refusal is the error envelope with ``code`` ``queue.join.<reason>`` and a
sentence the patient can be shown: ``409``, or ``429`` with ``Retry-After`` when rate limited.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.rbac_deps import require_patient
from src.commons.enums import (
    ActorKind,
    ConsentPurpose,
    JoinRefusal,
    PatientChannel,
    TicketSource,
    TicketStatus,
)
from src.commons.exceptions import NotFoundError
from src.commons.phone import normalize_phone
from src.commons.time import business_date, business_day_bounds, stored_sast
from src.core.client_ip import resolve_client_ip
from src.core.config import Settings, get_settings
from src.core.rate_limit_deps import DISCOVERY_SESSION_COOKIE
from src.core.site_scope import (
    SiteAccess,
    get_in_site_or_404,
    publicly_visible_site_clauses,
    published_select,
    require_site_access,
    scoped_select,
    site_not_found,
)
from src.database.models import Patient, Queue, Site, Ticket, Visit
from src.database.session import get_db
from src.modules.patients.consent import record_consent
from src.modules.patients.service import get_or_create_patient
from src.modules.patients.sessions import signed_in_patient_id
from src.modules.queue import service
from src.modules.queue.cancellation import (
    CancelResult,
    cancel_own_ticket,
    cancel_ticket,
)
from src.modules.queue.lifecycle import Actor, call_next, staff_move, undo_call
from src.modules.queue.priority import (
    COUNTS_ARE_NOT_RANKINGS,
    override_counts,
    override_priority,
    reorder_trail,
)
from src.modules.queue.request_keys import (
    REQUEST_KEY_HEADER,
    REQUEST_KEY_PATTERN,
    run_once,
)
from src.modules.queue.schemas import (
    CancelIn,
    CancelOut,
    JoinIn,
    JoinOut,
    MyTicketOut,
    OverrideCountsOut,
    PriorityIn,
    PriorityOut,
    ReorderOut,
    ReorderTrailOut,
    StaffOverrideCountOut,
    TicketListOut,
    TicketLookupOut,
    TicketOut,
    TicketPageOut,
    TransferIn,
    TransferOut,
    TransitionIn,
    VisitLegOut,
    VisitListOut,
    VisitOut,
    WaitOut,
    WalkInIn,
)
from src.modules.queue.service import JoinRefusedError, JoinResult
from src.modules.queue.ticket_codes import lookup_view
from src.modules.queue.ticket_page import find_by_page_token, page_state, page_url_for
from src.modules.queue.tickets import (
    CALL_ORDER,
    patient_tickets_select,
    site_day_select,
    waiting_ahead,
)
from src.modules.queue.transfer import VisitSummary, transfer_ticket, visit_summary
from src.modules.queue.waits import estimates_for
from src.modules.queue.walk_ins import undo_walk_in
from src.modules.queues.service import get_queue
from src.modules.sites.hours import published_schedules, schedule_for

router = APIRouter(tags=["queue"])

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

#: Issuing a walk-in: the front desk's grant, and a nurse's on their own queues (Issue 18).
TicketsIssue = Annotated[
    SiteAccess, Depends(require_site_access("queues.tickets", "update"))
]
#: Calling the next patient: the front desk on any queue here, a nurse on their own (Issue 18).
CallNext = Annotated[SiteAccess, Depends(require_site_access("queues.call", "update"))]
#: Seeing who would be called next.
CallPeek = Annotated[SiteAccess, Depends(require_site_access("queues.call", "read"))]
#: Reading the clinic's tickets: everyone who works the queues.
TicketsRead = Annotated[
    SiteAccess, Depends(require_site_access("queues.tickets", "read"))
]
#: The dashboard's ``Idempotency-Key`` (Issue 50): the same key twice does the action once.
RequestKey = Annotated[
    str | None,
    Header(
        alias=REQUEST_KEY_HEADER,
        pattern=REQUEST_KEY_PATTERN,
        description=(
            "A fresh random value per intended action, repeated when the same action is retried. "
            "A repeat answers with the first request's ticket and moves nothing."
        ),
    ),
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
        wait=WaitOut.of(result.wait),
        message=message,
        page_url=page_url_for(result.ticket),
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
    request_key: RequestKey = None,
) -> JoinOut:
    """Issue a walk-in in the same sequence as every remote join. A phone number is optional.

    With a phone, the patient's answer to being messaged about their turn is recorded as their
    ``notifications`` consent (Issue 51), in the same transaction as the ticket. With an
    ``Idempotency-Key``, a repeated press answers ``200`` with the ticket the first one issued.
    """
    site = db.get(Site, access.site_id)
    queue = get_queue(db, access, queue_id)
    if site is None or site.is_deleted or queue is None:
        raise site_not_found()
    issued: list[JoinResult] = []

    def issue() -> Ticket:
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
        if patient is not None and payload.notifications_consent:
            record_consent(
                db,
                patient,
                ConsentPurpose.NOTIFICATIONS,
                granted=True,
                channel=PatientChannel.WALK_IN,
                recorded_by=str(access.user.id),
                site_id=site.id,
            )
        issued.append(result)
        return result.ticket

    ticket = run_once(
        db,
        access,
        request_key,
        operation="walk_in",
        target=queue.id,
        act=issue,
    )
    if issued:
        return _answer(issued[0], response)
    return _answer(service.describe_ticket(db, queue, ticket), response)


@router.post(
    "/sites/{site_id}/tickets/{ticket_id}/undo-walk-in",
    response_model=CancelOut,
    operation_id="queueUndoWalkIn",
    summary="Undo the last walk-in issued at the desk, within the undo window",
)
def undo_last_walk_in(ticket_id: str, access: TicketsIssue, db: DbSession) -> CancelOut:
    """Take back the caller's most recent walk-in while it is still waiting and inside
    ``QUEUE_WALK_IN_UNDO_SECONDS``. It is cancelled with the reason ``joined_by_mistake`` and the audit
    row says ``undo walk-in``; its number is never reused."""
    ticket = get_in_site_or_404(db, Ticket, ticket_id, access)
    result = undo_walk_in(db, access, ticket.id, actor=_staff(access))
    db.commit()
    return _cancelled(result)


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
    "/sites/{site_id}/tickets/lookup",
    response_model=TicketLookupOut,
    operation_id="queueTicketLookup",
    summary="The ticket a scanned QR or a typed reference code opens (Issue 70)",
)
def lookup_ticket(
    access: TicketsRead,
    db: DbSession,
    code: Annotated[str, Query(min_length=1, max_length=64)],
) -> TicketLookupOut:
    """Resolve what a desk scanner or a receptionist typed: ``CLINICQ:K7M-4QP``, ``K7M-4QP`` or ``k7m 4qp``.

    Only this clinic's tickets, and only today's. A refusal is the error envelope with ``code``
    ``queue.lookup.<reason>``: ``not_a_code`` (422), ``not_found`` (404, also for another clinic's code),
    ``expired`` (422, a code from an earlier day, naming it) or ``ended`` (409). A transferred ticket's code
    opens the leg the visit is in now.
    """
    return lookup_view(db, access, code, today=business_date())


@router.get(
    "/patients/me/tickets",
    response_model=list[MyTicketOut],
    operation_id="queueMyTickets",
    summary="The signed-in patient's tickets today, with where each stands",
)
def my_tickets(patient: PatientReading, db: DbSession) -> list[MyTicketOut]:
    """The patient's own tickets today, earliest first. A waiting one says how many are ahead and
    the wait that means, both derived from the queue as it is now, so a cancellation ahead shows on
    the next read (Issue 44)."""
    rows = (
        db.execute(patient_tickets_select(patient.id, business_date())).scalars().all()
    )
    out: list[MyTicketOut] = []
    for row in rows:
        mine = MyTicketOut(**TicketOut.of(row).model_dump(), page_url=page_url_for(row))
        if row.status_enum is TicketStatus.WAITING:
            queue = db.get(Queue, row.queue_id)
            ahead = waiting_ahead(db, row)
            mine.waiting_ahead = ahead
            if queue is not None:
                mine.wait = WaitOut.of(
                    estimates_for(db, [queue], {queue.id: ahead})[queue.id]
                )
        out.append(mine)
    return out


@router.get(
    "/tickets/{page_token}",
    response_model=TicketPageOut,
    operation_id="queueTicketPage",
    summary="One ticket as its page shows it, found by the page's unguessable link",
)
def ticket_page(
    page_token: str, request: Request, response: Response, db: DbSession
) -> TicketPageOut:
    """The data behind a patient's ticket page (Issue 68): number, clinic, place in line, wait range.

    No sign-in: the link is the credential, and it lets its holder follow the ticket and nothing more.
    The ticket's own patient, signed in on this browser, is also given ``cancel_url``. An id, a number,
    a reference code or an unknown token is the same 404, so a ticket cannot be probed for.
    """
    ticket = find_by_page_token(db, page_token)
    if ticket is None:
        raise NotFoundError("No such ticket.", code="http.not_found")
    response.headers["Cache-Control"] = "no-store"
    return page_state(db, ticket, viewer_patient_id=signed_in_patient_id(request, db))


@router.post(
    "/patients/me/tickets/{ticket_id}/cancel",
    response_model=CancelOut,
    operation_id="queueCancelMyTicket",
    summary="Give my place back",
)
def cancel_my_ticket(
    ticket_id: str, payload: CancelIn, patient: PatientJoining, db: DbSession
) -> CancelOut:
    """Cancel one of the patient's own waiting tickets. After being called: 409, speak to reception."""
    result = cancel_own_ticket(
        db, patient.id, ticket_id, channel=PatientChannel.WEB, reason=payload.reason
    )
    db.commit()
    return _cancelled(result)


@router.post(
    "/sites/{site_id}/tickets/{ticket_id}/cancel",
    response_model=CancelOut,
    operation_id="queueCancelTicketAtDesk",
    summary="Cancel a ticket at the front desk",
)
def cancel_at_desk(
    ticket_id: str, payload: CancelIn, access: TicketsIssue, db: DbSession
) -> CancelOut:
    """Cancel a ticket for a patient standing at reception, including one already called."""
    ticket = get_in_site_or_404(db, Ticket, ticket_id, access)
    result = cancel_ticket(
        db,
        ticket.id,
        channel=PatientChannel.WALK_IN,
        actor=_staff(access),
        reason=payload.reason,
    )
    db.commit()
    return _cancelled(result)


def _cancelled(result: CancelResult) -> CancelOut:
    """The answer to a cancellation, with a sentence for the patient."""
    behind = result.moved_up
    return CancelOut(
        ticket=TicketOut.of(result.ticket),
        moved_up=behind,
        message=(
            f"Ticket {result.ticket.number} is cancelled. Thank you for giving your place back"
            + (
                f": {behind} {'person' if behind == 1 else 'people'} moved up."
                if behind
                else "."
            )
        ),
    )


def _staff(access: SiteAccess) -> Actor:
    """The signed-in staff member, as the lifecycle's actor."""
    return Actor(
        kind=ActorKind.STAFF, label=access.user.email, user_id=str(access.user.id)
    )


@router.post(
    "/sites/{site_id}/tickets/{ticket_id}/transitions",
    response_model=TicketOut,
    operation_id="queueTransitionTicket",
    summary="Move a ticket to another status",
)
def move_ticket(
    ticket_id: str,
    payload: TransitionIn,
    access: TicketsIssue,
    db: DbSession,
    request_key: RequestKey = None,
) -> TicketOut:
    """Apply one legal move. An illegal move, one decided on a stale screen, or a cancellation,
    transfer or undone call (which have their own routes) is a 409."""
    ticket = get_in_site_or_404(db, Ticket, ticket_id, access)
    moved = run_once(
        db,
        access,
        request_key,
        operation="transition",
        target=f"{ticket.id}:{payload.to.value}",
        act=lambda: staff_move(
            db,
            ticket.id,
            payload.to,
            actor=_staff(access),
            expected_status=payload.expected_status,
        ),
    )
    return TicketOut.of(moved)


@router.post(
    "/sites/{site_id}/queues/{queue_id}/tickets/call-next",
    response_model=TicketOut,
    operation_id="queueCallNext",
    summary="Call the next waiting ticket in a queue",
)
def call_next_ticket(
    queue_id: str, access: CallNext, db: DbSession, request_key: RequestKey = None
) -> TicketOut:
    """Call the next patient. Two staff pressing it at once call two different patients; one person
    pressing it twice with the same ``Idempotency-Key`` calls one."""
    queue = get_queue(db, access, queue_id)
    if queue is None:
        raise site_not_found()
    called = run_once(
        db,
        access,
        request_key,
        operation="call_next",
        target=queue.id,
        act=lambda: call_next(db, queue, actor=_staff(access)),
    )
    return TicketOut.of(called)


@router.post(
    "/sites/{site_id}/tickets/{ticket_id}/undo-call",
    response_model=TicketOut,
    operation_id="queueUndoCall",
    summary="Undo a call made by mistake, within the undo window",
)
def undo_ticket_call(
    ticket_id: str, access: CallNext, db: DbSession, request_key: RequestKey = None
) -> TicketOut:
    """Put a patient called by mistake back in their place. Only a ``called`` ticket, and only within
    ``QUEUE_CALL_UNDO_SECONDS`` of the call; the audit trail keeps the call and its undoing."""
    ticket = get_in_site_or_404(db, Ticket, ticket_id, access)
    undone = run_once(
        db,
        access,
        request_key,
        operation="undo_call",
        target=ticket.id,
        act=lambda: undo_call(db, ticket.id, actor=_staff(access)),
    )
    return TicketOut.of(undone)


@router.get(
    "/sites/{site_id}/queues/{queue_id}/tickets/next",
    response_model=TicketOut | None,
    operation_id="queueNextTicket",
    summary="Who would be called next in a queue",
)
def next_ticket(queue_id: str, access: CallPeek, db: DbSession) -> TicketOut | None:
    """The waiting ticket *Call next* would call now, or ``null``. Reads only; moves nothing."""
    queue = get_queue(db, access, queue_id)
    if queue is None:
        raise site_not_found()
    upcoming = (
        db.execute(
            site_day_select(
                access,
                business_date(),
                queue_id=queue.id,
                statuses=(TicketStatus.WAITING,),
            )
            .order_by(None)
            .order_by(*CALL_ORDER)
            .limit(1)
        )
        .scalars()
        .first()
    )
    return TicketOut.of(upcoming) if upcoming is not None else None


@router.post(
    "/sites/{site_id}/tickets/{ticket_id}/transfer",
    response_model=TransferOut,
    operation_id="queueTransferTicket",
    summary="Move a patient to another queue without rejoining",
)
def transfer(
    ticket_id: str, payload: TransferIn, access: TicketsIssue, db: DbSession
) -> TransferOut:
    """Transfer a waiting or in-progress ticket to another queue at this clinic, keeping the visit."""
    ticket = get_in_site_or_404(db, Ticket, ticket_id, access)
    # The patient must be in a queue the caller acts in; where they go is any queue at the clinic,
    # so a nurse can send someone on to the pharmacy without being on the pharmacy (Issue 53).
    target = get_queue(db, access.whole_site(), payload.queue_id)
    if target is None:
        raise site_not_found()
    result = transfer_ticket(
        db, ticket.id, target, actor=_staff(access), reason=payload.reason
    )
    db.commit()
    return TransferOut(
        from_ticket=TicketOut.of(result.from_ticket),
        ticket=TicketOut.of(result.ticket),
        visit_id=result.ticket.visit_id,
        waiting_ahead=result.waiting_ahead,
        wait=WaitOut.of(result.wait),
        message=(
            f"Moved to {target.name} as {result.ticket.number}. "
            f"Expected wait {result.wait.label}."
        ),
    )


def _visit_out(summary: VisitSummary) -> VisitOut:
    """The wire form of a visit summary."""
    return VisitOut(
        id=summary.visit.id,
        site_id=summary.visit.site_id,
        started_at=summary.started_at,
        ended_at=summary.ended_at,
        total_minutes=(
            round(summary.total_minutes, 1)
            if summary.total_minutes is not None
            else None
        ),
        legs=[
            VisitLegOut(
                ticket_id=leg.id,
                queue_id=leg.queue_id,
                number=leg.number,
                status=leg.status_enum,
                joined_at=stored_sast(leg.joined_at),
                called_at=stored_sast(leg.called_at) if leg.called_at else None,
                completed_at=stored_sast(leg.completed_at)
                if leg.completed_at
                else None,
                transferred_from_id=leg.transferred_from_id,
            )
            for leg in summary.legs
        ],
    )


@router.get(
    "/sites/{site_id}/visits/{visit_id}",
    response_model=VisitOut,
    operation_id="queueVisit",
    summary="One patient's journey through the clinic",
)
def visit(visit_id: str, access: TicketsRead, db: DbSession) -> VisitOut:
    """A visit's legs in order, and its total time once it has ended."""
    return _visit_out(visit_summary(db, access, visit_id))


@router.get(
    "/sites/{site_id}/visits",
    response_model=VisitListOut,
    operation_id="queueVisits",
    summary="The clinic's visits today",
)
def visits(access: TicketsRead, db: DbSession) -> VisitListOut:
    """Every visit that began today at this clinic, earliest first, with its legs."""
    today = business_date()
    start, end = business_day_bounds(today)
    ids = (
        db.execute(
            scoped_select(Visit, access)
            .with_only_columns(Visit.id)
            .where(Visit.started_at >= start, Visit.started_at < end)
            .order_by(Visit.started_at)
        )
        .scalars()
        .all()
    )
    items = [_visit_out(visit_summary(db, access, visit_id)) for visit_id in ids]
    return VisitListOut(
        site_id=access.site_id, service_day=today, total=len(items), items=items
    )


#: Moving a patient forward for clinical priority (Issue 46).
PriorityOverride = Annotated[
    SiteAccess, Depends(require_site_access("queues.tickets.priority", "update"))
]
#: Reading the override trail.
PriorityRead = Annotated[
    SiteAccess, Depends(require_site_access("queues.tickets.priority", "read"))
]
#: The clinic's reports: the override counts are one (Issue 90 builds the rest).
ReportsRead = Annotated[
    SiteAccess, Depends(require_site_access("sites.reports", "read"))
]


@router.post(
    "/sites/{site_id}/tickets/{ticket_id}/priority",
    response_model=PriorityOut,
    operation_id="queuePriorityOverride",
    summary="Move a waiting patient forward for clinical priority, with a reason",
)
def priority_override(
    ticket_id: str, payload: PriorityIn, access: PriorityOverride, db: DbSession
) -> PriorityOut:
    """Place the patient just ahead of another waiting ticket. A reason code is required."""
    ticket = get_in_site_or_404(db, Ticket, ticket_id, access)
    ahead_of_ticket = get_in_site_or_404(db, Ticket, payload.ahead_of_ticket_id, access)
    result = override_priority(
        db,
        ticket.id,
        ahead_of_ticket_id=ahead_of_ticket.id,
        reason=payload.reason,
        note=payload.note,
        actor=_staff(access),
    )
    db.commit()
    return PriorityOut(
        ticket=TicketOut.of(result.ticket),
        reorder=ReorderOut.of(result.reorder),
        waiting_ahead=result.reorder.position_after - 1,
    )


@router.get(
    "/sites/{site_id}/tickets/reorders",
    response_model=ReorderTrailOut,
    operation_id="queueReorderTrail",
    summary="The clinic's priority overrides today",
)
def reorder_trail_today(access: PriorityRead, db: DbSession) -> ReorderTrailOut:
    """Every override made today at this clinic, newest first, with who, why and the places."""
    today = business_date()
    rows = reorder_trail(db, access, today)
    return ReorderTrailOut(
        site_id=access.site_id,
        service_day=today,
        total=len(rows),
        items=[ReorderOut.of(row) for row in rows],
    )


@router.get(
    "/sites/{site_id}/tickets/reorders/counts",
    response_model=OverrideCountsOut,
    operation_id="queueOverrideCounts",
    summary="How often each staff member used a priority override (not a ranking)",
)
def override_counts_report(
    access: ReportsRead,
    db: DbSession,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> OverrideCountsOut:
    """Counts per staff member over a period (the last 30 days by default), listed by name."""
    last = end or business_date()
    first = start or last - timedelta(days=29)
    if first > last:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "start is after end."
        )
    counts = override_counts(db, access, first, last)
    return OverrideCountsOut(
        site_id=access.site_id,
        start=first,
        end=last,
        items=[
            StaffOverrideCountOut(staff=c.staff, overrides=c.overrides) for c in counts
        ],
        note=COUNTS_ARE_NOT_RANKINGS,
    )
