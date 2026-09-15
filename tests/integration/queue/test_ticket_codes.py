"""Finding a ticket at reception by its QR or code, and the code every surface carries (Issue 70).

Against the queue fixture's two clinics, the real lookup route and page, as JSON:

* **scanning the QR opens that exact ticket**: the payload from the patient's page data, sent as a desk scanner
  types it, answers with that ticket; so does the code typed in lower case with a space;
* **the page, its offline copy and the stub carry the same code**: the QR in the page data and on the printed
  stub are the same symbol for the same reference code;
* **a code from a previous day is refused, naming the day**, and an ended ticket's code is used up;
* **a code cannot be turned into another patient's ticket**: codes are unique and unrelated from one ticket to
  the next, a ticket's number or id opens nothing, and another clinic's code is "not found", word for word
  like a code that does not exist;
* **a transferred ticket's code follows the visit** to the leg that is open now.
"""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update
from starlette import status

from src.commons.enums import (
    ActorKind,
    CancellationReason,
    TicketStatus,
    TransferReason,
)
from src.commons.time import business_date
from src.database.models import Queue, Ticket
from src.modules.queue.lifecycle import Actor, call_next, transition_ticket
from src.modules.queue.ticket_codes import qr_for
from tests.integration.queue.conftest import SITE_A, SITE_B

_DESK = Actor(kind=ActorKind.STAFF, label="desk.a@clinicq.example")


def _join(desk: SimpleNamespace) -> dict[str, Any]:
    client, _ = desk.patient()
    answer = client.post(desk.join_path(desk.triage), json={})
    assert answer.status_code == status.HTTP_201_CREATED, answer.text
    return answer.json()


def _page(page_url: str, desk: SimpleNamespace) -> dict[str, Any]:
    response = TestClient(desk.app).get(
        f"/api/v1/tickets/{page_url.removeprefix('/t/')}"
    )
    assert response.status_code == status.HTTP_200_OK
    return response.json()


def _lookup(client: TestClient, code: str, site_id: str = SITE_A) -> Any:
    return client.get(f"/api/v1/sites/{site_id}/tickets/lookup", params={"code": code})


