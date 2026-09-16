"""A clinic signs itself up, and a platform admin checks it before patients see it (Issue 29).

Every acceptance criterion, and the first one is tested the way the issue asks: **against the search
service, not the page**. `src.modules.sites.discovery.search_sites` is the narrowing every
patient-facing surface goes through, so asserting there proves the rule for discovery (Issue 31),
the clinic detail page (Issue 35) and both channel menus at once.

The public form's endpoint takes no session, so the tests that use it build a bare client.
"""

from __future__ import annotations

from datetime import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from starlette import status

from src.commons.enums import (
    AuditAction,
    GrantScope,
    PermissionVerb,
    SiteSector,
    SiteStatus,
    TicketSource,
    UserRole,
)
from src.core import domain_events
from src.core.domain_events import SiteStatusChanged
from src.core.rbac_simulator import (
    SimulationDecision,
    SimulationPrincipal,
    SimulationTarget,
    simulate,
)
from src.database.models import AuditEvent, ClinicService, Queue, Site
from src.database.models.role_permission import RolePermission
from src.modules.sites.availability import NOT_ACCEPTING, join_gate
from src.modules.sites.discovery import search_sites
from src.modules.sites.hours import OpeningSchedule, TimeSpan
from src.modules.sites.onboarding import ALLOWED_TRANSITIONS

_FORM = {
    "name": "Zola Clinic",
    "slug": "zola-clinic",
    "sector": SiteSector.PUBLIC.value,
    "location": {"latitude": -26.26840, "longitude": 27.84720},
    "address_line": "Zola North, Soweto",
    "suburb": "Zola",
    "city": "Johannesburg",
    "province": "Gauteng",
    "phone_e164": "+27115551234",
    "contact_name": "Nomsa Dlamini",
    "contact_email": "nomsa@zolaclinic.example",
    "contact_phone": "+27821234567",
}

#: Open around the clock, so a join refusal in these tests is about the **status** and never the hour.
_ALWAYS_OPEN = OpeningSchedule(
    weekly=dict.fromkeys(range(7), (TimeSpan(time(0, 0), time(0, 0)),))
)


