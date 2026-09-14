"""The nurse's room view and private visit notes over HTTP (Issue 53).

Each acceptance criterion, proved without reading HTML (``docs/IDE/RULES/testing-strategy.mdc``):

* **Only their rooms, refused not hidden.** The room page reads only the nurse's queues, and every API
  call on another room's queue or ticket answers 404, exactly like another clinic's. Before this issue
  a nurse could press *Call next* on any queue at the clinic: the ``own`` tier was never turned into rows.
* **A note never reaches a board or a patient.** A note with a distinctive phrase is written, then every
  board, public and patient-facing response is read and searched for it.
* **Attributed, timestamped, encrypted.** The note names its author and time, the database holds only
  ciphertext, and the audit row records the note without its words.
* **Purged on the retention schedule.** A note past its window is gone after the sweep, a newer one
  stays, and the sweep's receipt counts without content.
* **Earlier visits only with consent**, and **a transfer keeps the visit** with its notes.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select, text
from starlette import status
from starlette.testclient import TestClient

from src.commons.enums import (
    ActorKind,
    AuditEntityType,
    ConsentPurpose,
    PatientChannel,
    TicketSource,
    TicketStatus,
)
from src.commons.time import now_sast
from src.core import scheduler
from src.core.config import Settings
from src.core.site_scope import SiteAccess
from src.database.models import (
    AuditEvent,
    Patient,
    Queue,
    Site,
    Ticket,
    User,
    VisitNote,
)
from src.modules.patients.consent import record_consent
from src.modules.queue.lifecycle import Actor, call_next
from src.modules.queue.sequence import issue_ticket
from src.modules.visits import notes as visit_notes

#: A phrase no screen could contain by accident, so finding it anywhere is a leak.
SECRET_NOTE = "ZEBRA-42 chest pain, query angina"


def _api(dashboard: SimpleNamespace) -> str:
    """Clinic A's staff API root."""
    return f"/api/v1/sites/{dashboard.site_a}"


def _called_ticket(
    dashboard: SimpleNamespace, queue_id: str, *, patient_id: str | None = None
) -> str:
    """Issue a ticket in ``queue_id`` and call it; return its id."""
    with dashboard.session() as db:
        queue = db.get(Queue, queue_id)
        issue_ticket(
            db, queue=queue, source=TicketSource.WALK_IN, patient_id=patient_id
        )
        db.commit()
        ticket = call_next(db, queue, actor=Actor(kind=ActorKind.STAFF, label="desk.a"))
        db.commit()
        return ticket.id


def test_a_note_is_attributed_timestamped_encrypted_and_audited_without_its_words(
    dashboard: SimpleNamespace,
) -> None:
    """The nurse writes on a patient in Room 2; the row holds ciphertext; the audit row holds no text."""
    ticket = _called_ticket(dashboard, dashboard.triage)
    nurse = dashboard.client("nurse.a")
    written = nurse.post(
        f"{_api(dashboard)}/tickets/{ticket}/notes", json={"text": f"  {SECRET_NOTE}  "}
    )
    assert written.status_code == status.HTTP_201_CREATED, written.text
    body = written.json()
    assert body["text"] == SECRET_NOTE
    assert body["author"] == "nurse.a@clinicq.example"
    assert body["created_at"].endswith("+02:00")
    assert body["expires_at"] > body["created_at"]

    with dashboard.session() as db:
        stored = db.execute(text("SELECT note_text FROM visit_note")).scalar_one()
        assert SECRET_NOTE not in stored and "ZEBRA" not in stored
        assert db.execute(select(VisitNote)).scalar_one().note_text == SECRET_NOTE
        audit = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.VISIT_NOTE.value
            )
        ).scalar_one()
    assert audit.actor == "nurse.a@clinicq.example"
    assert "ZEBRA" not in (audit.context or "") and not audit.diff

    read = nurse.get(f"{_api(dashboard)}/tickets/{ticket}/notes").json()
    assert [note["text"] for note in read["notes"]] == [SECRET_NOTE]


