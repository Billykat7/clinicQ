"""Transfers between queues, and the visit that links them (Issue 45).

* **Triage → doctor → pharmacy in one integration test**: one patient, three tickets, one visit,
  total visit time derivable, each move audited with the staff member and the reason, the patient
  told the new queue, number and wait each time, and their own ticket list showing it.
* A transfer into an **inactive** or **full** queue is refused with a clear message and changes
  nothing; so is the same queue, and a queue the patient is already in.
* **Placement** is the clinic's setting: by arrival order of the visit (the default), or at the back.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from starlette import status

from src.commons.enums import (
    ActorKind,
    AuditAction,
    AuditEntityType,
    ConsentPurpose,
    NotificationTemplate,
    PatientChannel,
    TicketStatus,
    TransferPlacement,
    TransferReason,
)
from src.commons.time import now_sast, stored_sast
from src.database.models import (
    AuditEvent,
    Notification,
    Patient,
    Queue,
    Site,
    Ticket,
    Visit,
)
from src.database.schema import apply_postgres_search_path
from src.modules.patients.consent import record_consent
from src.modules.queue.lifecycle import Actor
from src.modules.queue.transfer import transfer_ticket
from tests.conftest import run_alembic
from tests.factories import QueueFactory, TicketFactory, drive_ticket_to

_DESK = Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example")


def _tickets(desk: SimpleNamespace) -> str:
    return f"/api/v1/sites/{desk.triage.site_id}/tickets"


def _add_doctor(desk: SimpleNamespace, **fields: object) -> Queue:
    with desk.session() as db:
        doctor = QueueFactory.create(
            db,
            site_id=desk.triage.site_id,
            name="Doctor",
            slug="doctor",
            ticket_prefix="D",
            **fields,
        )
        db.commit()
        return doctor


def test_triage_to_doctor_to_pharmacy_is_one_visit_with_three_tickets(
    desk: SimpleNamespace,
) -> None:
    """How to verify, steps 1 and 3: the whole journey, over HTTP, as a nurse and a doctor run it."""
    doctor = _add_doctor(desk)
    client, patient_id = desk.patient()
    with desk.session() as db:
        record_consent(
            db,
            db.get(Patient, patient_id),
            ConsentPurpose.NOTIFICATIONS,
            granted=True,
            channel=PatientChannel.WEB,
        )
        db.commit()
    front = desk.staff("desk.a")
    first = client.post(desk.join_path(desk.triage), json={}).json()["ticket"]

    def see_and_move(ticket_id: str, queue: Queue | None) -> dict:
        for step in ("called", "in_progress"):
            assert (
                front.post(
                    f"{_tickets(desk)}/{ticket_id}/transitions", json={"to": step}
                ).status_code
                == 200
            )
        if queue is None:
            return front.post(
                f"{_tickets(desk)}/{ticket_id}/transitions", json={"to": "done"}
            ).json()
        moved = front.post(
            f"{_tickets(desk)}/{ticket_id}/transfer",
            json={"queue_id": queue.id, "reason": TransferReason.NEXT_STEP.value},
        )
        assert moved.status_code == status.HTTP_200_OK, moved.text
        return moved.json()

    to_doctor = see_and_move(first["id"], doctor)
    mine = client.get("/api/v1/patients/me/tickets").json()
    to_pharmacy = see_and_move(to_doctor["ticket"]["id"], desk.pharmacy)
    finished = see_and_move(to_pharmacy["ticket"]["id"], None)

    # The patient appears in each next queue without rejoining, and their own list shows it.
    assert to_doctor["from_ticket"]["status"] == "transferred"
    assert (to_doctor["ticket"]["queue_id"], to_doctor["ticket"]["number"]) == (
        doctor.id,
        "D001",
    )
    assert to_doctor["message"].startswith("Moved to Doctor as D001. Expected wait ~")
    assert [t["number"] for t in mine] == ["T001", "D001"]
    assert mine[1]["waiting_ahead"] == 0 and mine[1]["wait"]["label"].startswith("~")
    # The pharmacy takes walk-ins only; a transfer is the clinic moving somebody already inside.
    assert to_pharmacy["ticket"]["number"] == "P001"
    assert finished["status"] == "done"

    visit_id = to_doctor["visit_id"]
    assert to_pharmacy["visit_id"] == visit_id
    journey = front.get(f"/api/v1/sites/{desk.triage.site_id}/visits/{visit_id}").json()
    assert [leg["number"] for leg in journey["legs"]] == ["T001", "D001", "P001"]
    assert [leg["status"] for leg in journey["legs"]] == [
        "transferred",
        "transferred",
        "done",
    ]
    assert journey["legs"][1]["transferred_from_id"] == first["id"]
    assert journey["legs"][2]["transferred_from_id"] == to_doctor["ticket"]["id"]
    with desk.session() as db:
        visit = db.get(Visit, visit_id)
        last = db.get(Ticket, to_pharmacy["ticket"]["id"])
        expected_minutes = (
            stored_sast(last.completed_at) - stored_sast(visit.started_at)
        ).total_seconds() / 60
        assert journey["total_minutes"] == round(expected_minutes, 1)
        assert journey["ended_at"] is not None
        assert db.scalar(select(func.count(Visit.id))) == 1  # one journey, not three

        moves = db.scalars(
            select(AuditEvent.context).where(
                AuditEvent.entity_type == AuditEntityType.TICKET.value,
                AuditEvent.actor == "desk.a@clinicq.example",
                AuditEvent.context.like("%transferred%"),
            )
        ).all()
        messages = db.scalars(
            select(Notification.template_key).where(
                Notification.recipient == db.get(Patient, patient_id).phone_e164
            )
        ).all()
    assert sorted(moves) == sorted(
        [
            "T001: in_progress → transferred (transferred to Doctor, reason next_step)",
            "D001 in Doctor: transferred from T001 in Triage, reason next_step",
            "D001: in_progress → transferred (transferred to Pharmacy, reason next_step)",
            "P001 in Pharmacy: transferred from D001 in Doctor, reason next_step",
        ]
    )
    assert messages == [NotificationTemplate.TICKET_TRANSFERRED.value] * 2


def test_a_transfer_into_an_inactive_queue_is_refused_and_changes_nothing(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 2: a clear message, the ticket still in progress, no new ticket."""
    closed = _add_doctor(desk, is_active=False)
    with desk.session() as db:
        seen = drive_ticket_to(
            db,
            TicketFactory.create(db, queue=db.get(Queue, desk.triage.id)),
            TicketStatus.IN_PROGRESS,
        )
        db.commit()

    refused = desk.staff("desk.a").post(
        f"{_tickets(desk)}/{seen.id}/transfer",
        json={"queue_id": closed.id, "reason": TransferReason.NEXT_STEP.value},
    )

    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["code"] == "ticket.transfer.queue_closed"
    assert (
        refused.json()["detail"] == "This queue is not taking patients at the moment."
    )
    with desk.session() as db:
        assert db.get(Ticket, seen.id).status == TicketStatus.IN_PROGRESS.value
        assert (
            db.scalar(select(func.count(Ticket.id)).where(Ticket.queue_id == closed.id))
            == 0
        )


