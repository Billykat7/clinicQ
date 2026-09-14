"""Reordering from the front desk, its trail and the manager's view, over HTTP (Issue 52).

The rules of an override are Issue 46's and are tested there. What this file proves is what the
dashboard adds on top, without reading HTML (``docs/IDE/RULES/testing-strategy.mdc``): the board's
context is the data its waiting lines, badges and trail render from, and the priority route is the one
request both gestures send.

* The line is in call order, a moved ticket carries the staff-only badge, and the trail is on the card.
* Dragging and tapping send one request shape; the same move lands the same way in two queues.
* A move without a reason is not saved, and the line on the next read is unchanged.
* A role without the permission gets the line with its controls **disabled**, reads no badge or trail,
  and is refused by the API.
* The manager sees the day's overrides with counts per staff member, listed by name.
* Nothing about priority reaches a patient's own ticket or a waiting-room board template.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select
from starlette import status

from src.commons.enums import (
    GrantScope,
    PermissionEffect,
    PermissionVerb,
    PriorityReason,
    TicketSource,
)
from src.database.models import Queue, QueueReorder, RolePermission, Ticket
from src.modules.queue.priority import COUNTS_ARE_NOT_RANKINGS, PRIORITY_REASON_LABELS
from src.modules.queue.sequence import issue_ticket
from src.web.dashboard.reorder import PRIORITY_RESOURCE
from tests.integration.dashboard.conftest import TRAINEE_ROLE

TEMPLATES = Path(__file__).resolve().parents[3] / "src" / "templates"


def _line(dashboard: SimpleNamespace, queue_id: str, size: int) -> list[str]:
    """Issue ``size`` walk-ins into a queue in order; return their ids."""
    with dashboard.session() as db:
        queue = db.get(Queue, queue_id)
        ids = [
            issue_ticket(
                db, queue=queue, source=TicketSource.WALK_IN, walk_in_name=f"W{n}"
            ).id
            for n in range(1, size + 1)
        ]
        db.commit()
    return ids


def _priority(dashboard: SimpleNamespace, ticket_id: str) -> str:
    """The one endpoint both gestures post to."""
    return f"/api/v1/sites/{dashboard.site_a}/tickets/{ticket_id}/priority"


def _board(client, dashboard: SimpleNamespace):
    """The front desk at clinic A; asserts it opened."""
    response = client.get(dashboard.page(dashboard.site_a, "board"))
    assert response.status_code == status.HTTP_200_OK, response.text[:200]
    return response


def _numbers(response, queue_id: str) -> list[str]:
    """A queue's waiting line on the board, as ticket numbers in call order."""
    return [ticket.number for ticket in response.context["lines"][queue_id].waiting]


def _move(
    ticket_id: str, ahead_of: str, reason: str | None, note: str | None = None
) -> dict:
    """The body dashboard-reorder.js sends for a move, dragged or tapped."""
    body: dict[str, str] = {"ahead_of_ticket_id": ahead_of}
    if reason is not None:
        body["reason"] = reason
    if note is not None:
        body["note"] = note
    return body


def test_the_line_is_in_call_order_and_a_moved_ticket_has_its_badge_and_trail(
    dashboard: SimpleNamespace,
) -> None:
    """One override: the line shows the new order, the ticket its reason, the card its trail."""
    line = _line(dashboard, dashboard.triage, 5)
    desk = dashboard.client("desk.a")
    before = _board(desk, dashboard)
    assert _numbers(before, dashboard.triage) == [
        "T001",
        "T002",
        "T003",
        "T004",
        "T005",
    ]
    assert before.context["can_reorder"] is True
    assert [choice.value for choice in before.context["reasons"]] == [
        reason.value for reason in PriorityReason
    ]

    moved = desk.post(
        _priority(dashboard, line[4]),
        json=_move(line[4], line[1], PriorityReason.ELDERLY.value, "Using a walker"),
    )
    assert moved.status_code == status.HTTP_200_OK, moved.text

    after = _board(desk, dashboard)
    card = after.context["lines"][dashboard.triage]
    assert _numbers(after, dashboard.triage) == ["T001", "T005", "T002", "T003", "T004"]
    marks = {ticket.number: ticket.priority for ticket in card.waiting}
    assert marks["T005"] is not None
    assert marks["T005"].reason_label == PRIORITY_REASON_LABELS[PriorityReason.ELDERLY]
    assert marks["T005"].staff == "desk.a@clinicq.example"
    assert [number for number, mark in marks.items() if mark] == ["T005"]
    (entry,) = card.trail
    assert (entry.ticket_number, entry.position_before, entry.position_after) == (
        "T005",
        5,
        2,
    )
    assert entry.note == "Using a walker"
    # The other queue's card has no trail of this move.
    assert after.context["lines"][dashboard.pharmacy].trail == ()