def test_a_note_on_a_patient_who_has_not_been_seen_is_refused(
    dashboard: SimpleNamespace,
) -> None:
    """A waiting ticket: 409 ``ticket.note.not_seen``, and nothing written."""
    with dashboard.session() as db:
        waiting = issue_ticket(
            db, queue=db.get(Queue, dashboard.triage), source=TicketSource.WALK_IN
        ).id
        db.commit()
    refused = dashboard.client("nurse.a").post(
        f"{_api(dashboard)}/tickets/{waiting}/notes", json={"text": "early"}
    )
    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["code"] == visit_notes.NOT_SEEN_CODE
    with dashboard.session() as db:
        assert db.execute(select(VisitNote)).scalars().all() == []


def test_a_nurse_sees_only_their_rooms_and_another_rooms_calls_are_refused(
    dashboard: SimpleNamespace,
) -> None:
    """Room 2's nurse: the Pharmacy is not on the page, and every call on it is the same 404 as another clinic's."""
    pharmacy_ticket = _called_ticket(dashboard, dashboard.pharmacy)
    with dashboard.session() as db:
        issue_ticket(
            db, queue=db.get(Queue, dashboard.pharmacy), source=TicketSource.WALK_IN
        )
        issue_ticket(
            db, queue=db.get(Queue, dashboard.triage), source=TicketSource.WALK_IN
        )
        db.commit()
    nurse = dashboard.client("nurse.a")
    api = _api(dashboard)

    room = nurse.get(dashboard.page(dashboard.site_a, "room"))
    assert room.status_code == status.HTTP_200_OK
    assert [current.card.id for current in room.context["room"].rooms] == [
        dashboard.triage
    ]

    refusals = {
        "call next in the Pharmacy": nurse.post(
            f"{api}/queues/{dashboard.pharmacy}/tickets/call-next"
        ),
        "peek at the Pharmacy": nurse.get(
            f"{api}/queues/{dashboard.pharmacy}/tickets/next"
        ),
        "finish a Pharmacy patient": nurse.post(
            f"{api}/tickets/{pharmacy_ticket}/transitions",
            json={"to": TicketStatus.IN_PROGRESS.value},
        ),
        "read a Pharmacy patient's notes": nurse.get(
            f"{api}/tickets/{pharmacy_ticket}/notes"
        ),
        "write on a Pharmacy patient": nurse.post(
            f"{api}/tickets/{pharmacy_ticket}/notes", json={"text": "x"}
        ),
        "issue a walk-in into the Pharmacy": nurse.post(
            f"{api}/queues/{dashboard.pharmacy}/tickets", json={}
        ),
    }
    for what, response in refusals.items():
        assert response.status_code == status.HTTP_404_NOT_FOUND, (what, response.text)
    # The same body as a clinic that does not exist: the refusal says nothing about the Pharmacy.
    elsewhere = nurse.get(
        f"/api/v1/sites/0199b0c0-0000-7000-8000-0000000dffff/tickets/{pharmacy_ticket}/notes"
    )
    assert (
        refusals["read a Pharmacy patient's notes"].json()["detail"]
        == elsewhere.json()["detail"]
    )
    # Their own room works.
    assert (
        nurse.post(f"{api}/queues/{dashboard.triage}/tickets/call-next").status_code
        == status.HTTP_200_OK
    )
    # The front desk is untouched by the narrowing: the receptionist still calls in any queue.
    assert (
        dashboard.client("desk.a")
        .post(f"{api}/queues/{dashboard.pharmacy}/tickets/call-next")
        .status_code
        == status.HTTP_200_OK
    )
    # And notes are a clinician's: the front desk and the manager are refused outright.
    for name in ("desk.a", "manager.a"):
        assert (
            dashboard.client(name)
            .get(f"{api}/tickets/{pharmacy_ticket}/notes")
            .status_code
            == status.HTTP_403_FORBIDDEN
        )


