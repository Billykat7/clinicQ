"""Repeating collections: the clinic's list, and the patient's one tap (Issue 85).

* the clinic sets a patient's repeat up, changes it and stops it (``appointments``: the manager's own
  grant, read by the front desk who answer the phone about it);
* the patient's reminder carries ``POST /api/v1/collections/joins/{token}``, which takes their place in
  the collection queue in one interaction, with no sign-in: the token is the whole of the credential,
  sent only in their own message, exactly like the ticket page's token (Issue 68) and the appointment
  reminder's reply (Issue 82);
* the adherence report is what Issue 90's dashboard reads.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from src.commons.enums import AuditAction, AuditEntityType
from src.commons.phone import InvalidPhoneNumberError, mask_phone, normalize_phone
from src.commons.time import business_date, now_sast
from src.core.audit import record_audit_event
from src.core.site_scope import SiteAccess, require_site_access, scoped_select
from src.database.models import ChronicSchedule, Patient, Queue
from src.database.session import get_db
from src.modules.appointments import chronic
from src.modules.appointments.chronic_schemas import (
    AdherenceOut,
    AdherenceReportOut,
    CollectionJoinOut,
    ScheduleIn,
    ScheduleListOut,
    ScheduleOut,
)
from src.modules.discovery.analytics import DEFAULT_REPORT_DAYS, MAX_REPORT_DAYS
from src.modules.patients.service import find_patient, get_or_create_patient

router = APIRouter(tags=["appointments"])

DbSession = Annotated[Session, Depends(get_db)]
# A repeating collection is part of the clinic's appointment book (Issue 80's resource): the front desk
# and the nurses read it, because they are the ones a patient asks about it; the manager shapes it.
ChronicRead = Annotated[
    SiteAccess, Depends(require_site_access("appointments", "read"))
]
ChronicUpdate = Annotated[
    SiteAccess, Depends(require_site_access("appointments", "update"))
]
ReportsRead = Annotated[
    SiteAccess, Depends(require_site_access("sites.reports", "read"))
]


def _view(db: Session, schedule: ChronicSchedule) -> ScheduleOut:
    """One schedule as the clinic's own screen reads it. Never the patient's number."""
    patient = db.get(Patient, schedule.patient_id)
    queue = db.get(Queue, schedule.queue_id)
    return ScheduleOut(
        id=schedule.id,
        patient_id=schedule.patient_id,
        # The number the desk typed, masked. A patient's name is shown only where consent allows it
        # (Issue 21), and a staff list of who is due for medication is not that place.
        patient_phone=mask_phone(patient.phone_e164)
        if patient and patient.phone_e164
        else "",
        queue_id=schedule.queue_id,
        queue_name=queue.name if queue else "",
        service=schedule.service,
        interval_days=schedule.interval_days,
        grace_days=schedule.grace_days,
        next_due_on=schedule.next_due_on,
        last_collected_on=schedule.last_collected_on,
        reminded_for=schedule.reminded_for,
        followed_up_for=schedule.followed_up_for,
        active=schedule.is_active,
    )


def _list(db: Session, access: SiteAccess, *, include_stopped: bool) -> ScheduleListOut:
    rows = chronic.schedules_at(db, access, include_stopped=include_stopped)
    return ScheduleListOut(
        site_id=access.site_id, total=len(rows), items=[_view(db, row) for row in rows]
    )


@router.get(
    "/sites/{site_id}/collection-schedules",
    response_model=ScheduleListOut,
    operation_id="collectionsList",
    summary="This clinic's repeating collections",
)
def list_schedules(
    access: ChronicRead,
    db: DbSession,
    include_stopped: Annotated[bool, Query()] = False,
) -> ScheduleListOut:
    """The clinic's repeating collections, soonest due first; stopped ones only when asked for."""
    return _list(db, access, include_stopped=include_stopped)


