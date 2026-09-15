"""HTTP routes for booking an appointment (Issue 81): what a patient may book, and their bookings.

* ``GET /clinics/{site_id}/appointments/availability`` is public, like a clinic's queues in discovery: it
  lists bookable times at a clinic a patient may be shown, and nothing about anyone else.
* ``POST /clinics/{site_id}/appointments`` books, and ``/patients/me/appointments…`` lists, moves and cancels
  the signed-in patient's own bookings. The web is the first channel; USSD and WhatsApp call the same
  service functions (:mod:`src.modules.appointments.booking`) with their own source.
* ``GET /sites/{site_id}/appointments/bookings`` is the front desk's list for a day.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from src.api.rbac_deps import require_patient
from src.commons.enums import TicketSource
from src.commons.exceptions import NotFoundError
from src.commons.time import business_date, now_sast
from src.core.site_scope import SiteAccess, require_site_access
from src.database.models import Patient
from src.database.session import get_db
from src.modules.appointments import booking, service
from src.modules.appointments.schemas import (
    BookIn,
    BookingListOut,
    BookingOut,
    PublicDayOut,
    PublicQueueDayOut,
    PublicSlotOut,
    RescheduleIn,
)
from src.modules.sites.hours import published_schedules

router = APIRouter(tags=["appointments"])

DbSession = Annotated[Session, Depends(get_db)]
PatientBooking = Annotated[Patient, Depends(require_patient("patients.self", "update"))]
PatientReading = Annotated[Patient, Depends(require_patient("patients.self", "read"))]
AppointmentsRead = Annotated[
    SiteAccess, Depends(require_site_access("appointments", "read"))
]


@router.get(
    "/clinics/{site_id}/appointments/availability",
    response_model=PublicDayOut,
    operation_id="appointmentsPublicAvailability",
    summary="The times a patient can book at a clinic on one day",
)
def public_availability(
    site_id: str,
    response: Response,
    db: DbSession,
    day: Annotated[date | None, Query()] = None,
) -> PublicDayOut:
    """Only times with room that the clinic offers now; the same rule a booking is refused by."""
    moment = now_sast()
    day = day or business_date(moment)
    rows = service.published_rows([site_id])
    opening = published_schedules(
        db, [site_id], from_day=day - service.PLANNING_MARGIN, horizon_days=3
    ).get(site_id)
    if opening is None:
        raise NotFoundError("No such clinic.", code="http.not_found")
    view = service.day_availability(
        db,
        rows,
        site_id=site_id,
        queues=service.bookable_queues(db, rows),
        opening=opening,
        day=day,
        moment=moment,
    )
    response.headers["Cache-Control"] = "no-store"
    return PublicDayOut(
        site_id=site_id,
        day=day,
        as_of=moment,
        queues=[
            PublicQueueDayOut(
                queue_id=queue.queue_id,
                queue_name=queue.queue_name,
                slots=[
                    PublicSlotOut(
                        id=slot.id,
                        starts_at=slot.starts_at,
                        ends_at=slot.ends_at,
                        service_id=slot.service_id,
                    )
                    for slot in queue.slots
                    if slot.refusal is None and slot.remaining > 0
                ],
            )
            for queue in view.queues
            if queue.slots
        ],
    )


@router.post(
    "/clinics/{site_id}/appointments",
    response_model=BookingOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="appointmentsBook",
    summary="Book an appointment as the signed-in patient",
)
def book(
    site_id: str, payload: BookIn, patient: PatientBooking, db: DbSession
) -> BookingOut:
    """Book a time. 409 ``appointments.slot.<reason>`` when it is not on offer or has no room."""
    booked = booking.book(
        db,
        site_id=site_id,
        slot_id=payload.slot_id,
        patient=patient,
        source=TicketSource.WEB,
        actor=f"patient:{patient.id}",
    )
    db.commit()
    return booking.view_by_id(db, booked.appointment.id, message=booked.confirmation)


@router.get(
    "/patients/me/appointments",
    response_model=BookingListOut,
    operation_id="appointmentsMine",
    summary="My bookings from today on",
)
def my_bookings(patient: PatientReading, db: DbSession) -> BookingListOut:
    """The signed-in patient's bookings at every clinic, soonest first."""
    rows = booking.patient_bookings(db, patient.id)
    return BookingListOut(
        total=len(rows),
        items=[booking.booking_view(db, *row) for row in rows],
    )


@router.post(
    "/patients/me/appointments/{appointment_id}/reschedule",
    response_model=BookingOut,
    operation_id="appointmentsReschedule",
    summary="Move my booking to another time",
)
def reschedule(
    appointment_id: str, payload: RescheduleIn, patient: PatientBooking, db: DbSession
) -> BookingOut:
    """Move a booking at the same clinic. The original time is free at once; a refused move changes nothing."""
    moved = booking.reschedule(
        db,
        patient=patient,
        appointment_id=appointment_id,
        slot_id=payload.slot_id,
        actor=f"patient:{patient.id}",
    )
    db.commit()
    return booking.view_by_id(db, moved.appointment.id, message=moved.confirmation)


@router.post(
    "/patients/me/appointments/{appointment_id}/cancel",
    response_model=BookingOut,
    operation_id="appointmentsCancel",
    summary="Cancel my booking",
)
def cancel(appointment_id: str, patient: PatientBooking, db: DbSession) -> BookingOut:
    """Cancel a booking. The time is free for someone else at once."""
    cancelled = booking.cancel(
        db,
        patient=patient,
        appointment_id=appointment_id,
        actor=f"patient:{patient.id}",
    )
    db.commit()
    return booking.view_by_id(db, cancelled.id)


@router.get(
    "/sites/{site_id}/appointments/bookings",
    response_model=BookingListOut,
    operation_id="appointmentsSiteBookings",
    summary="A clinic's bookings on one day",
)
def site_bookings(
    access: AppointmentsRead,
    db: DbSession,
    day: Annotated[date | None, Query()] = None,
) -> BookingListOut:
    """The front desk's list: every booking that day, whatever became of it, in time order."""
    rows = booking.site_bookings(db, access, day or business_date(now_sast()))
    return BookingListOut(
        total=len(rows),
        items=[booking.booking_view(db, *row) for row in rows],
    )
