"""A clinic finishes setting itself up, and asks to be listed (Issue 223).

The clinic's own side of onboarding. An operator adds it (Issue 222) and sends one link — the staff
invitation of Issue 22, issued for ``clinic_manager`` at that clinic — and the person who accepts it
lands on a checklist derived from the clinic's own rows.

What is asserted here:

* the checklist is **derived**, so a step finished through any route is finished on it;
* a clinic may make exactly one move on its own listing, ``draft → pending_verification``, and it
  cannot approve itself;
* an incomplete clinic is refused **with the list of what is missing**, not silently ignored;
* two steps are decisions no other row can express, and each has its own confirm;
* another clinic's setup is the guard's 404.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import delete, select
from starlette import status

from src.commons.enums import SiteStatus
from src.database.models import AuditEvent, ClinicService, Queue, Site, SiteOpeningHours

_ALL_DAY = {
    "days": [
        {"weekday": day, "spans": [{"opens_at": "07:00:00", "closes_at": "16:00:00"}]}
        for day in range(5)
    ]
}


def _setup(clinics: SimpleNamespace, who: str = "manager.a@clinicq.example"):  # type: ignore[no-untyped-def]
    """The checklist as ``who`` sees it for clinic A."""
    return clinics.client(who).get(f"/api/v1/sites/{clinics.site_a}/setup")


def _steps(body: dict) -> dict[str, bool]:
    """``{step: done}`` from a checklist response."""
    return {step["step"]: step["done"] for step in body["steps"]}


def _make_draft(clinics: SimpleNamespace) -> None:
    """Clinic A as a freshly created one: a draft, with nothing confirmed and no hours."""
    with clinics.session() as db:
        site = db.get(Site, clinics.site_a)
        assert site is not None
        site.status = SiteStatus.DRAFT.value
        site.setup_confirmed_details = False
        site.setup_confirmed_board = False
        site.setup_completed_at = None
        db.execute(
            delete(SiteOpeningHours).where(SiteOpeningHours.site_id == clinics.site_a)
        )
        db.commit()


def _finish_everything(clinics: SimpleNamespace) -> None:
    """Settle every step the honest way: real rows, through the real routes."""
    manager = clinics.client("manager.a@clinicq.example")
    with clinics.session() as db:
        # Rooms and services: the default sets a new clinic is created with (Issue 29).
        if not db.execute(select(Queue).where(Queue.site_id == clinics.site_a)).first():
            db.add(
                Queue(
                    site_id=clinics.site_a,
                    slug="triage",
                    name="Triage",
                    kind="general",
                    ticket_prefix="T",
                )
            )
        if not db.execute(
            select(ClinicService).where(ClinicService.site_id == clinics.site_a)
        ).first():
            db.add(
                ClinicService(
                    site_id=clinics.site_a,
                    slug="pharmacy",
                    name="Pharmacy",
                    category="pharmacy",
                )
            )
        db.commit()
    assert (
        manager.put(f"/api/v1/sites/{clinics.site_a}/hours", json=_ALL_DAY).status_code
        == status.HTTP_200_OK
    )
    manager.post(
        f"/api/v1/sites/{clinics.site_a}/staff/invitations",
        json={"email": "desk.new@clinicq.example", "role": "receptionist"},
    )
    assert (
        manager.post(f"/api/v1/sites/{clinics.site_a}/setup/details").status_code
        == status.HTTP_200_OK
    )
    assert (
        manager.post(f"/api/v1/sites/{clinics.site_a}/setup/board").status_code
        == status.HTTP_200_OK
    )


# --- the checklist is derived ------------------------------------------------------------------


def test_a_fresh_clinic_has_a_checklist_with_things_still_to_do(
    clinics: SimpleNamespace,
) -> None:
    """Rooms and services come with a clinic; hours, the two decisions and staff do not."""
    _make_draft(clinics)

    body = _setup(clinics).json()
    done = _steps(body)

    assert body["complete"] is False
    assert body["can_submit"] is False
    assert done["hours"] is False
    assert done["clinic"] is False, (
        "somebody else typed the details; the clinic has not looked"
    )
    assert done["board"] is False, "number_only is the default, not a decision"
    assert body["total_count"] == len(body["steps"]) == 6


def test_setting_the_hours_anywhere_settles_that_step(clinics: SimpleNamespace) -> None:
    """Derived, not remembered: the hours route knows nothing about the checklist."""
    _make_draft(clinics)
    assert _steps(_setup(clinics).json())["hours"] is False

    clinics.client("manager.a@clinicq.example").put(
        f"/api/v1/sites/{clinics.site_a}/hours", json=_ALL_DAY
    )

    body = _setup(clinics).json()
    assert _steps(body)["hours"] is True
    assert "5 days a week" in [step["detail"] for step in body["steps"]]


def test_the_two_decisions_have_their_own_confirms(clinics: SimpleNamespace) -> None:
    """Neither is visible in any other row, which is exactly why each has a column."""
    _make_draft(clinics)
    manager = clinics.client("manager.a@clinicq.example")

    after_details = manager.post(f"/api/v1/sites/{clinics.site_a}/setup/details").json()
    after_board = manager.post(f"/api/v1/sites/{clinics.site_a}/setup/board").json()

    assert _steps(after_details)["clinic"] is True
    assert _steps(after_board)["board"] is True
    # Confirming the board changes no setting: it records that somebody looked.
    with clinics.session() as db:
        site = db.get(Site, clinics.site_a)
        assert site is not None
        assert site.display_mode == "number_only"


# --- asking to be listed ------------------------------------------------------------------------


def test_an_unfinished_clinic_is_refused_and_told_what_is_missing(
    clinics: SimpleNamespace,
) -> None:
    """The most useful sentence on the page: refusing silently would be the worst outcome."""
    _make_draft(clinics)

    refused = clinics.client("manager.a@clinicq.example").post(
        f"/api/v1/sites/{clinics.site_a}/setup/submit"
    )

    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = refused.json()["detail"]
    assert "Set your opening hours" in detail
    assert "Check your clinic's details" in detail
    with clinics.session() as db:
        site = db.get(Site, clinics.site_a)
        assert site is not None
        assert site.status == SiteStatus.DRAFT.value, "nothing moved"


def test_a_finished_clinic_puts_itself_forward_and_lands_in_the_admin_queue(
    clinics: SimpleNamespace,
) -> None:
    """``draft → pending_verification``, through the one writer of ``status``."""
    _make_draft(clinics)
    _finish_everything(clinics)
    assert _setup(clinics).json()["can_submit"] is True

    submitted = clinics.client("manager.a@clinicq.example").post(
        f"/api/v1/sites/{clinics.site_a}/setup/submit"
    )

    assert submitted.status_code == status.HTTP_200_OK, submitted.text
    assert submitted.json()["status"] == SiteStatus.PENDING_VERIFICATION.value
    assert submitted.json()["waiting"] is True
    assert submitted.json()["completed_at"] is not None

    queue = clinics.client("operator@clinicq.example").get("/api/v1/sites/verification")
    assert clinics.site_a in {item["id"] for item in queue.json()["items"]}


def test_a_clinic_cannot_approve_itself(clinics: SimpleNamespace) -> None:
    """Submitting is ``assigned``; the decision is ``business``, and a manager holds nothing there."""
    _make_draft(clinics)
    _finish_everything(clinics)
    manager = clinics.client("manager.a@clinicq.example")
    manager.post(f"/api/v1/sites/{clinics.site_a}/setup/submit")

    approving = manager.put(
        f"/api/v1/sites/{clinics.site_a}/verification",
        json={"status": SiteStatus.VERIFIED.value},
    )

    assert approving.status_code == status.HTTP_403_FORBIDDEN


def test_a_clinic_already_waiting_is_not_put_forward_twice(
    clinics: SimpleNamespace,
) -> None:
    """``pending_verification`` cannot move to itself: the state machine says so, and so does this."""
    _make_draft(clinics)
    _finish_everything(clinics)
    manager = clinics.client("manager.a@clinicq.example")
    assert (
        manager.post(f"/api/v1/sites/{clinics.site_a}/setup/submit").status_code
        == status.HTTP_200_OK
    )

    again = manager.post(f"/api/v1/sites/{clinics.site_a}/setup/submit")

    assert again.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "not waiting to be put forward" in again.json()["detail"]


def test_a_verified_clinic_has_nothing_to_submit(clinics: SimpleNamespace) -> None:
    """It is already listed. The checklist still renders, because it is still worth reading."""
    _finish_everything(clinics)

    body = _setup(clinics).json()

    assert body["status"] == SiteStatus.VERIFIED.value
    assert body["complete"] is True
    assert body["can_submit"] is False
    assert body["waiting"] is False


def test_submitting_is_audited_and_announced_like_every_other_move(
    clinics: SimpleNamespace,
) -> None:
    """It goes through ``onboarding.transition``, so it gets the trail every transition gets."""
    _make_draft(clinics)
    _finish_everything(clinics)
    clinics.client("manager.a@clinicq.example").post(
        f"/api/v1/sites/{clinics.site_a}/setup/submit"
    )

    with clinics.session() as db:
        rows = list(
            db.execute(
                select(AuditEvent).where(AuditEvent.entity_id == clinics.site_a)
            ).scalars()
        )

    assert any(
        "draft -> pending_verification" in (row.context or "") for row in rows
    ), [row.context for row in rows]
    assert any(row.actor == "manager.a@clinicq.example" for row in rows)


# --- whose setup it is ---------------------------------------------------------------------------


def test_another_clinics_setup_is_a_404_not_a_403(clinics: SimpleNamespace) -> None:
    """Non-negotiable 3: an id that is not yours looks exactly like one that never existed."""
    manager = clinics.client("manager.a@clinicq.example")

    assert (
        manager.get(f"/api/v1/sites/{clinics.site_b}/setup").status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert (
        manager.post(f"/api/v1/sites/{clinics.site_b}/setup/submit").status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_the_front_desk_reads_nothing_and_settles_nothing(
    clinics: SimpleNamespace,
) -> None:
    """Setting the clinic up is the manager's job; the desk runs the queue."""
    desk = clinics.client("desk.a@clinicq.example")

    assert (
        desk.get(f"/api/v1/sites/{clinics.site_a}/setup").status_code
        == status.HTTP_403_FORBIDDEN
    )
    assert (
        desk.post(f"/api/v1/sites/{clinics.site_a}/setup/details").status_code
        == status.HTTP_403_FORBIDDEN
    )
