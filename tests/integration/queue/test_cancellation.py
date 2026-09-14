"""Patient cancellation and positions derived from order (Issue 44).

* **Cancel #3 of 10**: tickets 4–10 each move up one place, read back within 2 seconds, and no other
  ticket's row is written (positions are derived, never stored).
* A **called ticket cannot be self-cancelled**: the patient is told to speak to reception, and the
  desk can still cancel it.
* **All four channels** cancel through the same function with the same outcome, and the audit row
  and the ticket name the channel.
* **Reasons** are optional, from a closed list, and countable for the no-show analysis.
"""

from __future__ import annotations

import time as clock
from datetime import date, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from starlette import status

from src.commons.enums import (
    ActorKind,
    AuditAction,
    AuditEntityType,
    CancellationReason,
    PatientChannel,
    TicketSource,
    TicketStatus,
)
from src.commons.time import business_date, now_sast
from src.core.site_scope import SiteAccess
from src.database.models import AuditEvent, Queue, Ticket, User
from src.modules.patients.service import patient_for_gateway
from src.modules.queue.cancellation import (
    SPEAK_TO_RECEPTION,
    cancel_own_ticket,
    reason_counts,
)
from src.modules.queue.tickets import waiting_ahead
from tests.factories import TicketFactory, drive_ticket_to


def _join(client: TestClient, desk: SimpleNamespace) -> dict:
    return client.post(desk.join_path(desk.triage), json={}).json()["ticket"]