def test_scanning_the_qr_from_the_ticket_page_opens_that_exact_ticket(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 1, with the payload a desk scanner types."""
    for _ in range(2):
        _join(desk)
    mine = _join(desk)
    page = _page(mine["page_url"], desk)
    staff = desk.staff("desk.a")

    scanned = _lookup(staff, page["reference_qr"]["payload"])
    assert scanned.status_code == status.HTTP_200_OK, scanned.text
    body = scanned.json()
    assert body["ticket"]["id"] == mine["ticket"]["id"]
    assert body["ticket"]["number"] == page["number"] and body["waiting_ahead"] == 2
    assert body["message"] == f"{page['number']} is waiting in Triage, 2 ahead."
    assert body["reference_spoken"] == page["reference_spoken"]

    typed = _lookup(staff, page["reference_code"].lower().replace("-", " "))
    assert typed.json()["ticket"]["id"] == mine["ticket"]["id"]


def test_the_page_and_the_printed_stub_carry_the_same_code(
    desk: SimpleNamespace,
) -> None:
    staff = desk.staff("desk.a")
    walk_in = staff.post(desk.walk_in_path(desk.triage), json={"name": "Thandi"}).json()
    ticket = walk_in["ticket"]
    page = _page(walk_in["page_url"], desk)

    stored = ticket["reference_code"].replace("-", "")
    assert page["reference_code"] == ticket["reference_code"]
    assert page["reference_qr"] == {
        "payload": f"CLINICQ:{ticket['reference_code']}",
        "size": qr_for(stored).size,
        "path": qr_for(stored).path,
    }

    stub = staff.get(f"/dashboard/sites/{SITE_A}/walk-in/tickets/{ticket['id']}/stub")
    assert stub.status_code == status.HTTP_200_OK
    shown = stub.context["stub"]
    assert shown.reference_code == page["reference_code"]
    assert (shown.reference_qr.payload, shown.reference_qr.path) == (
        page["reference_qr"]["payload"],
        page["reference_qr"]["path"],
    )
    assert f'd="{page["reference_qr"]["path"]}"' in stub.text


def test_a_code_from_a_previous_day_is_rejected_with_a_clear_message(
    desk: SimpleNamespace,
) -> None:
    """How to verify, step 3."""
    mine = _join(desk)
    yesterday = business_date() - timedelta(days=1)
    with desk.session() as db:
        db.execute(
            update(Ticket)
            .where(Ticket.id == mine["ticket"]["id"])
            .values(service_day=yesterday)
        )
        db.commit()

    refused = _lookup(desk.staff("desk.a"), mine["ticket"]["reference_code"])
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert refused.json()["code"] == "queue.lookup.expired"
    assert refused.json()["detail"] == (
        f"Code {mine['ticket']['reference_code']} was for {yesterday:%A %d %B %Y}. A ticket code works only on "
        "the day it was issued: the patient needs a new ticket today."
    )


def test_an_ended_tickets_code_is_used_up_and_a_transfer_follows_the_visit(
    desk: SimpleNamespace,
) -> None:
    staff = desk.staff("desk.a")
    moving = _join(desk)
    with desk.session() as db:
        call_next(db, db.get_one(Queue, desk.triage.id), actor=_DESK)
        transition_ticket(
            db, moving["ticket"]["id"], TicketStatus.IN_PROGRESS, actor=_DESK
        )
        db.commit()
    moved = staff.post(
        f"/api/v1/sites/{SITE_A}/tickets/{moving['ticket']['id']}/transfer",
        json={"queue_id": desk.pharmacy.id, "reason": TransferReason.NEXT_STEP.value},
    )
    assert moved.status_code == status.HTTP_200_OK, moved.text

    followed = _lookup(staff, moving["ticket"]["reference_code"]).json()
    assert followed["ticket"]["id"] != moving["ticket"]["id"]
    assert (
        followed["queue_name"] == "Pharmacy"
        and followed["followed_from"] == moving["ticket"]["number"]
    )
    assert followed["message"].endswith(
        f", moved on from {moving['ticket']['number']}."
    )

    done = _join(desk)
    cancel = staff.post(
        f"/api/v1/sites/{SITE_A}/tickets/{done['ticket']['id']}/cancel",
        json={"reason": CancellationReason.WENT_ELSEWHERE.value},
    )
    assert cancel.status_code == status.HTTP_200_OK, cancel.text
    ended = _lookup(staff, f"CLINICQ:{done['ticket']['reference_code']}")
    assert ended.status_code == status.HTTP_409_CONFLICT
    assert ended.json()["code"] == "queue.lookup.ended"
    assert "cannot be used again" in ended.json()["detail"]


def test_a_code_cannot_be_turned_into_another_patients_ticket(
    desk: SimpleNamespace,
) -> None:
    tickets = [_join(desk)["ticket"] for _ in range(30)]
    codes = [ticket["reference_code"] for ticket in tickets]
    assert len(set(codes)) == len(codes)
    # Consecutive tickets share no leading characters more often than chance would (31 symbols).
    shared = sum(a[:2] == b[:2] for a, b in pairwise(codes))
    assert shared <= 3, codes

    staff = desk.staff("desk.a")
    first = tickets[0]
    for not_a_code in (
        first["number"],
        first["id"],
        "0" + first["reference_code"][1:],
    ):
        answer = _lookup(staff, not_a_code)
        assert answer.status_code in (
            status.HTTP_404_NOT_FOUND,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
        assert answer.json()["code"] in (
            "queue.lookup.not_found",
            "queue.lookup.not_a_code",
        )

    # Clinic B's desk cannot find clinic A's ticket, and is told exactly what a made-up code gets.
    other_clinic = _lookup(
        desk.staff("desk.b"), first["reference_code"], site_id=SITE_B
    )
    assert other_clinic.status_code == status.HTTP_404_NOT_FOUND
    made_up = _lookup(desk.staff("desk.b"), "ZZZ-ZZZ", site_id=SITE_B)
    assert (
        other_clinic.json()["code"]
        == made_up.json()["code"]
        == "queue.lookup.not_found"
    )
    assert (
        other_clinic.json()["detail"].replace(first["reference_code"], "ZZZ-ZZZ")
        == made_up.json()["detail"]
    )
    # Nor can a patient, or anyone signed out, look codes up at all.
    patient, _ = desk.patient()
    assert _lookup(patient, first["reference_code"]).status_code in (401, 403)
    assert (
        _lookup(TestClient(desk.app), first["reference_code"]).status_code
        == status.HTTP_401_UNAUTHORIZED
    )


def test_the_reception_page_shows_the_ticket_for_a_scan_and_says_why_otherwise(
    desk: SimpleNamespace,
) -> None:
    mine = _join(desk)
    staff = desk.staff("desk.a")
    path = f"/dashboard/sites/{SITE_A}/lookup"

    empty = staff.get(path)
    assert empty.status_code == status.HTTP_200_OK
    assert empty.context["lookup"]["found"] is None

    scanned = staff.get(
        path, params={"code": f"CLINICQ:{mine['ticket']['reference_code']}"}
    )
    view = scanned.context["lookup"]
    assert view["found"].ticket.id == mine["ticket"]["id"] and view["refusal"] is None
    assert scanned.headers["cache-control"] == "no-store"

    typo = staff.get(path, params={"code": "K0M-4QP"}).context["lookup"]
    assert typo["found"] is None and typo["refusal_kind"] == "not_a_code"
    assert typo["refusal"].startswith("That is not a ticket code.")

    assert desk.staff("desk.b").get(path).status_code in (
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    )