@pytest.fixture
def collected_events(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Capture everything published on the domain bus."""
    published: list[object] = []
    original = domain_events.publish

    def _capture(event: object) -> None:
        published.append(event)
        original(event)  # type: ignore[arg-type]

    monkeypatch.setattr(domain_events, "publish", _capture)
    return published


def _anonymous(clinics: SimpleNamespace) -> TestClient:
    """A client with no session: the public form's real audience."""
    return TestClient(clinics.app)


def _submit(clinics: SimpleNamespace, **overrides: object):
    """Submit the public registration form."""
    return _anonymous(clinics).post(
        "/api/v1/sites/register", json={**_FORM, **overrides}
    )


def _operator(clinics: SimpleNamespace):
    """The platform admin."""
    return clinics.client("operator@clinicq.example")


# --- submitting ------------------------------------------------------------------------------


def test_a_stranger_can_submit_a_clinic_and_it_is_waiting_not_listed(
    clinics: SimpleNamespace, collected_events: list[object]
) -> None:
    """The happy path, and what the submitter is told."""
    submitted = _submit(clinics)

    assert submitted.status_code == status.HTTP_201_CREATED, submitted.text
    body = submitted.json()
    assert body["status"] == SiteStatus.PENDING_VERIFICATION.value
    assert body["submitted_at"] is not None
    assert "will not appear to patients" in body["message"]
    event = next(e for e in collected_events if isinstance(e, SiteStatusChanged))
    assert event.to_status == SiteStatus.PENDING_VERIFICATION.value
    assert event.contact_email == "nomsa@zolaclinic.example"


def test_an_unverified_clinic_never_appears_in_a_discovery_search(
    clinics: SimpleNamespace,
) -> None:
    """The criterion, asserted against the **search service** rather than a page.

    `search_sites` is the narrowing every patient-facing surface goes through, so proving it here
    proves it for discovery, the clinic detail page and both channel menus at once.
    """
    _submit(clinics)

    with clinics.session() as db:
        found = {site.slug for site in search_sites(db, query="zola")}
        everything = {site.slug for site in db.execute(select(Site)).scalars()}

    assert "zola-clinic" in everything  # the row is there...
    assert found == set()  # ...and a patient cannot find it


@pytest.mark.parametrize(
    "invisible",
    [SiteStatus.DRAFT, SiteStatus.PENDING_VERIFICATION, SiteStatus.SUSPENDED],
)
def test_only_a_verified_clinic_is_ever_returned_to_a_patient(
    clinics: SimpleNamespace, invisible: SiteStatus
) -> None:
    """Every status but ``verified`` is invisible, so a fourth one cannot be added as visible."""
    with clinics.session() as db:
        site = db.get(Site, clinics.site_a)
        assert site is not None
        site.status = invisible.value
        db.commit()
        assert clinics.site_a not in {row.id for row in search_sites(db)}
        site = db.get(Site, clinics.site_a)
        assert site is not None
        site.status = SiteStatus.VERIFIED.value
        db.commit()
        assert clinics.site_a in {row.id for row in search_sites(db)}


def test_a_submitted_clinic_arrives_ready_rather_than_empty(
    clinics: SimpleNamespace,
) -> None:
    """Default queues and a services catalogue, so an approving admin sees a working clinic."""
    site_id = _submit(clinics).json()["id"]

    with clinics.session() as db:
        queues = (
            db.execute(select(Queue).where(Queue.site_id == site_id)).scalars().all()
        )
        services = (
            db.execute(select(ClinicService).where(ClinicService.site_id == site_id))
            .scalars()
            .all()
        )
        site = db.get(Site, site_id)

    assert len(queues) >= 3
    assert len(services) >= 5
    # And non-negotiable 4 holds on this path too.
    assert site is not None and site.display_mode == "number_only"


def test_a_submission_grants_nothing_and_reveals_nothing(
    clinics: SimpleNamespace,
) -> None:
    """No session, no role, and the submitter cannot read the queue they are now in."""
    site_id = _submit(clinics).json()["id"]
    anonymous = _anonymous(clinics)

    assert anonymous.get("/api/v1/sites/verification").status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )
    assert anonymous.get(f"/api/v1/sites/{site_id}").status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


def test_a_slug_somebody_else_holds_is_refused_without_saying_whose(
    clinics: SimpleNamespace,
) -> None:
    """A stranger must not learn from a refusal whether a clinic exists and is listed."""
    with clinics.session() as db:
        site = db.get(Site, clinics.site_a)
        assert site is not None
        taken = site.slug

    refused = _submit(clinics, slug=taken)

    assert refused.status_code == status.HTTP_409_CONFLICT
    detail = refused.json()["detail"]
    assert "already in use" in detail
    assert "verified" not in detail and "pending" not in detail


def test_the_form_is_validated_the_same_way_the_operators_path_is(
    clinics: SimpleNamespace,
) -> None:
    """A stranger gets the same bounding-box rule an operator does (Issue 23)."""
    refused = _submit(clinics, location={"latitude": 0.0, "longitude": 0.0})
    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "South Africa" in refused.text


# --- deciding --------------------------------------------------------------------------------


def _decide(clinics: SimpleNamespace, site_id: str, **body: object):
    """The platform admin's decision endpoint."""
    return _operator(clinics).put(f"/api/v1/sites/{site_id}/verification", json=body)