def test_dragging_and_tapping_send_one_request_that_lands_identically(
    dashboard: SimpleNamespace,
) -> None:
    """The same move (5th before 2nd, same reason) in two identical queues: same order, same record.

    In the page both gestures call one function that builds this body: a drag names the ticket it
    was dropped on, and a tap names the ticket picked in the prompt. So "the same move" here is the
    same request, sent once per queue.
    """
    triage = _line(dashboard, dashboard.triage, 5)
    pharmacy = _line(dashboard, dashboard.pharmacy, 5)
    desk = dashboard.client("desk.a")
    dragged = desk.post(
        _priority(dashboard, triage[4]),
        json=_move(triage[4], triage[1], PriorityReason.INFANT.value),
    )
    tapped = desk.post(
        _priority(dashboard, pharmacy[4]),
        json=_move(pharmacy[4], pharmacy[1], PriorityReason.INFANT.value),
    )
    assert dragged.status_code == tapped.status_code == status.HTTP_200_OK

    board = _board(desk, dashboard)
    strip = lambda numbers: [number[1:] for number in numbers]  # noqa: E731 - drop the prefix
    assert strip(_numbers(board, dashboard.triage)) == strip(
        _numbers(board, dashboard.pharmacy)
    )
    shape = lambda body: (  # noqa: E731 - the parts of a move that must agree
        body["reorder"]["reason"],
        body["reorder"]["position_before"],
        body["reorder"]["position_after"],
        body["waiting_ahead"],
    )
    assert shape(dragged.json()) == shape(tapped.json()) == ("infant", 5, 2, 1)


