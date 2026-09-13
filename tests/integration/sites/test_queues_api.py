"""A clinic runs several named queues, and the server decides who may join which (Issue 25).

Every acceptance criterion over real HTTP, plus the two that outlive this issue:

* **deactivation hides a queue from new joins and keeps its history** — proven by reading
  yesterday's tickets for a deactivated queue through the agreed ticket fixture, since the
  ``tickets`` table itself arrives with Issue 39;
* **a walk-in-only queue is enforced on the server** — the join gate refuses a web, USSD or
  WhatsApp source and admits a walk-in, and it is the *server's* answer that the channel menu
  renders.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from starlette import status

from src.commons.enums import (
    AuditAction,
    AuditEntityType,
    QueueKind,
    TicketSource,
)
from src.commons.time import now_sast
from src.database.models import AuditEvent, Queue, Ticket
from src.modules.queues import service
from src.modules.queues.service import (
    DEFAULT_QUEUES,
    REMOTE_SOURCES,
    QueueNotJoinableError,
    RemoteJoinNotAllowedError,
    create_default_queues,
    ensure_remote_join_allowed,
)
from tests.factories import QueueFactory, TicketFactory

_TRIAGE = {
    "name": "Triage",
    "slug": "triage",
    "kind": QueueKind.TRIAGE.value,
    "room_label": "Front desk",
    "ticket_prefix": "T",
    "display_order": 0,
    "expected_service_minutes": 5,
}


def _manager(clinics: SimpleNamespace):
    """The clinic manager at Clinic A."""
    return clinics.client("manager.a@clinicq.example")


def _add(clinics: SimpleNamespace, **overrides: object):
    """Create one queue at Clinic A through the API."""
    return _manager(clinics).post(
        f"/api/v1/sites/{clinics.site_a}/queues", json={**_TRIAGE, **overrides}
    )


# --- many queues per clinic ------------------------------------------------------------------


def test_a_clinic_carries_any_number_of_queues_in_the_order_it_chose(
    clinics: SimpleNamespace,
) -> None:
    """A real visit is triage, a room, the pharmacy: three lines, in the clinic's order."""
    assert _add(clinics).status_code == status.HTTP_201_CREATED
    _add(
        clinics,
        name="Doctor Room 1",
        slug="room-1",
        kind="consultation",
        display_order=1,
    )
    _add(clinics, name="Pharmacy", slug="pharmacy", kind="pharmacy", display_order=2)

    listed = _manager(clinics).get(f"/api/v1/sites/{clinics.site_a}/queues").json()

    assert listed["total"] == 3
    assert [item["name"] for item in listed["items"]] == [
        "Triage",
        "Doctor Room 1",
        "Pharmacy",
    ]


def test_queue_names_are_unique_per_site_and_not_across_the_platform(
    clinics: SimpleNamespace,
) -> None:
    """Every clinic has a Triage. A second one at the *same* clinic is a 409."""
    assert _add(clinics).status_code == status.HTTP_201_CREATED
    clash = _add(clinics, slug="triage-2")
    assert clash.status_code == status.HTTP_409_CONFLICT

    at_clinic_b = clinics.client("manager.b@clinicq.example").post(
        f"/api/v1/sites/{clinics.site_b}/queues", json=_TRIAGE
    )
    assert at_clinic_b.status_code == status.HTTP_201_CREATED


def test_reordering_is_one_decision_over_the_whole_list(
    clinics: SimpleNamespace,
) -> None:
    """The ids in the order they should appear, so two queues cannot end up sharing a position."""
    manager = _manager(clinics)
    _add(clinics)
    _add(clinics, name="Pharmacy", slug="pharmacy", kind="pharmacy", display_order=1)
    ids = [
        item["id"]
        for item in manager.get(f"/api/v1/sites/{clinics.site_a}/queues").json()[
            "items"
        ]
    ]

    reordered = manager.put(
        f"/api/v1/sites/{clinics.site_a}/queues",
        json={"queue_ids": list(reversed(ids))},
    )

    assert reordered.status_code == status.HTTP_200_OK
    assert [item["id"] for item in reordered.json()["items"]] == list(reversed(ids))


