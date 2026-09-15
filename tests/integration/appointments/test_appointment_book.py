"""A clinic's appointment book over HTTP: generation, the day view, blocks and the shared limit (Issue 80).

What each test proves, in the order of the issue's criteria:

* a month of slots from a weekly template skips a public holiday, and generating again changes nothing;
* appointments and walk-ins are counted against one daily limit, in both directions;
* the day view reflects a cancellation on the very next read;
* slots are Johannesburg wall clock across midnight, whatever zone the server runs in (CI is UTC);
* a manager's block hides the range at once and lifting it brings the slots back;
* and the guard rails: the manager shapes the book, the front desk reads it, another clinic is a 404.

Concurrency (the last place in a slot, a walk-in racing a booking) is proved on PostgreSQL in
``test_capacity_concurrency.py``; SQLite has a single writer and would pass by construction.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from starlette import status

from src.commons.enums import (
    AuditEntityType,
    JoinRefusal,
    SlotRefusal,
)
from src.commons.time import APP_TIMEZONE, business_date, now_sast
from src.database.models import (
    AppointmentSlot,
    AuditEvent,
    PublicHoliday,
    Queue,
)
from src.modules.appointments import capacity
from src.modules.appointments.service import generate_published
from tests.factories import PatientFactory

SITE_A = "0199b0c0-0000-7000-8000-0000000c1a01"


#: Clinic A's appointment book.
BOOK = f"/api/v1/sites/{SITE_A}/appointments"


def _every_day(
    starts: str, ends: str, minutes: int = 30, capacity: int = 2
) -> list[dict[str, Any]]:
    return [
        {
            "weekday": weekday,
            "starts_at": starts,
            "ends_at": ends,
            "slot_minutes": minutes,
            "capacity": capacity,
        }
        for weekday in range(7)
    ]


def _set_template(
    desk: SimpleNamespace, queue: Queue, windows: list[dict[str, Any]]
) -> None:
    manager = desk.staff("manager.a")
    answer = manager.put(
        f"{BOOK}/queues/{queue.id}/template", json={"windows": windows}
    )
    assert answer.status_code == status.HTTP_200_OK, answer.text


def _generate(desk: SimpleNamespace, queue: Queue, **body: Any) -> dict[str, Any]:
    answer = desk.staff("manager.a").post(
        f"{BOOK}/queues/{queue.id}/slots/generate", json=body
    )
    assert answer.status_code == status.HTTP_200_OK, answer.text
    return answer.json()


def _day(desk: SimpleNamespace, day: date, **params: Any) -> dict[str, Any]:
    answer = desk.staff("desk.a").get(
        f"{BOOK}/availability",
        params={"day": day.isoformat(), "queue_id": desk.triage.id, **params},
    )
    assert answer.status_code == status.HTTP_200_OK, answer.text
    return answer.json()["queues"][0]


def test_a_month_from_a_weekly_template_skips_the_holiday_and_regenerating_changes_nothing(
    desk: SimpleNamespace,
) -> None:
    """Every day 08:00 to 10:00 for 30 days, with a public holiday on day 10: 29 days of four slots."""
    today = business_date(now_sast())
    first = today + timedelta(days=1)
    holiday = first + timedelta(days=9)
    with desk.session() as db:
        db.add(PublicHoliday(holiday_date=holiday, name="A public holiday"))
        db.commit()
    _set_template(desk, desk.triage, _every_day("08:00", "10:00"))

    made = _generate(desk, desk.triage, from_day=first.isoformat(), days=30)
    again = _generate(desk, desk.triage, from_day=first.isoformat(), days=30)

    assert made["created"] == 29 * 4
    assert (made["first_day"], made["last_day"]) == (
        first.isoformat(),
        (first + timedelta(days=29)).isoformat(),
    )
    assert again["created"] == 0 and again["removed"] == 0 and again["kept"] == 29 * 4
    with desk.session() as db:
        days = set(
            db.execute(
                select(AppointmentSlot.service_day).where(
                    AppointmentSlot.queue_id == desk.triage.id
                )
            ).scalars()
        )
    assert holiday not in days
    assert len(days) == 29
    assert _day(desk, holiday)["slots"] == []


def test_walk_ins_and_appointments_share_one_daily_limit_in_both_directions(
    desk: SimpleNamespace,
) -> None:
    """A queue of 3 a day: two booked places leave one walk-in; a full day refuses the next booking."""
    today = business_date(now_sast())
    with desk.session() as db:
        queue = db.get(Queue, desk.triage.id)
        assert queue is not None
        queue.max_daily_capacity = 3
        slot = AppointmentSlot(
            site_id=SITE_A,
            queue_id=queue.id,
            starts_at=now_sast(),
            ends_at=now_sast() + timedelta(minutes=15),
            service_day=today,
            capacity=5,
        )
        db.add(slot)
        db.flush()
        for _ in range(2):
            capacity.claim_place(
                db, slot=slot, queue=queue, patient_id=PatientFactory.create(db).id
            )
        db.commit()

    front = desk.staff("desk.a")
    first = front.post(desk.walk_in_path(desk.triage), json={})
    full = front.post(desk.walk_in_path(desk.triage), json={})

    assert first.status_code == status.HTTP_201_CREATED, first.text
    assert full.status_code == status.HTTP_409_CONFLICT
    assert full.json()["code"] == f"queue.join.{JoinRefusal.QUEUE_FULL.value}"

    with desk.session() as db:
        queue = db.get(Queue, desk.triage.id)
        slot = db.get(AppointmentSlot, slot.id)
        assert queue is not None and slot is not None
        usage = capacity.day_usage(db, queue, today)
        assert (usage.tickets, usage.appointments, usage.remaining) == (1, 2, 0)
        try:
            capacity.claim_place(
                db, slot=slot, queue=queue, patient_id=PatientFactory.create(db).id
            )
        except capacity.SlotRefusedError as refused:
            assert refused.refusal is SlotRefusal.DAY_FULL
            assert refused.code == "appointments.slot.day_full"
        else:  # pragma: no cover - the assertion is the test
            raise AssertionError("A booking on a full day was accepted.")


def test_the_day_view_reflects_a_cancellation_on_the_next_read(
    desk: SimpleNamespace,
) -> None:
    """The last place taken hides the slot; releasing it shows the place again, with nothing cached."""
    day = business_date(now_sast()) + timedelta(days=2)
    _set_template(desk, desk.triage, _every_day("09:00", "09:30", capacity=1))
    _generate(desk, desk.triage, from_day=day.isoformat(), days=1)
    [offered] = _day(desk, day)["slots"]
    assert offered["remaining"] == 1

    with desk.session() as db:
        slot = db.get(AppointmentSlot, offered["id"])
        queue = db.get(Queue, desk.triage.id)
        assert slot is not None and queue is not None
        booking = capacity.claim_place(
            db, slot=slot, queue=queue, patient_id=PatientFactory.create(db).id
        )
        db.commit()
    booked = _day(desk, day, include_unavailable=True)["slots"]
    assert _day(desk, day)["slots"] == []
    assert [(s["booked"], s["remaining"], s["refusal"]) for s in booked] == [
        (1, 0, SlotRefusal.SLOT_FULL.value)
    ]

    with desk.session() as db:
        assert capacity.release_place(db, db.merge(booking)) is True
        assert capacity.release_place(db, db.merge(booking)) is False  # idempotent
        db.commit()
    [back] = _day(desk, day)["slots"]
    assert (back["booked"], back["remaining"], back["refusal"]) == (0, 1, None)


def test_slot_times_are_johannesburg_wall_clock_across_midnight(
    desk: SimpleNamespace,
) -> None:
    """23:00 to 01:00 every day: the next day's view opens with 00:00 and 00:30 SAST, its own day."""
    day = business_date(now_sast()) + timedelta(days=3)
    _set_template(desk, desk.triage, _every_day("23:00", "01:00"))
    _generate(desk, desk.triage, from_day=day.isoformat(), days=2)

    following = _day(desk, day + timedelta(days=1))

    starts = [datetime.fromisoformat(slot["starts_at"]) for slot in following["slots"]]
    assert [start.astimezone(APP_TIMEZONE).time() for start in starts] == [
        time(0),
        time(0, 30),
        time(23),
        time(23, 30),
    ]
    assert {start.astimezone(APP_TIMEZONE).date() for start in starts} == {
        day + timedelta(days=1)
    }
    assert following["slots"][0]["starts_at"].endswith("+02:00")