def test_a_move_without_a_reason_does_not_save_and_the_line_is_unchanged(
    dashboard: SimpleNamespace,
) -> None:
    """The prompt sends an unchosen reason as absent; the server refuses it and nothing moves."""
    line = _line(dashboard, dashboard.triage, 4)
    desk = dashboard.client("desk.a")
    refused = desk.post(
        _priority(dashboard, line[3]), json=_move(line[3], line[0], reason=None)
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    # The page names the missing reason from this answer, so the field must be the one named.
    assert any("reason" in item["loc"] for item in refused.json()["detail"])

    board = _board(desk, dashboard)
    assert _numbers(board, dashboard.triage) == ["T001", "T002", "T003", "T004"]
    assert board.context["lines"][dashboard.triage].trail == ()
    with dashboard.session() as db:
        assert db.scalar(select(func.count(QueueReorder.id))) == 0


def test_reordering_is_disabled_not_hidden_for_a_role_without_the_permission(
    dashboard: SimpleNamespace,
) -> None:
    """The trainee reads the line with its controls switched off, and the API refuses the move.

    Reading follows the grant too: ``queues.tickets:read`` reaches ``queues.tickets.priority`` by
    inheritance, so the trainee reads the badge and the trail exactly as the trail API would let
    them. Making a move needs ``update``, which they do not hold.
    """
    line = _line(dashboard, dashboard.triage, 3)
    dashboard.client("desk.a").post(
        _priority(dashboard, line[2]),
        json=_move(line[2], line[0], PriorityReason.PREGNANCY.value),
    )
    trainee = dashboard.client("trainee")
    board = _board(trainee, dashboard)
    card = board.context["lines"][dashboard.triage]

    assert board.context["can_reorder"] is False
    # The line is there to be seen (and its controls rendered disabled), not left out.
    assert [ticket.number for ticket in card.waiting] == ["T003", "T001", "T002"]
    assert board.context["can_read_priority"] is True
    assert (
        trainee.get(f"/api/v1/sites/{dashboard.site_a}/tickets/reorders").status_code
        == status.HTTP_200_OK
    )
    assert [entry.ticket_number for entry in card.trail] == ["T003"]
    refused = trainee.post(
        _priority(dashboard, line[2]),
        json=_move(line[2], line[1], PriorityReason.ELDERLY.value),
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    with dashboard.session() as db:
        assert db.scalar(select(func.count(QueueReorder.id))) == 1

    # Deny the trainee's role the override record itself: the trail API refuses them, and the board
    # stops reading overrides for them at all, so there is no badge or trail left to hide.
    with dashboard.session() as db:
        db.add(
            RolePermission(
                role=TRAINEE_ROLE,
                resource=PRIORITY_RESOURCE,
                max_verb=PermissionVerb.READ.value,
                effect=PermissionEffect.DENY.value,
                scope=GrantScope.ASSIGNED.value,
                created_at=datetime.now(UTC),
            )
        )
        db.commit()
    denied = _board(trainee, dashboard)
    card = denied.context["lines"][dashboard.triage]
    assert denied.context["can_read_priority"] is False
    assert all(ticket.priority is None for ticket in card.waiting)
    assert card.trail == ()
    assert (
        trainee.get(f"/api/v1/sites/{dashboard.site_a}/tickets/reorders").status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_the_manager_sees_the_days_overrides_and_counts_by_name_with_filters(
    dashboard: SimpleNamespace,
) -> None:
    """Two people's overrides: every row, counts listed by name, and filters that narrow the list."""
    line = _line(dashboard, dashboard.triage, 6)
    manager = dashboard.client("manager.a")
    dashboard.client("desk.a").post(
        _priority(dashboard, line[5]),
        json=_move(line[5], line[0], PriorityReason.VISIBLY_UNWELL.value),
    )
    for ticket, reason in (
        (line[4], PriorityReason.INFANT),
        (line[3], PriorityReason.ELDERLY),
    ):
        manager.post(
            _priority(dashboard, ticket), json=_move(ticket, line[0], reason.value)
        )

    page = dashboard.page(dashboard.site_a, "overrides")
    everything = manager.get(page)
    assert everything.status_code == status.HTTP_200_OK
    view = everything.context["overrides"]
    assert [entry.reason for entry in everything.context["entries"]] == [
        PriorityReason.ELDERLY,
        PriorityReason.INFANT,
        PriorityReason.VISIBLY_UNWELL,
    ]
    # By name: desk.a (1) before manager.a (2), although manager.a made more.
    assert [(count.staff, count.overrides) for count in view.counts] == [
        ("desk.a@clinicq.example", 1),
        ("manager.a@clinicq.example", 2),
    ]
    assert view.counts_note == COUNTS_ARE_NOT_RANKINGS

    by_reason = manager.get(page, params={"reason": PriorityReason.INFANT.value})
    assert [entry.ticket_number for entry in by_reason.context["entries"]] == ["T005"]
    by_staff = manager.get(page, params={"staff": "desk.a@clinicq.example"})
    assert [entry.ticket_number for entry in by_staff.context["entries"]] == ["T006"]
    # The counts describe the day, whatever the list is filtered to.
    assert len(by_staff.context["overrides"].counts) == 2
    # A day in the future is today: there is no list of overrides that have not happened.
    tomorrow = manager.get(page, params={"day": "2999-01-01"})
    assert tomorrow.context["overrides"].day == everything.context["overrides"].day
    assert manager.get(page, params={"reason": "looks poorly"}).status_code == (
        status.HTTP_422_UNPROCESSABLE_CONTENT
    )

    for name in ("desk.a", "nurse.a", "trainee"):
        assert dashboard.client(name).get(page).status_code == status.HTTP_403_FORBIDDEN


def test_priority_never_reaches_a_patients_ticket_or_a_board_template(
    dashboard: SimpleNamespace,
) -> None:
    """A patient moved forward reads their ticket with no trace of why; board templates carry none."""
    patient, patient_id = dashboard.patient()
    joined = patient.post(
        f"/api/v1/clinics/{dashboard.site_a}/queues/{dashboard.triage}/tickets", json={}
    )
    assert joined.status_code == status.HTTP_201_CREATED, joined.text
    first = _line(dashboard, dashboard.triage, 1)[0]
    mine = joined.json()["ticket"]["id"]
    with dashboard.session() as db:
        assert db.get(Ticket, mine).patient_id == patient_id
    moved = dashboard.client("desk.a").post(
        _priority(dashboard, first),
        json=_move(first, mine, PriorityReason.STAFF_REFERRAL.value, "Doctor asked"),
    )
    assert moved.status_code == status.HTTP_200_OK, moved.text

    own = patient.get("/api/v1/patients/me/tickets").text.lower()
    for word in ("priority", "reorder", "reason", "staff_referral", "doctor asked"):
        assert word not in own

    # Every template on the waiting-room screen's layout: none may import the reorder partial or
    # name priority, so a board built later cannot pick a badge up by including the wrong file.
    extends_board = re.compile(r'{%-?\s*extends\s+"layouts/board.html"')
    board_templates = [TEMPLATES / "layouts" / "board.html"] + [
        path
        for path in TEMPLATES.rglob("*.html")
        if extends_board.search(path.read_text(encoding="utf-8"))
    ]
    assert board_templates
    for path in board_templates:
        text = path.read_text(encoding="utf-8").lower()
        assert "_reorder" not in text and "priority" not in text, path