def test_a_transfer_into_a_full_queue_is_refused_and_changes_nothing(
    desk: SimpleNamespace,
) -> None:
    """A full target: refused, the source ticket untouched, and the target's number not spent."""
    doctor = _add_doctor(desk, max_daily_capacity=1)
    with desk.session() as db:
        TicketFactory.create(db, queue=db.get(Queue, doctor.id))  # the one place, taken
        seen = drive_ticket_to(
            db,
            TicketFactory.create(db, queue=db.get(Queue, desk.triage.id)),
            TicketStatus.IN_PROGRESS,
        )
        db.commit()

    refused = desk.staff("desk.a").post(
        f"{_tickets(desk)}/{seen.id}/transfer",
        json={"queue_id": doctor.id, "reason": TransferReason.NEXT_STEP.value},
    )

    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["code"] == "ticket.transfer.queue_full"
    with desk.session() as db:
        assert db.get(Ticket, seen.id).status == TicketStatus.IN_PROGRESS.value
        db.get(Queue, doctor.id).max_daily_capacity = 2
        db.commit()
    allowed = desk.staff("desk.a").post(
        f"{_tickets(desk)}/{seen.id}/transfer",
        json={"queue_id": doctor.id, "reason": TransferReason.NEXT_STEP.value},
    )
    assert (
        allowed.json()["ticket"]["number"] == "D002"
    )  # the refused attempt left no gap