def test_cancelling_3_of_10_moves_4_to_10_up_one_place_within_2_seconds(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 1, with nobody's row rewritten but the cancelled one."""
    patients = [desk.patient() for _ in range(10)]
    tickets = [_join(client, desk) for client, _ in patients]
    before = [
        ahead["waiting_ahead"]
        for ahead in patients[0][0].get("/api/v1/patients/me/tickets").json()
    ]
    with desk.session() as db:
        positions_before = {
            t["id"]: waiting_ahead(db, db.get(Ticket, t["id"])) for t in tickets
        }
        modified_before = {
            t["id"]: db.get(Ticket, t["id"]).modified_at for t in tickets
        }

    started = clock.perf_counter()
    cancelled = patients[2][0].post(
        f"/api/v1/patients/me/tickets/{tickets[2]['id']}/cancel",
        json={"reason": CancellationReason.WAIT_TOO_LONG.value},
    )
    seen = {
        client.get("/api/v1/patients/me/tickets").json()[0]["id"]: client.get(
            "/api/v1/patients/me/tickets"
        ).json()[0]["waiting_ahead"]
        for client, _ in patients[3:]
    }
    elapsed = clock.perf_counter() - started

    assert before == [0]
    assert cancelled.status_code == status.HTTP_200_OK
    assert cancelled.json()["moved_up"] == 7
    assert cancelled.json()["message"] == (
        "Ticket T003 is cancelled. Thank you for giving your place back: 7 people moved up."
    )
    for ticket in tickets[3:]:
        assert seen[ticket["id"]] == positions_before[ticket["id"]] - 1
    assert [seen[t["id"]] for t in tickets[3:]] == [2, 3, 4, 5, 6, 7, 8]
    assert elapsed < 2
    with desk.session() as db:
        for ticket in tickets:
            if ticket["id"] != tickets[2]["id"]:
                assert (
                    db.get(Ticket, ticket["id"]).modified_at
                    == modified_before[ticket["id"]]
                )


def test_a_ticket_has_no_stored_position_to_go_stale() -> None:
    """The model has nothing a recalculation would have to rewrite."""
    columns = {column.key for column in inspect(Ticket).columns}
    assert not {
        name
        for name in columns
        if "position" in name or "place" in name or "rank" in name
    }


def test_a_called_ticket_cannot_be_self_cancelled_and_the_desk_can_cancel_it(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 2: refused with a message to speak to reception; reception can."""
    client, _ = desk.patient()
    ticket = _join(client, desk)
    with desk.session() as db:
        drive_ticket_to(db, db.get(Ticket, ticket["id"]), TicketStatus.CALLED)
        db.commit()

    refused = client.post(f"/api/v1/patients/me/tickets/{ticket['id']}/cancel", json={})
    at_desk = desk.staff("desk.a").post(
        f"/api/v1/sites/{desk.triage.site_id}/tickets/{ticket['id']}/cancel", json={}
    )

    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["detail"] == SPEAK_TO_RECEPTION
    assert refused.json()["code"] == "ticket.cancel.after_call"
    assert at_desk.status_code == status.HTTP_200_OK
    assert at_desk.json()["ticket"]["status"] == TicketStatus.CANCELLED.value


def test_another_patients_ticket_cannot_be_cancelled_and_looks_like_no_ticket(
    desk: SimpleNamespace,
) -> None:
    """A ticket id is not a key: somebody else's is the same 404 as one that does not exist."""
    owner, _ = desk.patient()
    stranger, _ = desk.patient()
    ticket = _join(owner, desk)

    theirs = stranger.post(
        f"/api/v1/patients/me/tickets/{ticket['id']}/cancel", json={}
    )
    nowhere = stranger.post(
        "/api/v1/patients/me/tickets/0199b0c0-0000-7000-8000-000000000000/cancel",
        json={},
    )

    assert theirs.status_code == nowhere.status_code == status.HTTP_404_NOT_FOUND
    assert theirs.json()["detail"] == nowhere.json()["detail"]


def test_cancellation_is_identical_on_all_four_channels_and_audited_with_the_channel(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 3: web and desk over HTTP, USSD and WhatsApp as their adapters will call it."""
    web_client, _ = desk.patient()
    web = _join(web_client, desk)
    with desk.session() as db:
        queue = db.get(Queue, desk.triage.id)
        ussd_patient = patient_for_gateway(
            db, msisdn="+27105550801", channel=PatientChannel.USSD
        )
        wa_patient = patient_for_gateway(
            db, msisdn="+27105550802", channel=PatientChannel.WHATSAPP
        )
        ussd = TicketFactory.create(
            db, queue=queue, source=TicketSource.USSD, patient_id=ussd_patient.id
        )
        whatsapp = TicketFactory.create(
            db, queue=queue, source=TicketSource.WHATSAPP, patient_id=wa_patient.id
        )
        walk_in = TicketFactory.create(db, queue=queue)
        db.commit()
        ids = {"ussd": ussd.id, "whatsapp": whatsapp.id, "walk_in": walk_in.id}

    web_answer = web_client.post(
        f"/api/v1/patients/me/tickets/{web['id']}/cancel", json={}
    )
    desk_answer = desk.staff("desk.a").post(
        f"/api/v1/sites/{desk.triage.site_id}/tickets/{ids['walk_in']}/cancel", json={}
    )
    with desk.session() as db:
        cancel_own_ticket(db, ussd_patient.id, ids["ussd"], channel=PatientChannel.USSD)
        cancel_own_ticket(
            db, wa_patient.id, ids["whatsapp"], channel=PatientChannel.WHATSAPP
        )
        db.commit()
        rows = {
            ticket.id: ticket
            for ticket in db.scalars(
                select(Ticket).where(Ticket.id.in_([web["id"], *ids.values()]))
            )
        }
        audits = {
            row.entity_id: row
            for row in db.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_type == AuditEntityType.TICKET.value,
                    AuditEvent.action == AuditAction.UPDATE.value,
                    AuditEvent.entity_id.in_(rows),
                )
            )
        }

    assert web_answer.status_code == desk_answer.status_code == status.HTTP_200_OK
    assert {t.status for t in rows.values()} == {TicketStatus.CANCELLED.value}
    assert {rows[web["id"]].cancelled_via, rows[ids["ussd"]].cancelled_via} == {
        "web",
        "ussd",
    }
    assert rows[ids["whatsapp"]].cancelled_via == PatientChannel.WHATSAPP.value
    assert rows[ids["walk_in"]].cancelled_via == PatientChannel.WALK_IN.value
    for ticket_id, channel, kind in (
        (web["id"], "web", ActorKind.PATIENT),
        (ids["ussd"], "ussd", ActorKind.PATIENT),
        (ids["whatsapp"], "whatsapp", ActorKind.PATIENT),
        (ids["walk_in"], "walk_in", ActorKind.STAFF),
    ):
        assert audits[ticket_id].context.endswith(
            f"waiting → cancelled (cancelled by {kind.value} via {channel})"
        ), audits[ticket_id].context
        assert audits[ticket_id].actor_role == kind.value


def test_reasons_are_optional_from_a_closed_list_and_counted_for_the_analysis(
    desk: SimpleNamespace,
) -> None:
    """Criterion 6: stored as enum values, refused as free text, and grouped per clinic."""
    clients = [desk.patient() for _ in range(3)]
    tickets = [_join(client, desk) for client, _ in clients]
    free_text = clients[0][0].post(
        f"/api/v1/patients/me/tickets/{tickets[0]['id']}/cancel",
        json={"reason": "too far"},
    )
    for (client, _), ticket, reason in zip(
        clients,
        tickets,
        [CancellationReason.WAIT_TOO_LONG, CancellationReason.WAIT_TOO_LONG, None],
        strict=True,
    ):
        client.post(
            f"/api/v1/patients/me/tickets/{ticket['id']}/cancel",
            json={"reason": reason.value if reason else None},
        )
    with desk.session() as db:
        access = SiteAccess(site_id=desk.triage.site_id, user=User(id="u", email="m@x"))
        today = business_date(now_sast())
        counts = reason_counts(db, access, today - timedelta(days=1), today)
        elsewhere = reason_counts(
            db,
            SiteAccess(
                site_id=desk.other_triage.site_id, user=User(id="u", email="m@x")
            ),
            date(2000, 1, 1),
            today,
        )
    assert free_text.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert counts == {CancellationReason.WAIT_TOO_LONG: 2, None: 1}
    assert elsewhere == {}


def test_a_finished_ticket_cannot_be_cancelled_either(desk: SimpleNamespace) -> None:
    """A done ticket is not reopened by a cancellation: the lifecycle's 409, not the window's."""
    client, _ = desk.patient()
    ticket = _join(client, desk)
    with desk.session() as db:
        drive_ticket_to(db, db.get(Ticket, ticket["id"]), TicketStatus.DONE)
        db.commit()
    refused = client.post(f"/api/v1/patients/me/tickets/{ticket['id']}/cancel", json={})
    assert refused.status_code == status.HTTP_409_CONFLICT
    assert refused.json()["code"] == "ticket.transition.illegal"