def test_approving_a_clinic_lists_it_notifies_the_submitter_and_is_audited(
    clinics: SimpleNamespace, collected_events: list[object]
) -> None:
    """The whole approval path in one test."""
    site_id = _submit(clinics).json()["id"]

    approved = _decide(clinics, site_id, status=SiteStatus.VERIFIED.value)

    assert approved.status_code == status.HTTP_200_OK, approved.text
    assert approved.json()["status"] == SiteStatus.VERIFIED.value
    with clinics.session() as db:
        assert "zola-clinic" in {site.slug for site in search_sites(db, query="zola")}
        row = db.execute(
            select(AuditEvent).where(
                AuditEvent.entity_id == site_id,
                AuditEvent.action == AuditAction.UPDATE.value,
            )
        ).scalar_one()
    assert row.actor == "operator@clinicq.example"
    assert "pending_verification -> verified" in (row.context or "")
    decided = [
        e
        for e in collected_events
        if isinstance(e, SiteStatusChanged) and e.decided_by != "submitter"
    ]
    assert decided[-1].to_status == SiteStatus.VERIFIED.value
    assert decided[-1].contact_email == "nomsa@zolaclinic.example"


def test_sending_a_clinic_back_must_say_what_is_wrong(
    clinics: SimpleNamespace,
) -> None:
    """A rejection with no words is the one thing this workflow must not produce."""
    site_id = _submit(clinics).json()["id"]

    silent = _decide(clinics, site_id, status=SiteStatus.DRAFT.value)
    assert silent.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "shown exactly what you write" in silent.json()["detail"]

    spoken = _decide(
        clinics,
        site_id,
        status=SiteStatus.DRAFT.value,
        note="We could not find this address. Please check the street number.",
    )
    assert spoken.status_code == status.HTTP_200_OK
    assert "street number" in spoken.json()["review_note"]


def test_a_status_a_clinic_cannot_move_to_is_refused(
    clinics: SimpleNamespace,
) -> None:
    """The lifecycle is a state machine, not a column anybody may set to anything."""
    site_id = _submit(clinics).json()["id"]
    _decide(clinics, site_id, status=SiteStatus.VERIFIED.value)

    # verified → verified is not a move.
    refused = _decide(clinics, site_id, status=SiteStatus.VERIFIED.value)

    assert refused.status_code == status.HTTP_409_CONFLICT
    assert SiteStatus.VERIFIED not in ALLOWED_TRANSITIONS[SiteStatus.VERIFIED]


def test_suspending_a_clinic_stops_joins_immediately(
    clinics: SimpleNamespace,
) -> None:
    """The criterion: no cache to invalidate and no list to rebuild.

    The join gate reads the status on every request, so the instant the suspension commits the
    clinic refuses on every channel — and the refusal says nothing about *why* the listing is off,
    which is the clinic's business and the platform's.
    """
    site_id = _submit(clinics).json()["id"]
    _decide(clinics, site_id, status=SiteStatus.VERIFIED.value)

    with clinics.session() as db:
        site = db.get(Site, site_id)
        assert site is not None
        assert join_gate(site, _ALWAYS_OPEN).allowed is True

    suspended = _decide(
        clinics,
        site_id,
        status=SiteStatus.SUSPENDED.value,
        note="Reported closed; confirming with the district.",
    )
    assert suspended.status_code == status.HTTP_200_OK

    with clinics.session() as db:
        site = db.get(Site, site_id)
        assert site is not None
        gate = join_gate(site, _ALWAYS_OPEN)
        assert gate.allowed is False
        assert gate.reason == NOT_ACCEPTING
        assert site_id not in {row.id for row in search_sites(db)}
    # And the same answer whichever door the patient is at.
    assert {source.value for source in TicketSource} == {
        "web",
        "ussd",
        "whatsapp",
        "walk_in",
    }