def test_the_same_queue_a_called_ticket_and_a_queue_already_held_are_refused(
    desk: SimpleNamespace,
) -> None:
    """Three more refusals, each with its own code, none changing anything."""
    doctor = _add_doctor(desk)
    with desk.session() as db:
        waiting = TicketFactory.create(db, queue=db.get(Queue, desk.triage.id))
        called = drive_ticket_to(
            db,
            TicketFactory.create(db, queue=db.get(Queue, desk.triage.id)),
            TicketStatus.CALLED,
        )
        db.commit()
    client, _ = desk.patient()
    in_triage = client.post(desk.join_path(desk.triage), json={}).json()["ticket"]
    doubled = client.post(desk.join_path(doctor), json={}).json()["ticket"]
    front = desk.staff("desk.a")

    same = front.post(
        f"{_tickets(desk)}/{waiting.id}/transfer",
        json={"queue_id": desk.triage.id, "reason": "wrong_queue"},
    )
    from_called = front.post(
        f"{_tickets(desk)}/{called.id}/transfer",
        json={"queue_id": doctor.id, "reason": "next_step"},
    )
    already = front.post(
        f"{_tickets(desk)}/{in_triage['id']}/transfer",
        json={"queue_id": doctor.id, "reason": "wrong_queue"},
    )

    assert same.json()["code"] == "ticket.transfer.same_queue"
    assert from_called.json()["code"] == "ticket.transition.illegal"
    assert already.json()["code"] == "ticket.transfer.already_there"
    with desk.session() as db:
        assert db.get(Ticket, in_triage["id"]).status == TicketStatus.WAITING.value
        assert db.get(Ticket, doubled["id"]).status == TicketStatus.WAITING.value


