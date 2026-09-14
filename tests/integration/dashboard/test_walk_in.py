"""Walk-in intake at the front desk, the recent list, undo and the ticket stub, over HTTP (Issue 51).

Each acceptance criterion the server can prove, without reading HTML except to search a patient-facing
page for what must never be on it (``docs/IDE/RULES/testing-strategy.mdc``):

* **One sequence.** A walk-in takes the next number after a phone join, and the next phone join the one
  after it: there is no walk-in path, only Issue 40's join with ``source=walk_in``.
* **One press, one ticket.** A repeated ``Idempotency-Key`` answers with the ticket already issued.
* **A phone number enables later notifications.** The walk-in is that patient's ticket, and the answer to
  being messaged, asked at the desk, is recorded as their ``notifications`` consent. Nothing is sent.
* **Undo, audited.** Only the last walk-in this person issued, only while it waits and within the window;
  it is cancelled through the lifecycle with ``undo walk-in`` on its audit row, and its number is never
  reused.
* **The page and the stub** are gated like the desk, and the stub never carries a name, phone or reason.

How fast a walk-in is by keyboard alone is measured in a browser and stated in the pull request.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import func, select, update
from starlette import status

from src.commons.enums import (
    ActorKind,
    AuditEntityType,
    CancellationReason,
    ConsentPurpose,
    PatientChannel,
    TicketSource,
    TicketStatus,
)
from src.commons.time import now_sast
from src.database.models import (
    AuditEvent,
    Notification,
    Patient,
    PatientConsentEvent,
    Queue,
    Ticket,
)
from src.modules.patients.consent import has_consent
from src.modules.queue.lifecycle import STALE_TRANSITION_CODE, Actor, call_next
from src.modules.queue.request_keys import REQUEST_KEY_REUSED_CODE
from src.modules.queue.walk_ins import (
    WALK_IN_UNDO_NOT_LATEST_CODE,
    WALK_IN_UNDO_WINDOW_CLOSED_CODE,
)

#: A name no page could contain by accident, so finding it on the stub is a leak.
SECRET_NAME = "Gogo ZEBRA-51"


def _walk_in_url(dashboard: SimpleNamespace, queue_id: str) -> str:
    return f"/api/v1/sites/{dashboard.site_a}/queues/{queue_id}/tickets"


def _undo_url(dashboard: SimpleNamespace, ticket_id: str) -> str:
    return f"/api/v1/sites/{dashboard.site_a}/tickets/{ticket_id}/undo-walk-in"


def _issue(dashboard: SimpleNamespace, who: str = "desk.a", **body: object) -> dict:
    response = dashboard.client(who).post(
        _walk_in_url(dashboard, dashboard.triage), json={"name": "Walk-in", **body}
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["ticket"]


def test_a_walk_in_takes_the_next_number_in_the_sequence_phone_joins_use(
    dashboard: SimpleNamespace,
) -> None:
    """A phone join, a walk-in, a phone join: T001, T002, T003 in one queue."""
    first, _ = dashboard.patient()
    second, _ = dashboard.patient()
    join = f"/api/v1/clinics/{dashboard.site_a}/queues/{dashboard.triage}/tickets"

    phone_1 = first.post(join, json={}).json()["ticket"]
    walk_in = _issue(dashboard)
    phone_2 = second.post(join, json={}).json()["ticket"]

    assert [phone_1["number"], walk_in["number"], phone_2["number"]] == [
        "T001",
        "T002",
        "T003",
    ]
    assert walk_in["source"] == TicketSource.WALK_IN.value


def test_a_double_enter_issues_one_walk_in(dashboard: SimpleNamespace) -> None:
    """The same key twice: 201 then 200 with the same ticket, and one ticket in the queue."""
    desk = dashboard.client("desk.a")
    url = _walk_in_url(dashboard, dashboard.triage)
    headers = {"Idempotency-Key": "walk-in-enter-01"}

    first = desk.post(url, json={"name": "Thabo"}, headers=headers)
    second = desk.post(url, json={"name": "Thabo"}, headers=headers)

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["ticket"]["id"] == second.json()["ticket"]["id"]
    assert second.json()["created"] is False
    with dashboard.session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Ticket)
                .where(Ticket.queue_id == dashboard.triage)
            )
            == 1
        )
    # The same key for another walk-in (another queue) is a different request, refused.
    reused = desk.post(
        _walk_in_url(dashboard, dashboard.pharmacy),
        json={"name": "Thabo"},
        headers=headers,
    )
    assert reused.status_code == status.HTTP_409_CONFLICT
    assert reused.json()["code"] == REQUEST_KEY_REUSED_CODE


def test_a_phone_number_links_the_patient_and_records_their_answer_to_messages(
    dashboard: SimpleNamespace,
) -> None:
    """With a number and a yes, the patient holds the ticket and the consent; nothing is sent here."""
    ticket = _issue(
        dashboard, name="Lindiwe", phone="082 555 0151", notifications_consent=True
    )
    declined = _issue(dashboard, name="Sipho", phone="082 555 0152")

    with dashboard.session() as db:
        agreed = db.get(Ticket, ticket["id"])
        said_no = db.get(Ticket, declined["id"])
        assert agreed.patient_id and said_no.patient_id
        assert (
            db.get(Patient, agreed.patient_id).last_channel
            == PatientChannel.WALK_IN.value
        )
        assert has_consent(db, agreed.patient_id, ConsentPurpose.NOTIFICATIONS)
        assert not has_consent(db, said_no.patient_id, ConsentPurpose.NOTIFICATIONS)
        (event,) = db.execute(
            select(PatientConsentEvent).where(
                PatientConsentEvent.patient_id == agreed.patient_id
            )
        ).scalars()
        assert (event.source_channel, event.recorded_by, event.site_id) == (
            PatientChannel.WALK_IN.value,
            dashboard.ids["desk.a"],
            dashboard.site_a,
        )
        # Capturing the number is the whole of it: notifications are M9's, and none was created.
        assert db.scalar(select(func.count()).select_from(Notification)) == 0

    no_number = dashboard.client("desk.a").post(
        _walk_in_url(dashboard, dashboard.triage),
        json={"name": "Anon", "notifications_consent": True},
    )
    assert no_number.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_only_the_desks_last_walk_in_can_be_undone_within_the_window_and_it_is_audited(
    dashboard: SimpleNamespace,
) -> None:
    """Earlier or someone else's: refused. The last: cancelled, audited, its number never reused."""
    earlier = _issue(dashboard, name="First")
    last = _issue(dashboard, name="Second")
    desk = dashboard.client("desk.a")

    not_latest = desk.post(_undo_url(dashboard, earlier["id"]))
    someone_else = dashboard.client("both").post(_undo_url(dashboard, last["id"]))
    undone = desk.post(_undo_url(dashboard, last["id"]))

    assert not_latest.status_code == status.HTTP_409_CONFLICT
    assert not_latest.json()["code"] == WALK_IN_UNDO_NOT_LATEST_CODE
    assert someone_else.json()["code"] == WALK_IN_UNDO_NOT_LATEST_CODE
    assert undone.status_code == status.HTTP_200_OK, undone.text
    assert undone.json()["ticket"]["status"] == TicketStatus.CANCELLED.value
    with dashboard.session() as db:
        ticket = db.get(Ticket, last["id"])
        assert (ticket.cancelled_via, ticket.cancellation_reason) == (
            PatientChannel.WALK_IN.value,
            CancellationReason.JOINED_BY_MISTAKE.value,
        )
        contexts = db.execute(
            select(AuditEvent.context).where(
                AuditEvent.entity_type == AuditEntityType.TICKET.value,
                AuditEvent.entity_id == last["id"],
            )
        ).scalars()
        assert (
            "T002: waiting → cancelled (cancelled by staff via walk_in, reason joined_by_mistake, undo walk-in)"
            in list(contexts)
        )
        assert db.get(Ticket, earlier["id"]).status == TicketStatus.WAITING.value

    # Undoing it again is refused: it is no longer waiting. And T002 is never given out again.
    again = desk.post(_undo_url(dashboard, last["id"]))
    assert again.json()["code"] == STALE_TRANSITION_CODE
    assert _issue(dashboard, name="Third")["number"] == "T003"