def test_the_verification_queue_is_the_operators_alone(
    clinics: SimpleNamespace,
) -> None:
    """A clinic manager runs their clinic; they do not decide about anybody else's."""
    site_id = _submit(clinics).json()["id"]
    manager = clinics.client("manager.a@clinicq.example")

    assert manager.get("/api/v1/sites/verification").status_code == (
        status.HTTP_403_FORBIDDEN
    )
    assert (
        manager.put(
            f"/api/v1/sites/{site_id}/verification",
            json={"status": SiteStatus.VERIFIED.value},
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_the_queue_lists_what_is_waiting_oldest_first(
    clinics: SimpleNamespace,
) -> None:
    """What the console renders, including the contact the admin has to reach."""
    _submit(clinics)
    _submit(clinics, name="Mofolo Clinic", slug="mofolo-clinic-2")

    queue = _operator(clinics).get("/api/v1/sites/verification").json()

    assert queue["total"] == 2
    assert [item["slug"] for item in queue["items"]] == [
        "zola-clinic",
        "mofolo-clinic-2",
    ]
    assert queue["items"][0]["contact_email"] == "nomsa@zolaclinic.example"


def test_the_queue_can_be_asked_for_any_status(clinics: SimpleNamespace) -> None:
    """The console's other tabs read the same endpoint with a different status."""
    site_id = _submit(clinics).json()["id"]
    _decide(clinics, site_id, status=SiteStatus.VERIFIED.value)

    verified = (
        _operator(clinics).get("/api/v1/sites/verification?status=verified").json()
    )

    assert site_id in {item["id"] for item in verified["items"]}
    assert _operator(clinics).get("/api/v1/sites/verification").json()["total"] == 0


def test_a_clinic_waiting_to_be_checked_can_walk_through_its_own_queue(
    clinics: SimpleNamespace,
) -> None:
    """Invisible in discovery, and joinable by direct link. The line, stated rather than implied.

    Issue 29 says only verified clinics appear in discovery and "the rest are reachable by direct
    link for testing". A clinic waiting to be checked has to be able to exercise its own queue
    before it goes live, and only somebody who already has its link can reach it. A ``draft``
    clinic is different: it has not been put forward at all, so there is nothing to test yet.
    """
    site_id = _submit(clinics).json()["id"]

    with clinics.session() as db:
        site = db.get(Site, site_id)
        assert site is not None
        assert site.status_enum is SiteStatus.PENDING_VERIFICATION
        assert join_gate(site, _ALWAYS_OPEN).allowed is True
        assert site_id not in {row.id for row in search_sites(db)}

        site.status = SiteStatus.DRAFT.value
        db.commit()
        site = db.get(Site, site_id)
        assert site is not None
        assert join_gate(site, _ALWAYS_OPEN).allowed is False


# --- who may decide, and who may remove (Issue 221) -------------------------------------------


def _narrow_operator_to_onboarding(clinics: SimpleNamespace) -> None:
    """Make ``platform_admin`` a verification officer: it decides listings and removes nothing.

    Swaps its one grant — ``sites`` + ``delete`` at ``business``, which cascades to everything under
    ``sites`` — for ``sites.onboarding`` + ``update`` at the same tier. That is a grant nobody could
    write before this issue, because deciding a clinic's listing *was* ``sites:delete``.
    """
    with clinics.session() as db:
        db.execute(
            delete(RolePermission).where(
                RolePermission.role == UserRole.PLATFORM_ADMIN.value,
                RolePermission.resource == "sites",
            )
        )
        db.add(
            RolePermission(
                role=UserRole.PLATFORM_ADMIN.value,
                resource="sites.onboarding",
                max_verb=PermissionVerb.UPDATE.value,
                scope=GrantScope.BUSINESS.value,
            )
        )
        db.commit()


def test_deciding_a_listing_no_longer_needs_the_grant_that_deletes_clinics(
    clinics: SimpleNamespace,
) -> None:
    """The whole point of ``sites.onboarding``: a verification officer is expressible.

    Before this issue the verification routes asked for ``sites`` + ``delete``, so authority to
    check clinics could not be handed out without authority to remove them.
    """
    site_id = _submit(clinics).json()["id"]
    _narrow_operator_to_onboarding(clinics)
    officer = _operator(clinics)

    assert officer.get("/api/v1/sites/verification").status_code == status.HTTP_200_OK
    decided = officer.put(
        f"/api/v1/sites/{site_id}/verification",
        json={"status": SiteStatus.VERIFIED.value},
    )
    assert decided.status_code == status.HTTP_200_OK, decided.text
    assert decided.json()["status"] == SiteStatus.VERIFIED.value

    # …and the clinic it just approved, it cannot remove.
    assert (
        officer.delete(f"/api/v1/sites/{site_id}").status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_a_platform_admin_decides_and_removes_exactly_as_before(
    clinics: SimpleNamespace,
) -> None:
    """The split takes nothing away: the shipped grant still reaches both, through the cascade."""
    site_id = _submit(clinics).json()["id"]
    operator = _operator(clinics)

    assert operator.get("/api/v1/sites/verification").status_code == status.HTTP_200_OK
    assert (
        operator.put(
            f"/api/v1/sites/{site_id}/verification",
            json={"status": SiteStatus.VERIFIED.value},
        ).status_code
        == status.HTTP_200_OK
    )
    assert (
        operator.delete(f"/api/v1/sites/{site_id}").status_code
        == status.HTTP_204_NO_CONTENT
    )


def test_a_clinic_reads_where_its_own_listing_stands_and_what_the_admin_wrote(
    clinics: SimpleNamespace,
) -> None:
    """The clinic side of the workflow: the review note is written for them, so they can read it.

    Read by the clinic's **own** manager, not by the operator: this route is site-scoped, and a
    platform admin is assigned to no clinic (Issue 19), so they reach one clinic's onboarding the
    way they reach anything else of one clinic's — through the audited cross-site hatch.
    """
    _decide(
        clinics,
        clinics.site_a,
        status=SiteStatus.PENDING_VERIFICATION.value,
        note="The street number does not match the one on your practice licence.",
    )

    state = (
        clinics.client("manager.a@clinicq.example")
        .get(f"/api/v1/sites/{clinics.site_a}/onboarding")
        .json()
    )

    assert state["status"] == SiteStatus.PENDING_VERIFICATION.value
    assert state["visible_to_patients"] is False
    assert state["can_submit_for_checking"] is False
    assert state["review_note"].startswith("The street number")
    assert state["reviewed_at"] is not None


def test_a_manager_reads_their_own_clinics_listing_and_no_other(
    clinics: SimpleNamespace,
) -> None:
    """``sites.onboarding`` at ``assigned``, through the cascade, and the guard does the rest."""
    manager = clinics.client("manager.a@clinicq.example")

    own = manager.get(f"/api/v1/sites/{clinics.site_a}/onboarding")
    assert own.status_code == status.HTTP_200_OK
    assert own.json()["visible_to_patients"] is True

    assert (
        manager.get(f"/api/v1/sites/{clinics.site_b}/onboarding").status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_a_clinic_manager_reaches_onboarding_but_never_at_the_deciding_tier(
    clinics: SimpleNamespace,
) -> None:
    """A manager reads their own clinic's onboarding state; the decision asks for ``business``.

    Their ``sites`` + ``update`` at ``assigned`` cascades to ``sites.onboarding`` at ``assigned`` —
    which is what the setup link (Issue 223) will ask for — and the verification routes ask for
    ``business``, which a manager holds nothing at.
    """
    with clinics.session() as db:
        decided = simulate(
            db,
            SimulationPrincipal(role=UserRole.CLINIC_MANAGER.value),
            SimulationTarget(
                resource="sites.onboarding", verb=PermissionVerb.UPDATE.value
            ),
        )

    assert decided.decision is SimulationDecision.ALLOW
    assert decided.effective_tier is GrantScope.ASSIGNED

    manager = clinics.client("manager.a@clinicq.example")
    assert (
        manager.put(
            f"/api/v1/sites/{clinics.site_a}/verification",
            json={"status": SiteStatus.SUSPENDED.value, "note": "closing for a week"},
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )
