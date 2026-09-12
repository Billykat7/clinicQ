"""A clinic lists what it offers, and how long each thing takes (Issue 26).

Every acceptance criterion over HTTP, and the two that outlive this issue:

* **expected minutes are the wait estimator's prior**, so they are validated to a range an estimate
  can be built on rather than left free;
* **deactivating a service removes it from new joins and never from history**, the same rule
  queues hold and for the same reason.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from starlette import status

from src.commons.enums import AuditAction, AuditEntityType, ServiceCategory
from src.database.models import AuditEvent, ClinicService
from src.modules.sites.catalogue import (
    DEFAULT_CATALOGUE,
    expected_minutes_prior,
    seed_default_catalogue,
)

_CONSULTATION = {
    "name": "General consultation",
    "slug": "consultation",
    "category": ServiceCategory.CONSULTATION.value,
    "description": "Seeing a nurse or a doctor about something new.",
    "expected_minutes": 15,
    "display_order": 0,
}


def _manager(clinics: SimpleNamespace):
    """The clinic manager at Clinic A."""
    return clinics.client("manager.a@clinicq.example")


def _add(clinics: SimpleNamespace, **overrides: object):
    """Add one service to Clinic A through the API."""
    return _manager(clinics).post(
        f"/api/v1/sites/{clinics.site_a}/services",
        json={**_CONSULTATION, **overrides},
    )


def test_a_clinic_lists_what_it_offers_in_its_own_order(
    clinics: SimpleNamespace,
) -> None:
    """The catalogue, in the order the clinic put it in rather than the alphabet's."""
    assert _add(clinics).status_code == status.HTTP_201_CREATED
    _add(
        clinics,
        name="Antenatal care",
        slug="antenatal",
        category="maternal",
        display_order=1,
        expected_minutes=25,
    )

    listed = _manager(clinics).get(f"/api/v1/sites/{clinics.site_a}/services").json()

    assert listed["total"] == 2
    assert [item["name"] for item in listed["items"]] == [
        "General consultation",
        "Antenatal care",
    ]
    assert listed["items"][1]["category"] == ServiceCategory.MATERNAL.value


def test_expected_minutes_are_validated_to_a_sensible_range(
    clinics: SimpleNamespace,
) -> None:
    """The criterion: zero and 600 are refused, because this number seeds a wait estimate."""
    assert _add(clinics, expected_minutes=0).status_code == 422
    assert _add(clinics, expected_minutes=600).status_code == 422
    assert _add(clinics, expected_minutes=240).status_code == 201  # the ceiling itself


def test_a_service_can_name_the_queues_that_handle_it(
    clinics: SimpleNamespace,
) -> None:
    """The optional link: a clinic may say which line a service happens in, or say nothing."""
    manager = _manager(clinics)
    queue_id = manager.post(
        f"/api/v1/sites/{clinics.site_a}/queues",
        json={"name": "Doctor Room 1", "slug": "room-1", "kind": "consultation"},
    ).json()["id"]

    created = _add(clinics, queue_ids=[queue_id])

    assert created.status_code == status.HTTP_201_CREATED
    assert created.json()["queue_ids"] == [queue_id]


def test_another_clinics_queue_id_is_dropped_rather_than_linked(
    clinics: SimpleNamespace,
) -> None:
    """A stale id in a browser's list must not turn into a cross-tenant link."""
    at_b = (
        clinics.client("manager.b@clinicq.example")
        .post(
            f"/api/v1/sites/{clinics.site_b}/queues",
            json={"name": "Their room", "slug": "their-room"},
        )
        .json()["id"]
    )

    created = _add(clinics, queue_ids=[at_b])

    assert created.status_code == status.HTTP_201_CREATED
    assert created.json()["queue_ids"] == []


def test_deactivating_a_service_removes_it_from_new_joins_but_not_from_history(
    clinics: SimpleNamespace,
) -> None:
    """The criterion, both halves: gone from what a patient may choose, still resolvable."""
    manager = _manager(clinics)
    service_id = _add(clinics).json()["id"]

    deactivated = manager.delete(
        f"/api/v1/sites/{clinics.site_a}/services/{service_id}"
    )

    assert deactivated.status_code == status.HTTP_200_OK
    assert deactivated.json()["is_active"] is False
    joinable = manager.get(
        f"/api/v1/sites/{clinics.site_a}/services?include_inactive=false"
    ).json()
    assert joinable["items"] == []
    # Still in the manager's list, and still resolvable by a past ticket's service id.
    assert manager.get(f"/api/v1/sites/{clinics.site_a}/services").json()["total"] == 1
    with clinics.session() as db:
        row = db.get(ClinicService, service_id)
        assert row is not None and row.is_deleted is False