def test_an_undo_after_the_window_or_once_called_is_refused_and_changes_nothing(
    dashboard: SimpleNamespace,
) -> None:
    """Two minutes and a second old: too late. Already called: not waiting any more."""
    old = _issue(dashboard, name="Late")
    with dashboard.session() as db:
        db.execute(
            update(Ticket)
            .where(Ticket.id == old["id"])
            .values(
                joined_at=now_sast()
                - timedelta(seconds=dashboard.settings.queue_walk_in_undo_seconds + 1)
            )
        )
        db.commit()
    desk = dashboard.client("desk.a")
    too_late = desk.post(_undo_url(dashboard, old["id"]))
    assert too_late.status_code == status.HTTP_409_CONFLICT
    assert too_late.json()["code"] == WALK_IN_UNDO_WINDOW_CLOSED_CODE

    called = _issue(dashboard, name="Quick")
    with dashboard.session() as db:
        call_next(
            db,
            db.get(Queue, dashboard.triage),
            actor=Actor(kind=ActorKind.STAFF, label="nurse"),
        )
        call_next(
            db,
            db.get(Queue, dashboard.triage),
            actor=Actor(kind=ActorKind.STAFF, label="nurse"),
        )
        db.commit()
    refused = desk.post(_undo_url(dashboard, called["id"]))
    assert refused.json()["code"] == STALE_TRANSITION_CODE
    with dashboard.session() as db:
        assert [db.get(Ticket, t["id"]).status for t in (old, called)] == [
            TicketStatus.CALLED.value,
            TicketStatus.CALLED.value,
        ]