def test_a_transfer_lands_by_arrival_order_by_default_or_at_the_back_if_the_clinic_says(
    desk: SimpleNamespace,
) -> None:
    """Placement is a site setting: the default keeps the visit's arrival order; the other is the back."""
    doctor = _add_doctor(desk)
    # A fixed hour of one service day, so every visit and both transfers fall on the same day whatever
    # time the suite runs (two hours before now crossed midnight between 00:00 and 02:00).
    start = (now_sast() - timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    with desk.session() as db:
        doctor_queue = db.get(Queue, doctor.id)
        # Waiting at the doctor: somebody whose visit began at +10 min, and somebody at +30 min.
        early = TicketFactory.create(
            db, queue=doctor_queue, moment=start + timedelta(minutes=10)
        )
        late = TicketFactory.create(
            db, queue=doctor_queue, moment=start + timedelta(minutes=30)
        )
        # In triage: two patients whose visits began at +20 min.
        movers = [
            drive_ticket_to(
                db,
                TicketFactory.create(
                    db,
                    queue=db.get(Queue, desk.triage.id),
                    moment=start + timedelta(minutes=20),
                ),
                TicketStatus.IN_PROGRESS,
            )
            for _ in range(2)
        ]
        db.commit()

        by_arrival = transfer_ticket(
            db,
            movers[0].id,
            doctor_queue,
            actor=_DESK,
            reason=TransferReason.NEXT_STEP,
            moment=start + timedelta(minutes=40),
        )
        db.commit()
        # Between the +10 and the +30 visit: one waiting ahead, and the +30 patient is now behind.
        assert by_arrival.waiting_ahead == 1
        assert early.order_key < by_arrival.ticket.order_key < late.order_key

        db.get(
            Site, desk.triage.site_id
        ).transfer_placement = TransferPlacement.BACK_OF_LINE.value
        db.commit()
        at_back = transfer_ticket(
            db,
            movers[1].id,
            doctor_queue,
            actor=_DESK,
            reason=TransferReason.NEXT_STEP,
            moment=start + timedelta(minutes=41),
        )
        db.commit()
        assert at_back.waiting_ahead == 3


def test_a_clinic_manager_chooses_the_placement_and_the_change_is_audited(
    desk: SimpleNamespace,
) -> None:
    """``GET/PUT /sites/{site_id}/settings/transfers``: arrival order by default, the manager's to change."""
    path = f"/api/v1/sites/{desk.triage.site_id}/settings/transfers"
    manager = desk.staff("manager.a")

    default = manager.get(path).json()
    changed = manager.put(path, json={"transfer_placement": "back_of_line"})
    refused = desk.staff("desk.a").put(
        path, json={"transfer_placement": "arrival_order"}
    )

    assert default["transfer_placement"] == TransferPlacement.ARRIVAL_ORDER.value
    assert changed.json()["transfer_placement"] == TransferPlacement.BACK_OF_LINE.value
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    with desk.session() as db:
        audited = db.scalars(
            select(AuditEvent.context).where(
                AuditEvent.action == AuditAction.UPDATE.value,
                AuditEvent.context.like("transfer placement set to back_of_line%"),
            )
        ).all()
    assert len(audited) == 1


@pytest.mark.postgres
def test_the_migration_gives_every_existing_ticket_a_visit_and_its_call_order(
    empty_database: URL,
) -> None:
    """``0023`` on a database that already has tickets: each gets a visit and keeps its place."""
    run_alembic(empty_database, ("upgrade", "0022"))
    engine = create_engine(empty_database)
    apply_postgres_search_path(engine)
    with sessionmaker(bind=engine)() as db:
        # Raw SQL: at revision 0022 the site table does not have this branch's newer columns yet.
        site_id = db.execute(
            text(
                "INSERT INTO clinicq.site (id, slug, name, sector, status, location, address_line, "
                "city, province, display_mode, display_show_comment, board_language, "
                "announce_audio, reason_retention_days, analytics_enabled, is_active, is_deleted) "
                "VALUES (gen_random_uuid()::text, 'backfill-clinic', 'Backfill Clinic', 'public', "
                "'verified', ST_GeogFromText('SRID=4326;POINT(28.04 -26.20)'), '1 Main Road', "
                "'Johannesburg', 'Gauteng', 'number_only', false, 'en', true, 30, true, true, "
                "false) RETURNING id"
            )
        ).scalar_one()
        queue_id = db.execute(
            text(
                "INSERT INTO clinicq.queue (id, site_id, name, slug, kind, ticket_prefix, "
                "display_order, expected_service_minutes, allows_remote_join, is_active, "
                "is_deleted) VALUES (gen_random_uuid()::text, :site, 'Triage', 'triage', "
                "'triage', 'T', 0, 5, true, true, false) RETURNING id"
            ),
            {"site": site_id},
        ).scalar_one()
        for sequence in (1, 2):
            db.execute(
                text(
                    "INSERT INTO clinicq.ticket (id, site_id, queue_id, service_day, sequence, "
                    "number, reference_code, source, joined_at) VALUES (gen_random_uuid()::text, "
                    ":site, :queue, CURRENT_DATE, :sequence, :number, :code, 'walk_in', now())"
                ),
                {
                    "site": site_id,
                    "queue": queue_id,
                    "sequence": sequence,
                    "number": f"T00{sequence}",
                    "code": f"ABCDE{sequence + 1}",
                },
            )
        db.commit()
    engine.dispose()

    run_alembic(empty_database, ("upgrade", "head"))

    engine = create_engine(empty_database)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT t.sequence, t.order_key, v.site_id = t.site_id, v.started_at = t.joined_at "
                    "FROM clinicq.ticket t JOIN clinicq.visit v ON v.id = t.visit_id ORDER BY t.sequence"
                )
            ).all()
            visits = conn.execute(
                text("SELECT count(*) FROM clinicq.visit")
            ).scalar_one()
    finally:
        engine.dispose()
    assert [tuple(row) for row in rows] == [(1, 1.0, True, True), (2, 2.0, True, True)]
    assert visits == 2
