"""Booking, moving, cancelling and converting an appointment, over HTTP and the real sweep (Issue 81).

Against the queue fixture's clinic A (listed, open around the clock), its Triage queue, and real patients:

* **book, reschedule, cancel**: a reference and a confirmation; one booking a day per queue; a move frees the
  original time at once and a refused move changes nothing; a cancellation frees the time at once;
* **conversion at the lead time**: exactly one ticket, from the same counter as walk-ins, however often the
  sweep runs; onto an existing ticket when the patient walked in early; never double-counted against the day;
* **the late rule**: a booking converted after its time still gets a ticket at the back; a closed clinic makes
  it wait; a day that ends unconverted lapses and the patient is told;
* **parity**: the same service calls give the same answers for web, USSD and WhatsApp, the source carried
  onto the ticket, so the channel adapters (Issues 73 to 76) and the parity suite (Issue 79) inherit them.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette import status

from src.commons.enums import (
    AppointmentStatus,
    ConsentPurpose,
    JoinRefusal,
    NotificationChannel,
    PatientChannel,
    PatientEvent,
    SiteStatus,
    SlotRefusal,
    TicketSource,
    TicketStatus,
)
from src.commons.time import APP_TIMEZONE, business_date, now_sast
from src.database.models import (
    Appointment,
    AppointmentSlot,
    Notification,
    Patient,
    Queue,
    Site,
    SiteClosure,
    Ticket,
)
from src.modules.appointments import booking, capacity, conversion
from src.modules.notifications.transports import NoopTransport, use_transports
from src.modules.patients.consent import record_consent
from src.modules.queue.service import JoinRefusedError, join_queue
from src.modules.sites.hours import published_schedules
from tests.factories import PatientFactory
from tests.integration.queue.conftest import SITE_A

_CLINIC = f"/api/v1/clinics/{SITE_A}/appointments"


@pytest.fixture
def everywhere() -> Iterator[None]:
    """Every transport reaches every patient."""
    with use_transports(
        NoopTransport(channel=NotificationChannel.WEB_PUSH, free=True, address="sub"),
        NoopTransport(channel=NotificationChannel.SMS, address="+27820000001"),
    ):
        yield


def _slot(
    desk: SimpleNamespace,
    starts: datetime,
    *,
    capacity_: int = 2,
    queue: Queue | None = None,
) -> str:
    with desk.session() as db:
        slot = AppointmentSlot(
            site_id=SITE_A,
            queue_id=(queue or desk.triage).id,
            starts_at=starts,
            ends_at=starts + timedelta(minutes=15),
            service_day=business_date(starts),
            capacity=capacity_,
        )
        db.add(slot)
        db.commit()
        return slot.id


def _patient(desk: SimpleNamespace) -> tuple[TestClient, str]:
    client, patient_id = desk.patient()
    with desk.session() as db:
        record_consent(
            db,
            db.get_one(Patient, patient_id),
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
        db.commit()
    return client, patient_id


def _book(client: TestClient, slot_id: str) -> dict:
    answer = client.post(_CLINIC, json={"slot_id": slot_id})
    assert answer.status_code == status.HTTP_201_CREATED, answer.text
    return answer.json()


def _booked_count(desk: SimpleNamespace, slot_id: str) -> int:
    with desk.session() as db:
        return db.get_one(AppointmentSlot, slot_id).booked_count


def _tickets_for(desk: SimpleNamespace, appointment_id: str) -> list[Ticket]:
    with desk.session() as db:
        return list(
            db.execute(
                select(Ticket).where(Ticket.appointment_id == appointment_id)
            ).scalars()
        )


def _tomorrow_at(hour: int, minute: int = 0) -> datetime:
    """A fixed time tomorrow in Johannesburg, so a test never depends on the hour it runs at."""
    tomorrow = business_date(now_sast()) + timedelta(days=1)
    return datetime.combine(tomorrow, time(hour, minute), tzinfo=APP_TIMEZONE)


def _join(
    desk: SimpleNamespace, moment: datetime, patient_id: str | None = None
) -> Ticket:
    """A walk-in (or a patient's own join) through the join service, at ``moment``."""
    with desk.session() as db:
        site = db.get_one(Site, SITE_A)
        result = join_queue(
            db,
            site=site,
            queue=db.get_one(Queue, desk.triage.id),
            schedule=published_schedules(db, [SITE_A], from_day=moment.date())[SITE_A],
            source=TicketSource.WEB if patient_id else TicketSource.WALK_IN,
            patient=db.get_one(Patient, patient_id) if patient_id else None,
            actor="desk.a@clinicq.example",
            moment=moment,
        )
        db.commit()
        return result.ticket


def _convert(desk: SimpleNamespace, moment: datetime) -> conversion.Converted:
    with desk.session() as db:
        done = conversion.convert_due(db, moment=moment)
        db.commit()
    return done


def test_a_patient_books_a_time_and_is_told_the_reference(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """The public day view lists the time; booking answers 201 with a reference, and the patient is sent it."""
    later = now_sast().replace(second=0, microsecond=0) + timedelta(days=1)
    slot_id = _slot(desk, later)
    client, _patient_id = _patient(desk)

    offered = TestClient(desk.app).get(
        f"{_CLINIC}/availability", params={"day": later.date().isoformat()}
    )
    booked = _book(client, slot_id)
    again = client.post(_CLINIC, json={"slot_id": slot_id})
    mine = client.get("/api/v1/patients/me/appointments").json()

    assert offered.status_code == status.HTTP_200_OK
    assert [s["id"] for q in offered.json()["queues"] for s in q["slots"]] == [slot_id]
    assert (
        "booked_count" not in str(offered.json())
        and "tickets" not in offered.json()["queues"][0]
    )
    assert (booked["status"], booked["source"], booked["changeable"]) == (
        "booked",
        "web",
        True,
    )
    assert len(booked["reference"]) == 7 and booked["reference"][3] == "-"
    assert booked["message"].startswith("Booked: Triage at ")
    assert again.status_code == status.HTTP_409_CONFLICT
    assert (
        again.json()["code"] == f"appointments.slot.{SlotRefusal.ALREADY_BOOKED.value}"
    )
    assert [item["id"] for item in mine["items"]] == [booked["id"]]
    with desk.session() as db:
        told = (
            db.execute(
                select(Notification).where(
                    Notification.event == PatientEvent.BOOKED.value
                )
            )
            .scalars()
            .first()
        )
    assert told is not None and told.payload["number"] == booked["reference"]


def test_rescheduling_frees_the_original_time_at_once_and_a_refused_move_changes_nothing(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """How the criterion reads: the original slot is free the moment the move answers."""
    day = now_sast().replace(second=0, microsecond=0) + timedelta(days=2)
    first, second, full = (
        _slot(desk, day),
        _slot(desk, day + timedelta(hours=1)),
        _slot(desk, day + timedelta(hours=2), capacity_=1),
    )
    client, _ = _patient(desk)
    other, _ = _patient(desk)
    _book(other, full)
    booked = _book(client, first)

    refused = client.post(
        f"/api/v1/patients/me/appointments/{booked['id']}/reschedule",
        json={"slot_id": full},
    )
    assert refused.status_code == status.HTTP_409_CONFLICT
    assert (_booked_count(desk, first), refused.json()["code"]) == (
        1,
        "appointments.slot.slot_full",
    )

    moved = client.post(
        f"/api/v1/patients/me/appointments/{booked['id']}/reschedule",
        json={"slot_id": second},
    )
    assert moved.status_code == status.HTTP_200_OK, moved.text
    assert (_booked_count(desk, first), _booked_count(desk, second)) == (0, 1)
    assert moved.json()["rescheduled_from_id"] == booked["id"]
    assert moved.json()["reference"] != booked["reference"]
    stale = client.post(f"/api/v1/patients/me/appointments/{booked['id']}/cancel")
    assert stale.status_code == status.HTTP_409_CONFLICT
    assert stale.json()["code"] == booking.NOT_BOOKED_CODE


def test_cancelling_frees_the_time_at_once_and_only_the_patient_may(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """Cancel: booked_count back to 0 and the day view offers the time again; a stranger gets 404."""
    later = now_sast().replace(second=0, microsecond=0) + timedelta(days=1)
    slot_id = _slot(desk, later, capacity_=1)
    client, _ = _patient(desk)
    stranger, _ = _patient(desk)
    booked = _book(client, slot_id)

    theirs = stranger.post(f"/api/v1/patients/me/appointments/{booked['id']}/cancel")
    cancelled = client.post(f"/api/v1/patients/me/appointments/{booked['id']}/cancel")
    offered = (
        TestClient(desk.app)
        .get(f"{_CLINIC}/availability", params={"day": later.date().isoformat()})
        .json()
    )

    assert theirs.status_code == status.HTTP_404_NOT_FOUND
    assert cancelled.json()["status"] == AppointmentStatus.CANCELLED.value
    assert _booked_count(desk, slot_id) == 0
    assert [s["id"] for q in offered["queues"] for s in q["slots"]] == [slot_id]


def test_a_booking_becomes_one_ticket_at_the_lead_time_in_the_same_sequence_however_often_the_sweep_runs(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """How to verify, steps 1 and 2: not yet at 31 minutes before; one ticket at 30; still one after two more runs."""
    starts = _tomorrow_at(10)
    client, patient_id = _patient(desk)
    booked = _book(client, _slot(desk, starts))
    walk_in = _join(desk, starts - timedelta(minutes=45))

    early = _convert(desk, starts - timedelta(minutes=31))
    first = _convert(desk, starts - timedelta(minutes=30))
    second = _convert(desk, starts - timedelta(minutes=30))
    third = _convert(desk, starts - timedelta(minutes=29))
    [ticket] = _tickets_for(desk, booked["id"])
    walk_in_after = _join(desk, starts - timedelta(minutes=20))

    assert (early.converted, second.converted, third.converted) == ([], [], [])
    assert first.converted == [(booked["id"], ticket.id)]
    # The same counter as walk-ins: after the walk-in before it, before the walk-in after it.
    assert (walk_in.number, ticket.number, walk_in_after.number) == (
        "T001",
        "T002",
        "T003",
    )
    assert (ticket.source, ticket.status) == (
        TicketSource.WEB.value,
        TicketStatus.WAITING.value,
    )
    with desk.session() as db:
        stored = db.get_one(Appointment, booked["id"])
        assert stored.status == AppointmentStatus.CONVERTED.value
        assert (
            db.execute(
                select(func.count(Ticket.id)).where(Ticket.patient_id == patient_id)
            ).scalar_one()
            == 1
        )
    mine = client.get("/api/v1/patients/me/appointments").json()["items"][0]
    assert (mine["status"], mine["changeable"]) == ("converted", False)
    assert mine["ticket_page_url"].startswith("/t/")


def test_a_converted_booking_is_never_counted_twice_against_the_day(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """A queue of 2 with one booking and one walk-in is full; converting the booking keeps it at 2, not 3."""
    starts = _tomorrow_at(11)
    with desk.session() as db:
        db.get_one(Queue, desk.triage.id).max_daily_capacity = 2
        db.commit()
    client, _ = _patient(desk)
    booked = _book(client, _slot(desk, starts))
    _join(desk, starts - timedelta(hours=2))
    with pytest.raises(JoinRefusedError) as full:
        _join(desk, starts - timedelta(hours=2))
    done = _convert(desk, starts - timedelta(minutes=30))

    assert full.value.refusal is JoinRefusal.QUEUE_FULL
    assert [appointment for appointment, _ in done.converted] == [booked["id"]]
    with desk.session() as db:
        usage = capacity.day_usage(
            db, db.get_one(Queue, desk.triage.id), business_date(starts)
        )
    assert (usage.tickets, usage.appointments, usage.remaining) == (2, 0, 0)


def test_the_late_rule_converts_after_the_time_waits_while_closed_and_lapses_a_lost_day(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """How to verify, step 3, and the rest of the documented rule, at fixed times tomorrow."""
    noon = _tomorrow_at(12)
    late_slot = _slot(desk, noon - timedelta(hours=1))
    lost_slot = _slot(desk, noon - timedelta(days=1))
    closed_slot = _slot(desk, noon + timedelta(minutes=40))
    _, late_id = _patient(desk)
    _, lost_id = _patient(desk)
    waiting_patient, _ = _patient(desk)
    # Booked before the closure is announced: that is the case the rule is for.
    waiting_booking = _book(waiting_patient, closed_slot)["id"]
    with desk.session() as db:
        # Past times are booked directly: the booking API rightly refuses a time that has started.
        queue = db.get_one(Queue, desk.triage.id)
        late = capacity.claim_place(
            db,
            slot=db.get_one(AppointmentSlot, late_slot),
            queue=queue,
            patient_id=late_id,
        )
        lost = capacity.claim_place(
            db,
            slot=db.get_one(AppointmentSlot, lost_slot),
            queue=queue,
            patient_id=lost_id,
        )
        db.add(
            SiteClosure(
                site_id=SITE_A,
                reason="Water off",
                starts_at=noon + timedelta(minutes=5),
                ends_at=noon + timedelta(hours=2),
            )
        )
        db.commit()
        late_booking, lost_booking = late.id, lost.id
    ahead = _join(desk, noon - timedelta(minutes=1))

    at_noon = _convert(desk, noon)
    while_closed = _convert(desk, noon + timedelta(minutes=15))
    reopened = _convert(desk, noon + timedelta(hours=2, minutes=1))

    [late_ticket] = _tickets_for(desk, late_booking)
    [reopened_ticket] = _tickets_for(desk, waiting_booking)
    # A booking an hour past its time still gets a ticket, behind whoever was already there.
    assert [a for a, _ in at_noon.converted] == [late_booking]
    assert late_ticket.sequence > ahead.sequence
    # The clinic shut: the booking waits, and converts once it reopens, after its own time.
    assert (waiting_booking, JoinRefusal.CLINIC_CLOSED) in while_closed.waiting
    assert [a for a, _ in reopened.converted] == [waiting_booking]
    assert reopened_ticket.sequence > late_ticket.sequence
    # Yesterday's booking that never became a ticket lapses once, and the patient is told.
    assert at_noon.lapsed == [lost_booking] and while_closed.lapsed == []
    with desk.session() as db:
        assert (
            db.get_one(Appointment, lost_booking).status
            == AppointmentStatus.LAPSED.value
        )
        told = (
            db.execute(
                select(Notification.dedupe_key).where(
                    Notification.event == PatientEvent.BOOKING_LAPSED.value
                )
            )
            .scalars()
            .all()
        )
    assert set(told) == {f"{lost_booking}:lapsed"}


def test_a_patient_who_walked_in_early_keeps_their_ticket_and_the_booking_is_converted_onto_it(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """No second place: the booking is marked converted onto the ticket the patient already holds."""
    starts = _tomorrow_at(14)
    client, patient_id = _patient(desk)
    booked = _book(client, _slot(desk, starts))
    joined = _join(desk, starts - timedelta(hours=1), patient_id=patient_id)

    done = _convert(desk, starts - timedelta(minutes=30))

    assert done.converted == [(booked["id"], joined.id)]
    [ticket] = _tickets_for(desk, booked["id"])
    assert ticket.id == joined.id


@pytest.mark.parametrize(
    "source", [TicketSource.WEB, TicketSource.USSD, TicketSource.WHATSAPP]
)
def test_booking_answers_identically_on_every_channel(
    desk: SimpleNamespace, everywhere: None, source: TicketSource
) -> None:
    """The same service calls for each channel: the same reference shape, refusals and conversion, the source carried."""
    starts = _tomorrow_at(15)
    slot_id, other_id = (
        _slot(desk, starts, capacity_=1),
        _slot(desk, starts + timedelta(minutes=15)),
    )
    with desk.session() as db:
        patient = PatientFactory.create(db)
        rival = PatientFactory.create(db)
        db.commit()
        booked = booking.book(
            db,
            site_id=SITE_A,
            slot_id=slot_id,
            patient=patient,
            source=source,
            actor="channel",
        )
        db.commit()
        reference = booked.reference
        with pytest.raises(capacity.SlotRefusedError) as full:
            booking.book(
                db,
                site_id=SITE_A,
                slot_id=slot_id,
                patient=rival,
                source=source,
                actor="channel",
            )
        db.rollback()
        moved = booking.reschedule(
            db,
            patient=patient,
            appointment_id=booked.appointment.id,
            slot_id=other_id,
            actor="channel",
        )
        db.commit()
        moved_id, moved_source = moved.appointment.id, moved.appointment.source
    done = _convert(desk, starts - timedelta(minutes=15))
    [ticket] = _tickets_for(desk, moved_id)

    assert len(reference) == 7 and moved_source == source.value
    assert full.value.refusal is SlotRefusal.SLOT_FULL
    assert [a for a, _ in done.converted] == [moved_id]
    assert ticket.source == source.value


def test_the_front_desk_reads_the_days_bookings_and_another_clinic_is_a_404(
    desk: SimpleNamespace, everywhere: None
) -> None:
    """The site guard on the list, and an unlisted clinic's times are not public."""
    later = now_sast().replace(second=0, microsecond=0) + timedelta(days=1)
    client, _ = _patient(desk)
    booked = _book(client, _slot(desk, later))
    day = later.date().isoformat()

    listed = desk.staff("desk.a").get(
        f"/api/v1/sites/{SITE_A}/appointments/bookings", params={"day": day}
    )
    other = desk.staff("desk.b").get(
        f"/api/v1/sites/{SITE_A}/appointments/bookings", params={"day": day}
    )
    with desk.session() as db:
        db.get_one(Site, SITE_A).status = SiteStatus.SUSPENDED.value
        db.commit()
    hidden = TestClient(desk.app).get(f"{_CLINIC}/availability", params={"day": day})

    assert [item["reference"] for item in listed.json()["items"]] == [
        booked["reference"]
    ]
    assert other.status_code == status.HTTP_404_NOT_FOUND
    assert hidden.status_code == status.HTTP_404_NOT_FOUND
