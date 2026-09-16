"""HTTP routes for booking an appointment (Issue 81): what a patient may book, and their bookings.

* ``GET /clinics/{site_id}/appointments/availability`` is public, like a clinic's queues in discovery: it
  lists bookable times at a clinic a patient may be shown, and nothing about anyone else.
* ``POST /clinics/{site_id}/appointments`` books, and ``/patients/me/appointments…`` lists, moves and cancels
  the signed-in patient's own bookings. The web is the first channel; USSD and WhatsApp call the same
  service functions (:mod:`src.modules.appointments.booking`) with their own source.
* ``GET /sites/{site_id}/appointments/bookings`` is the front desk's list for a day.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from src.api.rbac_deps import require_patient
from src.commons.enums import BookingReply, BookingReplyChannel, TicketSource
from src.commons.exceptions import NotFoundError
from src.commons.time import business_date, now_sast
from src.core.site_scope import SiteAccess, require_site_access
from src.database.models import Patient
from src.database.session import get_db
from src.modules.appointments import booking, reminders, service
from src.modules.appointments.schemas import (
    BookIn,
    BookingListOut,
    BookingOut,
    BookingReplyIn,
    BookingReplyOut,
    PublicDayOut,
    PublicQueueDayOut,
    PublicSlotOut,
    ReminderOutcomeOut,
    ReminderReportOut,
    RescheduleIn,
)
from src.modules.discovery.analytics import DEFAULT_REPORT_DAYS, MAX_REPORT_DAYS
from src.modules.patients import proxy as proxy_links
from src.modules.sites.hours import published_schedules

router = APIRouter(tags=["appointments"])

DbSession = Annotated[Session, Depends(get_db)]
PatientBooking = Annotated[Patient, Depends(require_patient("patients.self", "update"))]
PatientReading = Annotated[Patient, Depends(require_patient("patients.self", "read"))]
ReportsRead = Annotated[
    SiteAccess, Depends(require_site_access("sites.reports", "read"))
]
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
    """Book a time. 409 ``appointments.slot.<reason>`` when it is not on offer or has no room.

    A patient who acts for a dependant (Issue 84) names them in ``for_patient_id``; the booking, its
    reference and the ticket it becomes are the dependant's.
    """
    seen, proxy = proxy_links.patient_or_dependant(db, patient, payload.for_patient_id)
    booked = booking.book(
        db,
        site_id=site_id,
        slot_id=payload.slot_id,
        patient=seen,
        source=TicketSource.WEB,
        actor=f"patient:{patient.id}",
        proxy=proxy,
    )
    if proxy is not None:
        proxy_links.record_action(
            db, proxy, seen, f"booked {booked.reference}", site_id=site_id
        )
    db.commit()
    return booking.view_by_id(db, booked.appointment.id, message=booked.confirmation)


@router.get(
    "/patients/me/appointments",
    response_model=BookingListOut,
    operation_id="appointmentsMine",
    summary="My bookings from today on",
)
def my_bookings(
    patient: PatientReading,
    db: DbSession,
    for_patient_id: Annotated[str | None, Query(max_length=36)] = None,
) -> BookingListOut:
    """The signed-in patient's bookings at every clinic, soonest first.

    ``for_patient_id`` reads a dependant's bookings instead (Issue 84), through the same one gate.
    """
    seen, _ = proxy_links.patient_or_dependant(db, patient, for_patient_id)
    rows = booking.patient_bookings(db, seen.id)
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
    seen, proxy = proxy_links.patient_or_dependant(db, patient, payload.for_patient_id)
    moved = booking.reschedule(
        db,
        patient=seen,
        appointment_id=appointment_id,
        slot_id=payload.slot_id,
        actor=f"patient:{patient.id}",
    )
    if proxy is not None:
        proxy_links.record_action(
            db,
            proxy,
            seen,
            f"moved {moved.reference}",
            site_id=moved.appointment.site_id,
        )
    db.commit()
    return booking.view_by_id(db, moved.appointment.id, message=moved.confirmation)


@router.post(
    "/patients/me/appointments/{appointment_id}/cancel",
    response_model=BookingOut,
    operation_id="appointmentsCancel",
    summary="Cancel my booking",
)
def cancel(
    appointment_id: str,
    patient: PatientBooking,
    db: DbSession,
    for_patient_id: Annotated[str | None, Query(max_length=36)] = None,
) -> BookingOut:
    """Cancel a booking. The time is free for someone else at once."""
    seen, proxy = proxy_links.patient_or_dependant(db, patient, for_patient_id)
    cancelled = booking.cancel(
        db,
        patient=seen,
        appointment_id=appointment_id,
        actor=f"patient:{patient.id}",
    )
    if proxy is not None:
        proxy_links.record_action(
            db, proxy, seen, "cancelled a booking", site_id=cancelled.site_id
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


@router.post(
    "/appointments/replies/{token}",
    response_model=BookingReplyOut,
    operation_id="appointmentsReply",
    summary="Confirm or cancel a booking from a reminder's own button",
)
def reply_by_button(
    token: str, payload: BookingReplyIn, db: DbSession
) -> BookingReplyOut:
    """What a web push's Confirm and Cancel buttons send, with no page and no sign-in (Issue 82).

    The token is the secret, sent only in that patient's reminder. A cancellation frees the time at once. Replying
    to a booking that has already become a ticket, or was cancelled, changes nothing.
    """
    appointment = reminders.by_token(db, token)
    applied = reminders.apply(
        db, appointment, payload.reply, BookingReplyChannel.WEB_PUSH
    )
    db.commit()
    return BookingReplyOut(
        reply=payload.reply,
        applied=applied,
        message=(
            "Thank you, your booking is confirmed."
            if applied and payload.reply is BookingReply.CONFIRM
            else "Your booking is cancelled. The time is free for someone else."
            if applied
            else "This booking can no longer be changed."
        ),
    )


@router.get(
    "/sites/{site_id}/reports/reminders",
    response_model=ReminderReportOut,
    operation_id="appointmentsReminderReport",
    summary="Attendance with and without appointment reminders",
)
def reminder_report(
    access: ReportsRead,
    db: DbSession,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> ReminderReportOut:
    """Per number of reminders sent: bookings, confirmations, cancellations by reply, attendance and no-shows.

    What Issue 93's no-show analysis reads. The last 30 days by default, at most 366.
    """
    last = end or business_date(now_sast())
    first = start or last - timedelta(days=DEFAULT_REPORT_DAYS - 1)
    if first > last or (last - first).days + 1 > MAX_REPORT_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"The start must come before the end, and a report covers at most {MAX_REPORT_DAYS} days.",
        )
    groups = reminders.reminder_outcomes(db, access, start=first, end=last)
    return ReminderReportOut(
        site_id=access.site_id,
        start=first,
        end=last,
        groups=[ReminderOutcomeOut(**asdict(group)) for group in groups],
    )