def test_a_note_never_appears_in_any_board_or_patient_facing_response(
    dashboard: SimpleNamespace,
) -> None:
    """Write a note on a patient who joined from their phone, then read everything they and the public see."""
    patient, _patient_id = dashboard.patient()
    joined = patient.post(
        f"/api/v1/clinics/{dashboard.site_a}/queues/{dashboard.triage}/tickets", json={}
    )
    assert joined.status_code == status.HTTP_201_CREATED, joined.text
    nurse = dashboard.client("nurse.a")
    called = nurse.post(
        f"{_api(dashboard)}/queues/{dashboard.triage}/tickets/call-next"
    )
    assert called.json()["id"] == joined.json()["ticket"]["id"]
    ticket_id = called.json()["id"]
    assert (
        nurse.post(
            f"{_api(dashboard)}/tickets/{ticket_id}/notes", json={"text": SECRET_NOTE}
        ).status_code
        == 201
    )

    with dashboard.session() as db:
        slug = db.get(Site, dashboard.site_a).slug
    desk = dashboard.client("desk.a")
    public = TestClient(dashboard.app)
    responses = {
        "front desk board page": desk.get(dashboard.page(dashboard.site_a, "board")),
        "front desk board cards": desk.get(
            dashboard.page(dashboard.site_a, "board/cards")
        ),
        "clinic tickets API": desk.get(f"{_api(dashboard)}/tickets"),
        "clinic visits API": desk.get(f"{_api(dashboard)}/visits"),
        "patient's own tickets": patient.get("/api/v1/patients/me/tickets"),
        "patient's own record": patient.get("/api/v1/patients/me"),
        "public clinic profile": public.get(f"/api/v1/clinics/{slug}"),
    }
    for where, response in responses.items():
        assert response.status_code < 500, where
        assert "ZEBRA" not in response.text, f"the note leaked into the {where}"
    # A live screen only ever hears that a queue changed; no event is published for a note at all.
    openapi = public.get("/openapi.json").json()
    for path, operations in openapi["paths"].items():
        if path.startswith(("/api/v1/clinics", "/api/v1/patients")):
            assert "note" not in str(operations).lower() or "notes" not in path, path
    assert not any(
        "note_text" in str(schema) or "VisitNote" in name
        for name, schema in openapi["components"]["schemas"].items()
        if name.startswith(("Join", "MyTicket", "Clinic", "Nearby", "Patient"))
    )


def test_expired_notes_are_purged_by_the_nightly_sweep_and_the_receipt_counts_without_words(
    dashboard: SimpleNamespace,
) -> None:
    """A note written 31 days ago is gone after the sweep; yesterday's stays; the receipt has only a count."""
    ticket = _called_ticket(dashboard, dashboard.triage)
    now = now_sast()
    with dashboard.session() as db:
        user = db.get(User, dashboard.ids["nurse.a"])
        access = SiteAccess(site_id=dashboard.site_a, user=user)
        actor = Actor(kind=ActorKind.STAFF, label="nurse.a@clinicq.example")
        visit_notes.add_note(
            db, access, ticket, "old note", actor=actor, moment=now - timedelta(days=31)
        )
        visit_notes.add_note(
            db, access, ticket, "new note", actor=actor, moment=now - timedelta(days=1)
        )
        db.commit()
        # Before the sweep, the expired note is already never returned.
        assert [
            note.note_text for note in visit_notes.read_notes(db, access, ticket).notes
        ] == ["new note"]
        purged = visit_notes.purge_expired_notes(db, moment=now)
        db.commit()
        remaining = [note.note_text for note in db.execute(select(VisitNote)).scalars()]
        receipt = db.execute(
            select(AuditEvent).where(AuditEvent.entity_id == "retention-sweep")
        ).scalar_one()
    assert purged == 1
    assert remaining == ["new note"]
    assert receipt.context == "purged 1 expired visit note(s)"

    settings = Settings(_env_file=None, scheduler_enabled=True)  # type: ignore[call-arg]
    started = scheduler.start_scheduler(settings)
    try:
        assert started is not None
        (job,) = [
            job for job in started.get_jobs() if job.id == "visit_note_retention_sweep"
        ]
        assert job.func is scheduler.run_visit_note_retention_sweep
    finally:
        scheduler.shutdown_scheduler()


