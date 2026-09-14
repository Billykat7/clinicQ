"""Clinical priority overrides, with a reason, on the record, and never on the public board (Issue 46).

* An override **without a reason code does not save**: refused by the request schema over HTTP and,
  separately, by the service when it is called without one; either way no row is written.
* Every override writes a **``queue_reorder`` row with the positions before and after, and an audit
  row**, in one transaction.
* An override **cannot move a ticket ahead of one already in progress** (or called).
* Affected patients' **positions change on the next read**, derived from the order, within 2 seconds.
* Override **counts per staff member are reportable**, listed by name and said not to be a ranking.
* The **trail is visible to the clinic manager**, and **nothing about priority reaches a public or
  patient-facing shape**.
"""

from __future__ import annotations

import time as clock
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette import status

from src.commons.enums import (
    ActorKind,
    AuditAction,
    AuditEntityType,
    PriorityReason,
    TicketStatus,
)
from src.core.audit import REDACTED
from src.database.models import AuditEvent, Queue, QueueReorder, Ticket
from src.main import create_app
from src.modules.patients.consent import BoardEntry
from src.modules.queue.lifecycle import Actor
from src.modules.queue.priority import (
    AHEAD_OF_CALLED,
    COUNTS_ARE_NOT_RANKINGS,
    PriorityReasonRequiredError,
    override_priority,
)
from src.modules.queue.tickets import waiting_ahead
from tests.factories import TicketFactory, drive_ticket_to


def _line(desk: SimpleNamespace, size: int) -> list[str]:
    """``size`` walk-ins waiting in Triage, T001 first; their ids."""
    with desk.session() as db:
        queue = db.get(Queue, desk.triage.id)
        ids = [TicketFactory.create(db, queue=queue).id for _ in range(size)]
        db.commit()
    return ids


def _priority(desk: SimpleNamespace, ticket_id: str) -> str:
    return f"/api/v1/sites/{desk.triage.site_id}/tickets/{ticket_id}/priority"


