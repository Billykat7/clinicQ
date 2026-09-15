"""Appointment reminders and replies, with the real sweep, notification service and SMS webhook (Issue 82).

At fixed times the day after tomorrow, against the queue fixture's clinic A:

* **both reminders, once each, on the patient's preferred transport**, and none inside a window the booking was made
  in;
* **a reply confirms or cancels without opening any app**: an SMS keyword, or a web push's own button; a cancellation
  gives the time back in the same request;
* ``CANCEL`` from a patient with no reminded booking still means STOP, as it always has;
* **a patient who has already checked in gets no reminder**;
* **quiet hours and opt-outs apply** because the reminder goes through the notification service;
* **what Issue 93 needs** is recorded and counted per clinic.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette import status

from src.commons.enums import (
    ActorKind,
    AppointmentStatus,
    ConsentPurpose,
    NotificationChannel,
    NotificationStatus,
    NotificationTemplate,
    PatientChannel,
    PatientEvent,
    TicketSource,
    TicketStatus,
)
from src.commons.time import APP_TIMEZONE, business_date, now_sast, stored_sast
from src.core.config import get_settings
from src.database.models import (
    Appointment,
    AppointmentSlot,
    Notification,
    Patient,
    PatientNotificationPreference,
    Queue,
    Site,
)
from src.modules.appointments import capacity, conversion, reminders
from src.modules.notifications import template_registry
from src.modules.notifications.transports import NoopTransport, use_transports
from src.modules.notifications.transports.webpush import payload_for
from src.modules.patients.consent import record_consent
from src.modules.queue.lifecycle import Actor, transition_ticket
from src.modules.queue.service import join_queue
from src.modules.sites.hours import published_schedules
from tests.factories import PatientFactory
from tests.integration.queue.conftest import SITE_A, queue_settings

_TOKEN = "rm" * 22


@pytest.fixture
def everywhere() -> Iterator[None]:
    """Web push and SMS both reach every patient."""
    with use_transports(
        NoopTransport(channel=NotificationChannel.WEB_PUSH, free=True, address="sub"),
        NoopTransport(channel=NotificationChannel.SMS, address="+27820000001"),
    ):
        yield


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(
        business_date(now_sast()) + timedelta(days=2),
        time(hour, minute),
        tzinfo=APP_TIMEZONE,
    )


def _booking(
    desk: SimpleNamespace, starts: datetime, *, booked_at: datetime
) -> tuple[str, str]:
    """A consenting patient's booking at ``starts``, made at ``booked_at``: ``(appointment id, patient id)``."""
    with desk.session() as db:
        patient = PatientFactory.create(db)
        record_consent(
            db,
            patient,
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
        slot = AppointmentSlot(
            site_id=SITE_A,
            queue_id=desk.triage.id,
            starts_at=starts,
            ends_at=starts + timedelta(minutes=15),
            service_day=business_date(starts),
            capacity=2,
        )
        db.add(slot)
        db.flush()
        appointment = capacity.claim_place(
            db,
            slot=slot,
            queue=db.get_one(Queue, desk.triage.id),
            patient_id=patient.id,
            moment=booked_at,
        )
        db.commit()
        return appointment.id, patient.id


def _sweep(desk: SimpleNamespace, moment: datetime) -> reminders.RemindersSent:
    with desk.session() as db:
        sent = reminders.send_due(db, moment=moment)
        db.commit()
    return sent


def _messages(desk: SimpleNamespace, appointment_id: str) -> list[Notification]:
    with desk.session() as db:
        return list(
            db.execute(
                select(Notification)
                .where(Notification.dedupe_key.startswith(appointment_id))
                .order_by(Notification.created_at)
            ).scalars()
        )


def _gateway(desk: SimpleNamespace):
    desk.app.dependency_overrides[get_settings] = lambda: queue_settings(
        sms_webhook_token=_TOKEN
    )
    client = TestClient(desk.app)

    def reply(phone: str, text: str, message_id: str) -> str:
        answer = client.post(
            f"/api/v1/webhooks/sms/africastalking/{_TOKEN}/inbound",
            content=urlencode(
                {"id": message_id, "from": phone, "text": text, "to": "12345"}
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert answer.status_code == status.HTTP_200_OK, answer.text
        return answer.json()["outcome"]

    return reply


def test_both_reminders_go_once_each_on_the_preferred_transport(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """How to verify, step 1: at 24 hours and at 2 hours, on the channel the patient chose, never twice."""
    starts = _at(10)
    appointment_id, patient_id = _booking(
        desk, starts, booked_at=starts - timedelta(days=3)
    )
    with desk.session() as db:
        db.add(
            PatientNotificationPreference(
                patient_id=patient_id, preferred_channel=NotificationChannel.SMS.value
            )
        )
        db.commit()

    early = _sweep(desk, starts - timedelta(hours=24, minutes=1))
    day_before = _sweep(desk, starts - timedelta(hours=23, minutes=59))
    again = _sweep(desk, starts - timedelta(hours=3))
    two_hours = _sweep(desk, starts - timedelta(hours=2))
    later = _sweep(desk, starts - timedelta(minutes=30))

    assert (early.day_before, early.two_hours) == ([], [])
    assert day_before.day_before == [appointment_id] and again.day_before == []
    assert two_hours.two_hours == [appointment_id] and later.two_hours == []
    sent = [
        m
        for m in _messages(desk, appointment_id)
        if m.event != PatientEvent.BOOKED.value
    ]
    assert [m.event for m in sent] == [
        PatientEvent.REMINDER_24H.value,
        PatientEvent.REMINDER_2H.value,
    ]
    assert {m.channel for m in sent} == {NotificationChannel.SMS.value}
    with desk.session() as db:
        stored = db.get_one(Appointment, appointment_id)
        assert stored.reminded_24h_at is not None and stored.reminded_2h_at is not None


def test_a_booking_made_inside_a_window_is_not_reminded_for_it(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """Booked three hours before: no day-before reminder, only the two-hour one."""
    starts = _at(11)
    appointment_id, _ = _booking(desk, starts, booked_at=starts - timedelta(hours=3))

    inside_day = _sweep(desk, starts - timedelta(hours=2, minutes=30))
    two_hours = _sweep(desk, starts - timedelta(hours=1, minutes=59))

    assert inside_day.day_before == [] and two_hours.two_hours == [appointment_id]


def test_an_sms_reply_confirms_or_cancels_and_cancel_frees_the_time_at_once(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """How to verify, step 2: CANCEL frees the slot in the webhook's own request; CONFIRM records the confirmation."""
    reply = _gateway(desk)
    starts = _at(9)
    keep, keep_patient = _booking(desk, starts, booked_at=starts - timedelta(days=2))
    drop, drop_patient = _booking(
        desk, starts + timedelta(hours=1), booked_at=starts - timedelta(days=2)
    )
    _sweep(desk, starts - timedelta(hours=20))
    with desk.session() as db:
        keep_phone = db.get_one(Patient, keep_patient).phone_e164
        drop_phone = db.get_one(Patient, drop_patient).phone_e164
        drop_slot = db.get_one(Appointment, drop).slot_id

    confirmed = reply(keep_phone, "Confirm", "r-1")
    cancelled = reply(drop_phone, "CANCEL please", "r-2")

    assert (confirmed, cancelled) == ("confirmed", "cancelled")
    with desk.session() as db:
        kept = db.get_one(Appointment, keep)
        dropped = db.get_one(Appointment, drop)
        assert (
            kept.confirmed_at is not None
            and kept.status == AppointmentStatus.BOOKED.value
        )
        assert (dropped.status, dropped.cancelled_via) == (
            AppointmentStatus.CANCELLED.value,
            "sms",
        )
        assert db.get_one(AppointmentSlot, drop_slot).booked_count == 0
        assert db.get_one(Patient, drop_patient) is not None
        opted_out = db.execute(
            select(PatientNotificationPreference.opted_out_at).where(
                PatientNotificationPreference.patient_id == drop_patient
            )
        ).scalar_one_or_none()
    assert opted_out is None  # a booking's CANCEL is not STOP


def test_cancel_with_no_reminded_booking_still_means_stop(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """The keyword's older meaning holds for everyone without a reminded booking open."""
    reply = _gateway(desk)
    with desk.session() as db:
        patient = PatientFactory.create(db)
        db.commit()
        phone = patient.phone_e164
    assert reply(phone, "CANCEL", "r-3") == "stopped"


def test_a_web_push_button_answers_without_a_page_and_a_second_answer_changes_nothing(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """The reminder's own Cancel button: one POST with the token frees the time; the payload carries the reply path."""
    starts = _at(12)
    appointment_id, _ = _booking(desk, starts, booked_at=starts - timedelta(days=2))
    with desk.session() as db:
        token = db.get_one(Appointment, appointment_id).reply_token
    assert token is not None
    rendered = template_registry.render(
        NotificationTemplate.APPOINTMENT_REMINDER_24H,
        NotificationChannel.WEB_PUSH,
        "Ref {number} at {clinic}.",
        "Your appointment is tomorrow",
        {
            "number": "K7M-4QP",
            "clinic": "Zola",
            "reply_url": reminders.reply_path(token),
        },
    )
    anyone = TestClient(desk.app)

    first = anyone.post(
        f"/api/v1/appointments/replies/{token}", json={"reply": "cancel"}
    )
    second = anyone.post(
        f"/api/v1/appointments/replies/{token}", json={"reply": "confirm"}
    )
    unknown = anyone.post(
        "/api/v1/appointments/replies/" + "x" * 43, json={"reply": "cancel"}
    )

    assert payload_for(rendered)["reply"] == f"/api/v1/appointments/replies/{token}"
    assert first.json() == {
        "reply": "cancel",
        "applied": True,
        "message": "Your booking is cancelled. The time is free for someone else.",
    }
    assert second.json()["applied"] is False
    assert unknown.status_code == status.HTTP_404_NOT_FOUND
    with desk.session() as db:
        assert db.get_one(Appointment, appointment_id).cancelled_via == "web_push"


def test_a_patient_who_already_checked_in_gets_no_reminder(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """How to verify, step 3: a patient already holding a ticket in the queue that day is skipped."""
    starts = _at(13)
    appointment_id, patient_id = _booking(
        desk, starts, booked_at=starts - timedelta(days=2)
    )
    with desk.session() as db:
        join_queue(
            db,
            site=db.get_one(Site, SITE_A),
            queue=db.get_one(Queue, desk.triage.id),
            schedule=published_schedules(db, [SITE_A], from_day=starts.date())[SITE_A],
            source=TicketSource.WEB,
            patient=db.get_one(Patient, patient_id),
            actor="patient",
            moment=starts - timedelta(hours=3),
        )
        db.commit()

    sent = _sweep(desk, starts - timedelta(hours=1, minutes=30))

    assert sent.two_hours == [] and sent.checked_in == [appointment_id]
    assert [m.event for m in _messages(desk, appointment_id)] == []


def test_an_opted_out_patient_is_not_sent_the_reminder_by_the_notification_service(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """Not re-checked here: the service suppresses it, and the booking records that the reminder was due."""
    starts = _at(14)
    appointment_id, patient_id = _booking(
        desk, starts, booked_at=starts - timedelta(days=2)
    )
    with desk.session() as db:
        db.add(
            PatientNotificationPreference(
                patient_id=patient_id, opted_out_at=now_sast()
            )
        )
        db.commit()

    _sweep(desk, starts - timedelta(hours=20))

    [message] = _messages(desk, appointment_id)
    assert message.status == NotificationStatus.SUPPRESSED.value


def test_a_reminder_due_in_quiet_hours_waits_for_them_to_end(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """A 08:00 booking's two-hour reminder falls at 06:00, inside the patient's quiet hours: it is held, not sent."""
    starts = _at(8)
    appointment_id, patient_id = _booking(
        desk, starts, booked_at=starts - timedelta(days=2)
    )
    with desk.session() as db:
        db.add(
            PatientNotificationPreference(
                patient_id=patient_id,
                quiet_hours_start=time(21, 0),
                quiet_hours_end=time(7, 0),
            )
        )
        db.commit()

    sent = _sweep(desk, starts - timedelta(hours=1, minutes=55))

    assert sent.two_hours == [appointment_id]
    [message] = _messages(desk, appointment_id)
    assert message.status == NotificationStatus.QUEUED.value
    assert message.next_attempt_at is not None
    assert stored_sast(message.next_attempt_at) == starts.replace(hour=7)


def test_attendance_with_and_without_reminders_is_counted_per_clinic(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """What Issue 93 reads: bookings, confirmations, cancellations by reply, attended and no-shows per reminder count."""
    starts = _at(15)
    reminded, _ = _booking(desk, starts, booked_at=starts - timedelta(days=2))
    not_reminded, _ = _booking(
        desk, starts + timedelta(minutes=15), booked_at=starts - timedelta(hours=1)
    )
    _sweep(desk, starts - timedelta(hours=20))
    _sweep(desk, starts - timedelta(hours=1, minutes=50))
    with desk.session() as db:
        db.get_one(Appointment, reminded).confirmed_at = starts - timedelta(hours=19)
        db.commit()
    with desk.session() as db:
        done = conversion.convert_due(db, moment=starts - timedelta(minutes=10))
        db.commit()
    tickets = dict(done.converted)
    desk_actor = Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example")
    with desk.session() as db:
        for step in (TicketStatus.CALLED, TicketStatus.IN_PROGRESS, TicketStatus.DONE):
            transition_ticket(
                db, tickets[reminded], step, actor=desk_actor, moment=starts
            )
        for step in (TicketStatus.CALLED, TicketStatus.NO_SHOW):
            transition_ticket(
                db, tickets[not_reminded], step, actor=desk_actor, moment=starts
            )
        db.commit()

    report = desk.staff("manager.a").get(
        f"/api/v1/sites/{SITE_A}/reports/reminders",
        params={"start": starts.date().isoformat(), "end": starts.date().isoformat()},
    )

    assert report.status_code == status.HTTP_200_OK, report.text
    groups = {group["reminders"]: group for group in report.json()["groups"]}
    assert groups[2] == {
        "reminders": 2,
        "bookings": 1,
        "confirmed": 1,
        "cancelled_by_reply": 0,
        "attended": 1,
        "no_show": 0,
    }
    assert groups[0] == {
        "reminders": 0,
        "bookings": 1,
        "confirmed": 0,
        "cancelled_by_reply": 0,
        "attended": 0,
        "no_show": 1,
    }
    assert (
        desk.staff("desk.a")
        .get(f"/api/v1/sites/{SITE_A}/reports/reminders")
        .status_code
        == 403
    )
