"""Clinic profile CRUD over real HTTP: who may do what, and what is refused (Issue 23).

Every acceptance criterion that does not need PostGIS:

* a site persists with a real coordinate, and a location outside South Africa is refused with a
  message that says what to check;
* ``sector`` and ``status`` are enums on the wire, and ``status`` is not settable by a client;
* only a clinic manager at *that* clinic, or a platform admin, may edit it: another clinic's id is
  a 404, and a receptionist's edit is a 403;
* a listing shows a caller their own clinics and the operator the platform;
* every mutation writes an audit row naming the actor.

The PostGIS half (``ST_DWithin`` and the GiST index in the query plan) is in
``test_site_postgis.py``, which runs against a real server.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from starlette import status

from src.commons.enums import AuditAction, AuditEntityType, SiteSector, SiteStatus
from src.database.models import AuditEvent, Site

_NOWHERE = "0199b0c0-0000-7000-8000-0000000000ff"

_NEW_CLINIC = {
    "name": "Zola Clinic",
    "slug": "zola-clinic",
    "sector": SiteSector.PUBLIC.value,
    "location": {"latitude": -26.26840, "longitude": 27.84720},
    "address_line": "Zola North, Soweto",
    "suburb": "Zola",
    "city": "Johannesburg",
    "province": "Gauteng",
    "phone_e164": "+27115551234",
}


def _payload(**overrides: object) -> dict[str, object]:
    """The creation body, with fields replaced."""
    return {**_NEW_CLINIC, **overrides}


def test_an_operator_creates_a_clinic_and_it_persists_with_its_coordinate(
    clinics: SimpleNamespace,
) -> None:
    """The happy path: a real coordinate in, the same coordinate out, and a draft on the way in."""
    created = clinics.client("operator@clinicq.example").post(
        "/api/v1/sites", json=_payload()
    )

    assert created.status_code == status.HTTP_201_CREATED, created.text
    body = created.json()
    assert body["location"] == {"latitude": -26.26840, "longitude": 27.84720}
    assert body["sector"] == SiteSector.PUBLIC.value
    assert body["status"] == SiteStatus.DRAFT.value
    with clinics.session() as db:
        row = db.execute(select(Site).where(Site.slug == "zola-clinic")).scalar_one()
        assert (row.location.latitude, row.location.longitude) == (-26.26840, 27.84720)
        # The trail records the clinic it is about, so Issue 20's per-site audit API finds the row
        # that created it. Nothing else binds the site here: the platform routes have no site guard.
        event = db.execute(
            select(AuditEvent).where(AuditEvent.action == AuditAction.CREATE.value)
        ).scalar_one()
        assert event.entity_id == row.id and event.site_id == row.id


def test_a_client_cannot_post_itself_into_the_directory(
    clinics: SimpleNamespace,
) -> None:
    """``status`` is not an input: visibility comes from the verification workflow (Issue 29)."""
    created = clinics.client("operator@clinicq.example").post(
        "/api/v1/sites", json=_payload(status=SiteStatus.VERIFIED.value)
    )
    assert created.status_code == status.HTTP_201_CREATED
    assert created.json()["status"] == SiteStatus.DRAFT.value


def test_a_clinic_at_null_island_is_refused_with_a_clear_message(
    clinics: SimpleNamespace,
) -> None:
    """``0, 0`` is what an unset coordinate looks like, and the refusal says so."""
    refused = clinics.client("operator@clinicq.example").post(
        "/api/v1/sites",
        json=_payload(location={"latitude": 0.0, "longitude": 0.0}),
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "South Africa" in refused.text


def test_a_swapped_latitude_and_longitude_is_refused(
    clinics: SimpleNamespace,
) -> None:
    """The pair the other way round is in the Indian Ocean; the bounding box catches it."""
    refused = clinics.client("operator@clinicq.example").post(
        "/api/v1/sites",
        json=_payload(location={"latitude": 27.84720, "longitude": -26.26840}),
    )
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_a_duplicate_slug_is_a_conflict_not_a_server_error(
    clinics: SimpleNamespace,
) -> None:
    """A clash is answered as 409 before the insert, never as an integrity error from the driver."""
    operator = clinics.client("operator@clinicq.example")
    assert operator.post("/api/v1/sites", json=_payload()).status_code == 201
    again = operator.post("/api/v1/sites", json=_payload(name="Zola Clinic 2"))
    assert again.status_code == status.HTTP_409_CONFLICT
    assert "zola-clinic" in again.json()["detail"]


def test_a_clinic_manager_may_not_create_a_clinic(clinics: SimpleNamespace) -> None:
    """Onboarding is the operator's; a manager runs the clinic they were given."""
    refused = clinics.client("manager.a@clinicq.example").post(
        "/api/v1/sites", json=_payload()
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN


def test_a_manager_edits_their_own_clinic_and_the_change_is_audited(
    clinics: SimpleNamespace,
) -> None:
    """The update lands, and an audit row names who made it and at which clinic."""
    manager = clinics.client("manager.a@clinicq.example")
    current = manager.get(f"/api/v1/sites/{clinics.site_a}").json()

    updated = manager.put(
        f"/api/v1/sites/{clinics.site_a}",
        json={
            "name": "Hillbrow CHC (east entrance)",
            "slug": current["slug"],
            "sector": current["sector"],
            "location": current["location"],
            "address_line": current["address_line"],
            "suburb": current["suburb"],
            "city": current["city"],
            "province": current["province"],
            "notes": "Entrance on the Klein Street side.",
        },
    )

    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert updated.json()["name"] == "Hillbrow CHC (east entrance)"
    with clinics.session() as db:
        rows = (
            db.execute(
                select(AuditEvent).where(
                    AuditEvent.entity_type == AuditEntityType.SITE.value,
                    AuditEvent.action == AuditAction.UPDATE.value,
                )
            )
            .scalars()
            .all()
        )
    assert [row.actor for row in rows] == ["manager.a@clinicq.example"]
    assert rows[0].entity_id == clinics.site_a


def test_a_receptionist_reads_the_profile_and_cannot_change_it(
    clinics: SimpleNamespace,
) -> None:
    """The front desk sees the clinic; ``sites.profile:update`` is the manager's grant."""
    desk = clinics.client("desk.a@clinicq.example")
    assert desk.get(f"/api/v1/sites/{clinics.site_a}").status_code == status.HTTP_200_OK

    current = desk.get(f"/api/v1/sites/{clinics.site_a}").json()
    refused = desk.put(
        f"/api/v1/sites/{clinics.site_a}",
        json={
            "name": "Renamed by the front desk",
            "slug": current["slug"],
            "sector": current["sector"],
            "location": current["location"],
            "address_line": current["address_line"],
            "city": current["city"],
            "province": current["province"],
        },
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN


def test_another_clinics_id_is_a_404_for_a_read_and_for_a_write(
    clinics: SimpleNamespace,
) -> None:
    """Non-negotiable 3: not yours and never existed answer identically."""
    manager = clinics.client("manager.a@clinicq.example")
    at_b = manager.get(f"/api/v1/sites/{clinics.site_b}")
    nowhere = manager.get(f"/api/v1/sites/{_NOWHERE}")

    assert at_b.status_code == nowhere.status_code == status.HTTP_404_NOT_FOUND
    assert at_b.json()["detail"] == nowhere.json()["detail"]
    assert (
        manager.put(f"/api/v1/sites/{clinics.site_b}", json=_payload()).status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_the_directory_is_the_operators_and_a_manager_reads_their_clinic_by_id(
    clinics: SimpleNamespace,
) -> None:
    """Who may list *every* clinic is the grant's tier, not a query parameter a caller could change.

    A clinic manager's role is held at a site, so it does not satisfy a ``business``-tier gate on a
    route that names no site; they read their own clinic by its id instead, and their "which
    clinics do I work at" list is the site switcher's (Issue 28).
    """
    everything = clinics.client("operator@clinicq.example").get("/api/v1/sites")
    assert everything.status_code == status.HTTP_200_OK
    assert {item["id"] for item in everything.json()["items"]} == {
        clinics.site_a,
        clinics.site_b,
    }

    manager = clinics.client("manager.a@clinicq.example")
    assert manager.get("/api/v1/sites").status_code == status.HTTP_403_FORBIDDEN
    assert (
        manager.get(f"/api/v1/sites/{clinics.site_a}").status_code == status.HTTP_200_OK
    )


def test_the_operator_removes_a_clinic_and_it_leaves_every_read(
    clinics: SimpleNamespace,
) -> None:
    """A soft delete: the row stays for the trail, and no read returns it again."""
    operator = clinics.client("operator@clinicq.example")
    removed = operator.delete(f"/api/v1/sites/{clinics.site_b}")

    assert removed.status_code == status.HTTP_204_NO_CONTENT
    assert {item["id"] for item in operator.get("/api/v1/sites").json()["items"]} == {
        clinics.site_a
    }
    with clinics.session() as db:
        row = db.get(Site, clinics.site_b)
        assert row is not None and row.is_deleted and not row.is_active
    assert (
        clinics.client("manager.b@clinicq.example")
        .get(f"/api/v1/sites/{clinics.site_b}")
        .status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_the_geocoding_proxy_is_the_servers_job_and_says_so_when_it_is_off(
    clinics: SimpleNamespace,
) -> None:
    """The browser posts here rather than to a geocoder; unconfigured answers 503, not 500.

    Both doors: the operator's, for a clinic that does not exist yet, and the manager's, which
    names their clinic in the path so their site-held role resolves.
    """
    for client, path in (
        ("operator@clinicq.example", "/api/v1/sites/geocode"),
        ("manager.a@clinicq.example", f"/api/v1/sites/{clinics.site_a}/geocode"),
    ):
        answered = clinics.client(client).post(
            path, json={"address": "Hillbrow CHC, Johannesburg"}
        )
        assert answered.status_code == status.HTTP_503_SERVICE_UNAVAILABLE, path
        assert "GEOCODING_PROVIDER" in answered.json()["detail"]


def test_a_manager_cannot_geocode_for_another_clinic(
    clinics: SimpleNamespace,
) -> None:
    """The per-clinic proxy is behind the site guard, so Clinic B's id is a 404 here too."""
    refused = clinics.client("manager.a@clinicq.example").post(
        f"/api/v1/sites/{clinics.site_b}/geocode", json={"address": "anywhere"}
    )
    assert refused.status_code == status.HTTP_404_NOT_FOUND


def test_the_module_metadata_endpoint_needs_no_session(
    clinics: SimpleNamespace,
) -> None:
    """``/info`` is public, like every other module's."""
    from fastapi.testclient import TestClient

    with TestClient(clinics.app) as anonymous:
        info = anonymous.get("/api/v1/sites/info")
    assert info.status_code == status.HTTP_200_OK
    assert info.json()["context"] == "sites"