def test_a_duplicate_name_at_the_same_clinic_is_a_conflict(
    clinics: SimpleNamespace,
) -> None:
    """Unique per site, like a queue's name: another clinic may offer the same thing."""
    assert _add(clinics).status_code == status.HTTP_201_CREATED
    assert _add(clinics, slug="consultation-2").status_code == status.HTTP_409_CONFLICT
    assert (
        clinics.client("manager.b@clinicq.example")
        .post(f"/api/v1/sites/{clinics.site_b}/services", json=_CONSULTATION)
        .status_code
        == status.HTTP_201_CREATED
    )


def test_the_front_desk_reads_the_catalogue_and_the_manager_decides_it(
    clinics: SimpleNamespace,
) -> None:
    """It is what a walk-in is asked which of, so a receptionist has to be able to see it."""
    _add(clinics)
    desk = clinics.client("desk.a@clinicq.example")

    assert desk.get(f"/api/v1/sites/{clinics.site_a}/services").status_code == 200
    assert (
        desk.post(
            f"/api/v1/sites/{clinics.site_a}/services", json=_CONSULTATION
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_another_clinics_catalogue_is_not_found(clinics: SimpleNamespace) -> None:
    """Non-negotiable 3, for the catalogue as for everything else site-scoped."""
    manager = _manager(clinics)
    assert (
        manager.get(f"/api/v1/sites/{clinics.site_b}/services").status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert (
        manager.post(
            f"/api/v1/sites/{clinics.site_b}/services", json=_CONSULTATION
        ).status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_a_newly_onboarded_clinic_gets_a_catalogue_it_can_edit(
    clinics: SimpleNamespace,
) -> None:
    """The criterion: not an empty list, and every entry editable afterwards."""
    with clinics.session() as db:
        slugs = [row.slug for row in seed_default_catalogue(db, clinics.site_b)]
        db.commit()

    assert slugs == [entry.slug for entry in DEFAULT_CATALOGUE]
    manager_b = clinics.client("manager.b@clinicq.example")
    listed = manager_b.get(f"/api/v1/sites/{clinics.site_b}/services").json()
    assert listed["total"] == len(DEFAULT_CATALOGUE)
    assert {item["category"] for item in listed["items"]} >= {
        ServiceCategory.CONSULTATION.value,
        ServiceCategory.CHRONIC.value,
        ServiceCategory.CHILD_HEALTH.value,
        ServiceCategory.MATERNAL.value,
        ServiceCategory.HIV_TB.value,
    }

    first = listed["items"][0]
    edited = manager_b.put(
        f"/api/v1/sites/{clinics.site_b}/services/{first['id']}",
        json={
            **_CONSULTATION,
            "name": "Doctor consultation",
            "slug": first["slug"],
            "expected_minutes": 18,
        },
    )
    assert edited.status_code == status.HTTP_200_OK
    assert edited.json()["expected_minutes"] == 18


def test_the_default_catalogue_is_not_seeded_twice(clinics: SimpleNamespace) -> None:
    """Re-running onboarding must not duplicate an entry or resurrect a removed one."""
    with clinics.session() as db:
        seed_default_catalogue(db, clinics.site_b)
        db.commit()
        assert seed_default_catalogue(db, clinics.site_b) == []
        db.commit()
        rows = (
            db.execute(
                select(ClinicService).where(ClinicService.site_id == clinics.site_b)
            )
            .scalars()
            .all()
        )
    assert len(rows) == len(DEFAULT_CATALOGUE)


def test_the_estimator_reads_a_services_expected_minutes_and_falls_back_to_the_queues(
    clinics: SimpleNamespace,
) -> None:
    """What Issue 42 calls on a clinic's first morning, before there is any history to read."""
    from src.core.site_scope import SiteAccess
    from src.database.models import User

    service_id = _add(clinics, expected_minutes=22).json()["id"]
    with clinics.session() as db:
        staff = db.execute(
            select(User).where(User.email == "manager.a@clinicq.example")
        ).scalar_one()
        access = SiteAccess(site_id=clinics.site_a, user=staff)

        assert expected_minutes_prior(db, access, service_id, fallback=10) == 22
        # A ticket that names no service, or one from a clinic that has since removed it, falls
        # back to the queue's own pace rather than to nothing.
        assert expected_minutes_prior(db, access, "no-such-service", fallback=10) == 10


def test_every_catalogue_change_is_audited(clinics: SimpleNamespace) -> None:
    """Adding, editing and deactivating a service each leave a row naming who and where."""
    manager = _manager(clinics)
    service_id = _add(clinics).json()["id"]
    manager.put(
        f"/api/v1/sites/{clinics.site_a}/services/{service_id}",
        json={**_CONSULTATION, "expected_minutes": 20},
    )
    manager.delete(f"/api/v1/sites/{clinics.site_a}/services/{service_id}")

    with clinics.session() as db:
        rows = (
            db.execute(
                select(AuditEvent)
                .where(AuditEvent.entity_type == AuditEntityType.CLINIC_SERVICE.value)
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
    assert {row.site_id for row in rows} == {clinics.site_a}
    assert "past tickets still name it" in (rows[-1].context or "")