def test_a_block_for_a_staff_absence_hides_its_slots_until_it_is_lifted(
    desk: SimpleNamespace,
) -> None:
    """Blocking 08:00 to 09:00 removes two of four slots, counts the booked place, and lifts cleanly."""
    day = business_date(now_sast()) + timedelta(days=4)
    _set_template(desk, desk.triage, _every_day("08:00", "10:00"))
    _generate(desk, desk.triage, from_day=day.isoformat(), days=1)
    slots = _day(desk, day)["slots"]
    with desk.session() as db:
        slot = db.get(AppointmentSlot, slots[0]["id"])
        queue = db.get(Queue, desk.triage.id)
        assert slot is not None and queue is not None
        capacity.claim_place(
            db, slot=slot, queue=queue, patient_id=PatientFactory.create(db).id
        )
        db.commit()
    manager = desk.staff("manager.a")

    blocked = manager.post(
        f"{BOOK}/blocks",
        json={
            "starts_at": f"{day.isoformat()}T08:00:00+02:00",
            "ends_at": f"{day.isoformat()}T09:00:00",
            "reason": "Sister Dlamini on leave",
            "queue_id": desk.triage.id,
        },
    )
    assert blocked.status_code == status.HTTP_201_CREATED, blocked.text
    assert blocked.json()["booked_places_inside"] == 1
    hidden = _day(desk, day)["slots"]
    reasons = {
        s["starts_at"][11:16]: s["refusal"]
        for s in _day(desk, day, include_unavailable=True)["slots"]
    }

    lifted = manager.delete(f"{BOOK}/blocks/{blocked.json()['id']}")
    again = manager.delete(f"{BOOK}/blocks/{blocked.json()['id']}")

    assert [s["starts_at"][11:16] for s in hidden] == ["09:00", "09:30"]
    assert reasons["08:00"] == reasons["08:30"] == SlotRefusal.BLOCKED.value
    assert lifted.status_code == status.HTTP_200_OK and lifted.json()["lifted_at"]
    assert again.status_code == status.HTTP_404_NOT_FOUND
    assert len(_day(desk, day)["slots"]) == 4
    with desk.session() as db:
        audited = db.execute(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.entity_type == AuditEntityType.APPOINTMENT_BLOCK.value
            )
        ).scalar_one()
    assert audited == 2