def test_earlier_visits_notes_are_shown_only_while_the_patient_agrees(
    dashboard: SimpleNamespace,
) -> None:
    """Without consent, withheld with the reason; with it, shown; withdrawn, withheld again at the next read."""
    _patient, patient_id = dashboard.patient()
    first = _called_ticket(dashboard, dashboard.triage, patient_id=patient_id)
    nurse = dashboard.client("nurse.a")
    api = _api(dashboard)
    assert (
        nurse.post(
            f"{api}/tickets/{first}/notes", json={"text": "first visit note"}
        ).status_code
        == 201
    )
    with dashboard.session() as db:
        db.execute(
            text("UPDATE ticket SET status = 'done' WHERE id = :id"), {"id": first}
        )
        db.commit()
    second = _called_ticket(dashboard, dashboard.triage, patient_id=patient_id)

    withheld = nurse.get(f"{api}/tickets/{second}/notes").json()
    assert withheld["notes"] == [] and withheld["previous"] is None
    assert withheld["previous_withheld"] == visit_notes.HISTORY_NOT_SHARED

    with dashboard.session() as db:
        record = db.get(Patient, patient_id)
        record_consent(
            db,
            record,
            ConsentPurpose.VISIT_NOTE_HISTORY,
            granted=True,
            channel=PatientChannel.WEB,
        )
        db.commit()
    shared = nurse.get(f"{api}/tickets/{second}/notes").json()
    assert [note["text"] for note in shared["previous"]] == ["first visit note"]

    with dashboard.session() as db:
        record = db.get(Patient, patient_id)
        record_consent(
            db,
            record,
            ConsentPurpose.VISIT_NOTE_HISTORY,
            granted=False,
            channel=PatientChannel.WEB,
        )
        db.commit()
    assert nurse.get(f"{api}/tickets/{second}/notes").json()["previous"] is None


def test_a_transfer_from_the_room_keeps_the_visit_and_its_notes(
    dashboard: SimpleNamespace,
) -> None:
    """The nurse sends Room 2's patient to the Pharmacy (not their room): one visit, two legs, the note kept."""
    ticket = _called_ticket(dashboard, dashboard.triage)
    nurse = dashboard.client("nurse.a")
    api = _api(dashboard)
    assert (
        nurse.post(
            f"{api}/tickets/{ticket}/transitions", json={"to": "in_progress"}
        ).status_code
        == 200
    )
    assert (
        nurse.post(
            f"{api}/tickets/{ticket}/notes", json={"text": "for the pharmacist"}
        ).status_code
        == 201
    )

    moved = nurse.post(
        f"{api}/tickets/{ticket}/transfer",
        json={"queue_id": dashboard.pharmacy, "reason": "next_step"},
    )
    assert moved.status_code == status.HTTP_200_OK, moved.text
    visit_id = moved.json()["visit_id"]

    legs = dashboard.client("desk.a").get(f"{api}/visits/{visit_id}").json()["legs"]
    assert [leg["queue_id"] for leg in legs] == [dashboard.triage, dashboard.pharmacy]
    with dashboard.session() as db:
        (note,) = db.execute(select(VisitNote)).scalars().all()
        new_ticket = db.get(Ticket, moved.json()["ticket"]["id"])
    assert note.visit_id == visit_id == new_ticket.visit_id
    # The patient has left Room 2, so the nurse no longer reaches the new ticket: another room.
    assert (
        nurse.get(f"{api}/tickets/{new_ticket.id}/notes").status_code
        == status.HTTP_404_NOT_FOUND
    )