@router.post(
    "/sites/{site_id}/collection-schedules",
    response_model=ScheduleListOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="collectionsCreate",
    summary="Set up or correct a patient's repeating collection",
)
def create_schedule(
    payload: ScheduleIn, request: Request, access: ChronicUpdate, db: DbSession
) -> ScheduleListOut:
    """Set a repeat up for the patient on that number; setting one up twice corrects the first.

    The patient's record is created on first use, as a walk-in's is: a collection is something the
    clinic knows about the person in front of them.
    """
    try:
        phone = normalize_phone(payload.phone)
    except InvalidPhoneNumberError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error
    queue = db.execute(
        scoped_select(Queue, access).where(Queue.id == payload.queue_id)
    ).scalar_one_or_none()
    if queue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such queue at this clinic.")
    patient = find_patient(db, phone)
    if patient is None:
        patient, _ = get_or_create_patient(db, phone)
    schedule = chronic.create(
        db,
        access,
        patient=patient,
        queue=queue,
        next_due_on=payload.next_due_on,
        interval_days=payload.interval_days,
        grace_days=payload.grace_days,
        service=payload.service,
        actor=access.user.email,
    )
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.PATIENT,
        entity_id=patient.id,
        actor=access.user.email,
        actor_id=str(access.user.id),
        site_id=access.site_id,
        context=(
            f"collection every {schedule.interval_days} days in {queue.name}, "
            f"next due {schedule.next_due_on.isoformat()}"
        ),
    )
    db.commit()
    return _list(db, access, include_stopped=False)


@router.post(
    "/sites/{site_id}/collection-schedules/{schedule_id}/stop",
    response_model=ScheduleListOut,
    operation_id="collectionsStop",
    summary="Stop a patient's collection reminders",
)
def stop_schedule(
    schedule_id: str, access: ChronicUpdate, db: DbSession
) -> ScheduleListOut:
    """Stop the reminders. Nothing more is sent; the record of what was stays."""
    schedule = chronic.schedule_at(db, access, schedule_id)
    chronic.stop(db, schedule, by=access.user.email)
    db.commit()
    return _list(db, access, include_stopped=False)


@router.post(
    "/collections/joins/{token}",
    response_model=CollectionJoinOut,
    operation_id="collectionsJoin",
    summary="Take a place in the collection queue from a reminder",
)
def join_from_reminder(token: str, db: DbSession) -> CollectionJoinOut:
    """One interaction: the reminder's own button, with no page and no sign-in (Issue 85).

    404 for an unknown token, 409 when the reminders were stopped, and the queue's own refusal (the
    clinic is closed, the day is full) when there is no place to take.
    """
    schedule = chronic.by_token(db, token)
    joined = chronic.join_now(db, schedule)
    db.commit()
    return CollectionJoinOut(
        number=joined.number,
        queue_name=joined.queue_name,
        room_label=joined.room_label,
        waiting_ahead=joined.waiting_ahead,
        already=joined.already,
        message=(
            f"You already have a place: {joined.number} in {joined.queue_name}."
            if joined.already
            else f"You have a place in the queue: {joined.number} in {joined.queue_name}."
        ),
    )


@router.get(
    "/sites/{site_id}/reports/collections",
    response_model=AdherenceReportOut,
    operation_id="collectionsAdherence",
    summary="How many collections happen on time at this clinic",
)
def adherence_report(
    access: ReportsRead,
    db: DbSession,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> AdherenceReportOut:
    """Per collection queue: schedules, cycles due, collected on time, collected late, and missed.

    What Issue 90's adherence dashboard reads. The last 30 days by default, at most 366.
    """
    last = end or business_date(now_sast())
    first = start or last - timedelta(days=DEFAULT_REPORT_DAYS - 1)
    if first > last or (last - first).days + 1 > MAX_REPORT_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"The start must come before the end, and a report covers at most {MAX_REPORT_DAYS} days.",
        )
    rows = chronic.adherence(db, access, start=first, end=last)
    return AdherenceReportOut(
        site_id=access.site_id,
        start=first,
        end=last,
        queues=[
            AdherenceOut(
                queue_id=row.queue_id,
                queue_name=row.queue_name,
                schedules=row.schedules,
                due=row.due,
                collected_on_time=row.collected_on_time,
                collected_late=row.collected_late,
                missed=row.missed,
            )
            for row in rows
        ],
    )