def test_a_schedule_change_removes_unbooked_slots_and_withdraws_booked_ones(
    desk: SimpleNamespace,
) -> None:
    """Dropping a window deletes its empty slots, withdraws the booked one, and restoring offers it again."""
    day = business_date(now_sast()) + timedelta(days=5)
    _set_template(desk, desk.triage, _every_day("08:00", "09:00"))
    _generate(desk, desk.triage, from_day=day.isoformat(), days=1)
    with desk.session() as db:
        slot = (
            db.execute(
                select(AppointmentSlot)
                .where(AppointmentSlot.queue_id == desk.triage.id)
                .order_by(AppointmentSlot.starts_at)
            )
            .scalars()
            .first()
        )
        queue = db.get(Queue, desk.triage.id)
        assert slot is not None and queue is not None
        capacity.claim_place(
            db, slot=slot, queue=queue, patient_id=PatientFactory.create(db).id
        )
        db.commit()

    _set_template(desk, desk.triage, [])
    dropped = _generate(desk, desk.triage, from_day=day.isoformat(), days=1)
    view = _day(desk, day, include_unavailable=True)["slots"]
    _set_template(desk, desk.triage, _every_day("08:00", "09:00"))
    restored = _generate(desk, desk.triage, from_day=day.isoformat(), days=1)

    assert (dropped["removed"], dropped["withdrawn"]) == (1, 1)
    assert [(s["booked"], s["refusal"]) for s in view] == [
        (1, SlotRefusal.WITHDRAWN.value)
    ]
    assert (restored["restored"], restored["created"]) == (1, 1)