def test_expected_minutes_are_validated_to_a_sensible_range(
    clinics: SimpleNamespace,
) -> None:
    """Zero would make the estimator divide by nothing; six hours is hours in a minutes field."""
    assert _add(clinics, expected_service_minutes=0).status_code == 422
    assert _add(clinics, expected_service_minutes=600).status_code == 422
    assert _add(clinics, expected_service_minutes=45).status_code == 201


def test_only_a_manager_configures_queues_and_the_front_desk_reads_them(
    clinics: SimpleNamespace,
) -> None:
    """A receptionist works the queues; they do not decide what the queues are."""
    _add(clinics)
    desk = clinics.client("desk.a@clinicq.example")

    assert desk.get(f"/api/v1/sites/{clinics.site_a}/queues").status_code == 200
    assert (
        desk.post(f"/api/v1/sites/{clinics.site_a}/queues", json=_TRIAGE).status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_another_clinics_queues_are_not_found(clinics: SimpleNamespace) -> None:
    """Non-negotiable 3, for the queue routes as for every other site-scoped one."""
    manager = _manager(clinics)
    assert (
        manager.get(f"/api/v1/sites/{clinics.site_b}/queues").status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert (
        manager.post(f"/api/v1/sites/{clinics.site_b}/queues", json=_TRIAGE).status_code
        == status.HTTP_404_NOT_FOUND
    )


# --- deactivation keeps history ---------------------------------------------------------------


def test_deactivating_a_queue_hides_it_from_joins_and_keeps_its_history(
    clinics: SimpleNamespace,
) -> None:
    """The criterion, both halves.

    Yesterday's tickets are real rows since Issue 39: they are issued the day before, the queue is
    deactivated, and every one of them still resolves to the queue it was issued in.
    """
    manager = _manager(clinics)
    queue_id = _add(clinics).json()["id"]

    with clinics.session() as db:
        queue = db.get(Queue, queue_id)
        assert queue is not None
        yesterday = now_sast() - timedelta(days=1)
        yesterdays = [
            TicketFactory.create(db, queue=queue, moment=yesterday).id for _ in range(3)
        ]
        db.commit()

    deactivated = manager.delete(f"/api/v1/sites/{clinics.site_a}/queues/{queue_id}")

    assert deactivated.status_code == status.HTTP_200_OK
    assert deactivated.json()["is_active"] is False
    # Gone from what a join surface offers...
    joinable = manager.get(
        f"/api/v1/sites/{clinics.site_a}/queues?include_inactive=false"
    ).json()
    assert joinable["items"] == []
    # ...and still there for the manager, and for anything that has to resolve a past ticket.
    assert (
        manager.get(f"/api/v1/sites/{clinics.site_a}/queues/{queue_id}").status_code
        == 200
    )
    with clinics.session() as db:
        kept = db.get(Queue, queue_id)
        assert kept is not None and kept.is_deleted is False
        history = db.execute(
            select(Ticket.queue_id, Queue.slug)
            .join(Queue, Queue.id == Ticket.queue_id)
            .where(Ticket.id.in_(yesterdays))
        ).all()
        assert sorted(history) == [(queue_id, "triage")] * 3


def test_a_deactivated_queue_refuses_every_source(clinics: SimpleNamespace) -> None:
    """Not only remote ones: a closed line is closed to the front desk as well."""
    with clinics.session() as db:
        queue = QueueFactory.create(db, site_id=clinics.site_a, is_active=False)
        db.commit()
        for source in TicketSource:
            with pytest.raises(QueueNotJoinableError):
                ensure_remote_join_allowed(queue, source)


# --- walk-in only, enforced on the server -------------------------------------------------------


@pytest.mark.parametrize("source", sorted(REMOTE_SOURCES, key=lambda s: s.value))
def test_a_walk_in_only_queue_refuses_a_remote_join_on_the_server(
    clinics: SimpleNamespace, source: TicketSource
) -> None:
    """The criterion: refused by the server, not by a client declining to show a button.

    A USSD session has no buttons to hide, which is exactly why this cannot live in a template.
    """
    with clinics.session() as db:
        queue = QueueFactory.create(
            db, site_id=clinics.site_a, allows_remote_join=False
        )
        db.commit()

        with pytest.raises(RemoteJoinNotAllowedError) as refusal:
            ensure_remote_join_allowed(queue, source)
        assert "front desk" in str(refusal.value)
        # And the one source that is in the building is admitted.
        ensure_remote_join_allowed(queue, TicketSource.WALK_IN)


def test_the_channel_menu_carries_the_servers_reason_rather_than_dropping_the_queue(
    clinics: SimpleNamespace,
) -> None:
    """A patient on USSD is told the pharmacy is walk-in only, not left to wonder if there is one."""
    manager = _manager(clinics)
    _add(clinics)
    _add(
        clinics,
        name="Pharmacy",
        slug="pharmacy",
        kind="pharmacy",
        display_order=1,
        allows_remote_join=False,
    )

    by_ussd = manager.get(
        f"/api/v1/sites/{clinics.site_a}/queues/joinable?source=ussd"
    ).json()
    at_the_desk = manager.get(
        f"/api/v1/sites/{clinics.site_a}/queues/joinable?source=walk_in"
    ).json()

    assert by_ussd["source"] == "ussd"
    pharmacy = next(item for item in by_ussd["items"] if item["slug"] == "pharmacy")
    assert pharmacy["joinable"] is False
    assert "front desk" in pharmacy["refusal"]
    assert all(item["joinable"] for item in at_the_desk["items"])


# --- onboarding's default set --------------------------------------------------------------


def test_a_newly_onboarded_clinic_starts_with_a_sensible_queue_set(
    clinics: SimpleNamespace,
) -> None:
    """Triage, a consulting room and the pharmacy: a real visit, in order, not an empty board."""
    with clinics.session() as db:
        slugs = [queue.slug for queue in create_default_queues(db, clinics.site_b)]
        db.commit()

    assert slugs == [row[0] for row in DEFAULT_QUEUES]
    listed = (
        clinics.client("manager.b@clinicq.example")
        .get(f"/api/v1/sites/{clinics.site_b}/queues")
        .json()
    )
    assert [item["kind"] for item in listed["items"]] == [
        QueueKind.TRIAGE.value,
        QueueKind.CONSULTATION.value,
        QueueKind.PHARMACY.value,
    ]
    # And the pharmacy is walk-in only out of the box: you cannot collect medicine from a phone.
    pharmacy = next(item for item in listed["items"] if item["kind"] == "pharmacy")
    assert pharmacy["allows_remote_join"] is False


def test_the_default_set_is_not_created_twice(clinics: SimpleNamespace) -> None:
    """Re-running onboarding must not duplicate a line, or resurrect one the clinic deleted."""
    with clinics.session() as db:
        create_default_queues(db, clinics.site_b)
        db.commit()
        again = create_default_queues(db, clinics.site_b)
        db.commit()
        assert again == []
        assert len(
            db.execute(select(Queue).where(Queue.site_id == clinics.site_b)).all()
        ) == len(DEFAULT_QUEUES)


# --- the trail ------------------------------------------------------------------------------


def test_every_queue_change_is_audited_with_the_manager_and_the_clinic(
    clinics: SimpleNamespace,
) -> None:
    """Adding, editing and deactivating a queue each leave a row naming who and where."""
    manager = _manager(clinics)
    queue_id = _add(clinics).json()["id"]
    manager.put(
        f"/api/v1/sites/{clinics.site_a}/queues/{queue_id}",
        json={**_TRIAGE, "name": "Triage desk"},
    )
    manager.delete(f"/api/v1/sites/{clinics.site_a}/queues/{queue_id}")

    with clinics.session() as db:
        rows = (
            db.execute(
                select(AuditEvent)
                .where(AuditEvent.entity_type == AuditEntityType.QUEUE.value)
                .order_by(AuditEvent.created_at)
            )
            .scalars()
            .all()
        )
    assert [row.action for row in rows] == [
        AuditAction.CREATE.value,
        AuditAction.UPDATE.value,
        AuditAction.UPDATE.value,
    ]
    assert {row.actor for row in rows} == {"manager.a@clinicq.example"}
    assert {row.site_id for row in rows} == {clinics.site_a}
    assert "its tickets are kept" in (rows[-1].context or "")


def test_the_module_metadata_endpoint_needs_no_session(
    clinics: SimpleNamespace,
) -> None:
    """``/info`` is public, like every other module's."""
    from fastapi.testclient import TestClient

    with TestClient(clinics.app) as anonymous:
        info = anonymous.get("/api/v1/sites/queues/info")
    assert info.status_code == status.HTTP_200_OK
    assert info.json()["context"] == "queues"
    assert service.get_module_info().context.value == "queues"