def test_the_intake_page_offers_the_open_queues_and_the_callers_own_recent_walk_ins(
    dashboard: SimpleNamespace,
) -> None:
    """Newest first, only the newest undoable; another receptionist's walk-ins are not in the list."""
    first = _issue(dashboard, name="One")
    second = _issue(dashboard, name="Two")
    _issue(dashboard, who="both", name="Someone else's")
    desk = dashboard.client("desk.a")

    page = desk.get(dashboard.page(dashboard.site_a, "walk-in"))

    assert page.status_code == status.HTTP_200_OK
    view = page.context["walk_in"]
    assert [q.id for q in view.queues] == [dashboard.triage, dashboard.pharmacy]
    assert [item.ticket.id for item in view.recent] == [second["id"], first["id"]]
    assert [item.undo_until is not None for item in view.recent] == [True, False]
    assert view.undo_window == "2 minutes"
    # Shown in Johannesburg time, whatever the database handed back.
    assert view.recent[0].issued_at.utcoffset() == timedelta(hours=2)
    fragment = desk.get(dashboard.page(dashboard.site_a, "walk-in/recent"))
    assert fragment.status_code == status.HTTP_200_OK
    assert [item.ticket.id for item in fragment.context["walk_in"].recent] == [
        second["id"],
        first["id"],
    ]

    # The desk's screen: a nurse and the read-only trainee are refused, a stranger is sent to sign in.
    for name in ("nurse.a", "trainee"):
        assert (
            dashboard.client(name)
            .get(dashboard.page(dashboard.site_a, "walk-in"))
            .status_code
            == status.HTTP_403_FORBIDDEN
        ), name
    anonymous = dashboard.anonymous()
    assert anonymous.get(dashboard.page(dashboard.site_a, "walk-in")).status_code == 302
    assert (
        anonymous.get(dashboard.page(dashboard.site_a, "walk-in/recent")).status_code
        == status.HTTP_401_UNAUTHORIZED
    )


def test_the_stub_shows_the_number_and_where_the_patient_stands_and_never_who_they_are(
    dashboard: SimpleNamespace,
) -> None:
    """The number, the queue, the place and the reference; no name, phone or reason anywhere on it."""
    _issue(dashboard, name="Ahead")
    ticket = _issue(
        dashboard, name=SECRET_NAME, phone="082 555 0199", reason_text="ZEBRA cough"
    )
    desk = dashboard.client("desk.a")
    stub_path = dashboard.page(dashboard.site_a, f"walk-in/tickets/{ticket['id']}/stub")

    stub = desk.get(stub_path)

    assert stub.status_code == status.HTTP_200_OK
    shown = stub.context["stub"]
    assert (shown.number, shown.queue, shown.room_label) == ("T002", "Triage", "Room 2")
    assert shown.standing == "1 person ahead of you"
    assert shown.reference_code == ticket["reference_code"]
    assert "ZEBRA" not in stub.text and "555 0199" not in stub.text

    # Another clinic's desk, and a ticket no longer in the day, find nothing.
    assert (
        dashboard.client("desk.b").get(stub_path).status_code
        == status.HTTP_404_NOT_FOUND
    )
    desk.post(_undo_url(dashboard, ticket["id"]))
    assert desk.get(stub_path).status_code == status.HTTP_404_NOT_FOUND