def test_a_day_override_with_no_windows_closes_the_book_for_that_date(
    desk: SimpleNamespace,
) -> None:
    """An empty override removes the date's slots on generation; clearing it brings them back."""
    day = business_date(now_sast()) + timedelta(days=6)
    manager = desk.staff("manager.a")
    _set_template(desk, desk.triage, _every_day("08:00", "09:00"))
    path = f"{BOOK}/queues/{desk.triage.id}/days/{day.isoformat()}"

    closed = manager.put(path, json={"windows": []})
    none = _generate(desk, desk.triage, from_day=day.isoformat(), days=1)
    cleared = manager.delete(path)
    cleared_again = manager.delete(path)
    back = _generate(desk, desk.triage, from_day=day.isoformat(), days=1)

    assert closed.status_code == status.HTTP_200_OK and closed.json()["windows"] == []
    assert none["created"] == 0
    assert cleared.status_code == status.HTTP_204_NO_CONTENT
    assert cleared_again.status_code == status.HTTP_404_NOT_FOUND
    assert back["created"] == 2


def test_the_booking_policy_limits_what_is_offered(desk: SimpleNamespace) -> None:
    """Defaults until set; a 2-day horizon makes day 3 too far, and the policy change is audited."""
    manager = desk.staff("manager.a")
    default = desk.staff("desk.a").get(f"{BOOK}/policy").json()
    set_to = manager.put(
        f"{BOOK}/policy", json={"horizon_days": 2, "min_lead_minutes": 0}
    )
    day = business_date(now_sast()) + timedelta(days=3)
    _set_template(desk, desk.triage, _every_day("08:00", "08:30", capacity=1))
    generated = _generate(desk, desk.triage, from_day=day.isoformat(), days=1)

    view = _day(desk, day, include_unavailable=True)["slots"]

    assert (
        default["horizon_days"],
        default["min_lead_minutes"],
        default["is_default"],
    ) == (28, 60, True)
    assert (
        set_to.status_code == status.HTTP_200_OK
        and set_to.json()["is_default"] is False
    )
    assert generated["created"] == 1
    assert [s["refusal"] for s in view] == [SlotRefusal.TOO_FAR.value]


def test_the_manager_shapes_the_book_the_desk_reads_it_and_another_clinic_is_a_404(
    desk: SimpleNamespace,
) -> None:
    """RBAC and the site guard on the new routes, and a window naming a foreign service."""
    front = desk.staff("desk.a")
    other = desk.staff("desk.b")
    manager = desk.staff("manager.a")

    assert front.get(f"{BOOK}/policy").status_code == status.HTTP_200_OK
    assert (
        front.put(
            f"{BOOK}/policy", json={"horizon_days": 7, "min_lead_minutes": 0}
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert other.get(f"{BOOK}/availability").status_code == status.HTTP_404_NOT_FOUND
    assert (
        manager.get(f"{BOOK}/queues/{desk.other_triage.id}/template").status_code
        == status.HTTP_404_NOT_FOUND
    )
    foreign = manager.put(
        f"{BOOK}/queues/{desk.triage.id}/template",
        json={
            "windows": [
                {
                    **_every_day("08:00", "09:00")[0],
                    "service_id": "0199b0c0-0000-7000-8000-00000000dead",
                }
            ]
        },
    )
    assert foreign.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    inverted = manager.post(
        f"{BOOK}/blocks",
        json={
            "starts_at": "2026-10-14T09:00:00+02:00",
            "ends_at": "2026-10-14T08:00:00+02:00",
            "reason": "Backwards",
        },
    )
    assert inverted.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_the_nightly_run_rolls_listed_clinics_forward_and_is_idempotent(
    desk: SimpleNamespace,
) -> None:
    """The sweep's body generates through each listed clinic's horizon and makes nothing the second time."""
    _set_template(desk, desk.triage, _every_day("08:00", "09:00"))
    with desk.session() as db:
        first = generate_published(db)
        db.commit()
        second = generate_published(db)
        db.commit()
        site_days = db.execute(
            select(func.count(func.distinct(AppointmentSlot.service_day))).where(
                AppointmentSlot.queue_id == desk.triage.id
            )
        ).scalar_one()

    # Today (if 08:00 has not passed) through today + 28 days, two slots a day.
    assert first >= 28 * 2
    assert second == 0
    assert site_days >= 28