def test_an_override_without_a_reason_code_does_not_save(desk: SimpleNamespace) -> None:
    """How to verify, step 1: refused over HTTP and refused by the service; no row either way."""
    line = _line(desk, 5)
    front = desk.staff("desk.a")

    no_reason = front.post(
        _priority(desk, line[4]), json={"ahead_of_ticket_id": line[1]}
    )
    free_text = front.post(
        _priority(desk, line[4]),
        json={"ahead_of_ticket_id": line[1], "reason": "looks poorly"},
    )
    with desk.session() as db:
        try:
            override_priority(
                db,
                line[4],
                ahead_of_ticket_id=line[1],
                reason=None,
                actor=Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example"),
            )
        except PriorityReasonRequiredError as refused:
            service_refusal = refused
        db.commit()
        reorders = db.scalar(select(func.count(QueueReorder.id)))
        order_keys = [db.get(Ticket, ticket_id).order_key for ticket_id in line]

    assert (
        no_reason.status_code
        == free_text.status_code
        == status.HTTP_422_UNPROCESSABLE_CONTENT
    )
    assert service_refusal.status_code == 422
    assert str(service_refusal) == "A priority override needs a reason."
    assert reorders == 0
    assert order_keys == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_an_override_writes_a_reorder_row_and_an_audit_row_and_moves_everyone_behind(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 3, and criterion 3: the record, the proof, and positions within 2 s."""
    line = _line(desk, 7)
    patients_view = dict.fromkeys(line)
    front = desk.staff("desk.a")

    started = clock.perf_counter()
    moved = front.post(
        _priority(desk, line[6]),
        json={
            "ahead_of_ticket_id": line[1],
            "reason": PriorityReason.VISIBLY_UNWELL.value,
            "note": "short of breath",
        },
    )
    with desk.session() as db:
        for ticket_id in line:
            patients_view[ticket_id] = waiting_ahead(db, db.get(Ticket, ticket_id))
    elapsed = clock.perf_counter() - started

    assert moved.status_code == status.HTTP_200_OK, moved.text
    body = moved.json()
    assert (body["reorder"]["position_before"], body["reorder"]["position_after"]) == (
        7,
        2,
    )
    assert body["waiting_ahead"] == 1
    assert body["reorder"]["reason"] == "visibly_unwell"
    assert body["reorder"]["staff"] == "desk.a@clinicq.example"
    # T007 is now second; T002–T006 each moved back one place; T001 is untouched.
    assert [patients_view[t] for t in line] == [0, 2, 3, 4, 5, 6, 1]
    assert elapsed < 2
    with desk.session() as db:
        reorder = db.execute(select(QueueReorder)).scalar_one()
        audit = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == AuditEntityType.TICKET.value,
                AuditEvent.entity_id == line[6],
                AuditEvent.action == AuditAction.UPDATE.value,
            )
        ).scalar_one()
        untouched = [db.get(Ticket, t).order_key for t in line[:6]]
    assert (reorder.position_before, reorder.position_after) == (7, 2)
    assert reorder.note == "short of breath"
    assert (
        audit.actor == "desk.a@clinicq.example"
        and audit.actor_role == ActorKind.STAFF.value
    )
    assert audit.diff["position"] == {"before": 7, "after": 2}
    assert audit.diff["note"]["after"] == REDACTED
    assert (
        audit.context == "T007: priority override, place 7 → 2, reason visibly_unwell"
    )
    assert untouched == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]  # nobody else's row was written


def test_an_override_cannot_move_a_ticket_ahead_of_one_already_in_progress(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 2: ahead of an in-progress (or called) patient is refused."""
    line = _line(desk, 3)
    with desk.session() as db:
        drive_ticket_to(db, db.get(Ticket, line[0]), TicketStatus.IN_PROGRESS)
        drive_ticket_to(db, db.get(Ticket, line[1]), TicketStatus.CALLED)
        db.commit()
    front = desk.staff("desk.a")

    ahead_of_seen = front.post(
        _priority(desk, line[2]),
        json={"ahead_of_ticket_id": line[0], "reason": "elderly"},
    )
    ahead_of_called = front.post(
        _priority(desk, line[2]),
        json={"ahead_of_ticket_id": line[1], "reason": "elderly"},
    )

    for refused in (ahead_of_seen, ahead_of_called):
        assert refused.status_code == status.HTTP_409_CONFLICT
        assert refused.json()["code"] == "ticket.priority.ahead_of_called"
        assert refused.json()["detail"] == AHEAD_OF_CALLED
    with desk.session() as db:
        assert db.scalar(select(func.count(QueueReorder.id))) == 0


def test_other_refusals_move_nothing(desk: SimpleNamespace) -> None:
    """Moving a patient back, and naming a ticket in another queue, are refused with their own codes."""
    line = _line(desk, 3)
    with desk.session() as db:
        other = TicketFactory.create(db, queue=db.get(Queue, desk.pharmacy.id))
        db.commit()
    front = desk.staff("desk.a")

    backwards = front.post(
        _priority(desk, line[0]),
        json={"ahead_of_ticket_id": line[2], "reason": "infant"},
    )
    elsewhere = front.post(
        _priority(desk, line[2]),
        json={"ahead_of_ticket_id": other.id, "reason": "infant"},
    )

    assert backwards.json()["code"] == "ticket.priority.not_forward"
    assert elsewhere.json()["code"] == "ticket.priority.other_queue"


def test_the_trail_is_visible_to_the_clinic_manager_and_counts_are_not_a_ranking(
    desk: SimpleNamespace,
) -> None:
    """Criteria 4 and 6: the manager reads the trail and the counts; counts are by name, not by count."""
    line = _line(desk, 6)
    desk.staff("desk.a").post(
        _priority(desk, line[5]),
        json={"ahead_of_ticket_id": line[0], "reason": "pregnancy"},
    )
    manager = desk.staff("manager.a")
    manager.post(
        _priority(desk, line[4]),
        json={"ahead_of_ticket_id": line[0], "reason": "infant"},
    )
    manager.post(
        _priority(desk, line[3]),
        json={"ahead_of_ticket_id": line[0], "reason": "elderly"},
    )

    trail = manager.get(f"/api/v1/sites/{desk.triage.site_id}/tickets/reorders").json()
    counts = manager.get(
        f"/api/v1/sites/{desk.triage.site_id}/tickets/reorders/counts"
    ).json()
    desk_counts = desk.staff("desk.a").get(
        f"/api/v1/sites/{desk.triage.site_id}/tickets/reorders/counts"
    )

    assert trail["total"] == 3
    assert [item["reason"] for item in trail["items"]] == [
        "elderly",
        "infant",
        "pregnancy",
    ]
    # Listed by name: desk.a (1) before manager.a (2), although manager.a made more.
    assert [(c["staff"], c["overrides"]) for c in counts["items"]] == [
        ("desk.a@clinicq.example", 1),
        ("manager.a@clinicq.example", 2),
    ]
    assert counts["note"] == COUNTS_ARE_NOT_RANKINGS
    # No field an interface could sort a league table by: only a name and a count.
    assert {key for item in counts["items"] for key in item} == {"staff", "overrides"}
    assert (
        desk_counts.status_code == status.HTTP_403_FORBIDDEN
    )  # reports are the manager's


#: Words that would put priority, or the reason for it, where the public or a patient could see it.
_PRIORITY_WORDS = ("priority", "reorder", "reason_code", "override")


def _schema_names_reachable(document: dict, path_filter) -> set[str]:
    """Every component schema referenced, directly or not, by the operations ``path_filter`` accepts."""
    schemas = document["components"]["schemas"]
    seen: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in seen:
                    seen.add(name)
                    walk(schemas.get(name, {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path, operations in document["paths"].items():
        if path_filter(path):
            walk(operations)
    return seen


def test_nothing_about_priority_reaches_a_public_or_patient_facing_shape() -> None:
    """The discovery API (public), the patient's own routes and the board's projection carry none of it."""
    document = TestClient(create_app()).get("/openapi.json").json()
    public = _schema_names_reachable(
        document,
        lambda path: path.startswith(("/api/v1/clinics", "/api/v1/patients")),
    )
    schemas = document["components"]["schemas"]
    leaks = [
        f"{name}.{field}"
        for name in public
        for field in schemas[name].get("properties", {})
        if any(word in field.lower() for word in _PRIORITY_WORDS)
    ]
    assert public, "the walk found no public schemas; the guard would pass vacuously"
    assert leaks == []
    assert set(BoardEntry.__dataclass_fields__) == {"ticket_number", "name", "comment"}
