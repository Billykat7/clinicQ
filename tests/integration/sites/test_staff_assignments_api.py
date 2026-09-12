"""Who works where, and what happens the moment that changes (Issue 28).

Every acceptance criterion over real HTTP, and two of them are about *timing* rather than about
permissions, so they are tested the way the issue asks:

* **removal takes effect on the next request, not the next sign-in** — asserted with an access
  token minted **before** the change, still signed and unexpired, used **after** it;
* **a nurse assigned to Room 2 cannot call next on Room 3** — asserted at the row level the
  call-next route (Issue 42) will ask, because the route itself does not exist yet.

The rest: a staff member at two clinics switches without signing out, a clinic always keeps at least
one manager, and every change is audited.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from starlette import status

from src.commons.enums import (
    AssignmentScopeType,
    AuditAction,
    AuditEntityType,
    UserRole,
)
from src.core.security import create_access_token
from src.database.models import (
    AuditEvent,
    StaffQueueAssignment,
    User,
    UserRoleAssignment,
)
from src.modules.staff.assignments import may_work_queue


def _manager(clinics: SimpleNamespace):
    """The clinic manager at Clinic A."""
    return clinics.client("manager.a@clinicq.example")


def _user_id(clinics: SimpleNamespace, email: str) -> str:
    """One account's id."""
    with clinics.session() as db:
        return db.execute(select(User.id).where(User.email == email)).scalar_one()


def _rooms(clinics: SimpleNamespace, names: tuple[str, ...]) -> list[str]:
    """Create the named queues at Clinic A and return their ids."""
    manager = _manager(clinics)
    return [
        manager.post(
            f"/api/v1/sites/{clinics.site_a}/queues",
            json={
                "name": name,
                "slug": name.lower().replace(" ", "-"),
                "kind": "consultation",
                "room_label": name,
            },
        ).json()["id"]
        for name in names
    ]


# --- room assignment ----------------------------------------------------------------------


def test_a_nurse_assigned_to_room_2_cannot_work_room_3(
    clinics: SimpleNamespace,
) -> None:
    """The criterion, at the row level the call-next route (Issue 42) will ask at.

    The **grant's tier** decides whether the question is asked at all — a receptionist holds
    ``queues.call`` at ``assigned`` and reaches every queue at their clinic, a nurse holds it at
    ``own`` — and this is that question.
    """
    room_2, room_3 = _rooms(clinics, ("Room 2", "Room 3"))
    nurse_id = _user_id(clinics, "nurse.a@clinicq.example")

    assigned = _manager(clinics).put(
        f"/api/v1/sites/{clinics.site_a}/staff/{nurse_id}/queues",
        json={"queue_ids": [room_2]},
    )

    assert assigned.status_code == status.HTTP_200_OK, assigned.text
    assert [item["queue_id"] for item in assigned.json()["items"]] == [room_2]
    with clinics.session() as db:
        nurse = db.get(User, nurse_id)
        assert nurse is not None
        assert may_work_queue(db, nurse, room_2) is True
        assert may_work_queue(db, nurse, room_3) is False


def test_the_assignment_row_and_the_kernels_queue_scope_stay_in_step(
    clinics: SimpleNamespace,
) -> None:
    """Two records, one writer.

    ``staff_queue_assignment`` is the clinic's record (who assigned whom, and when); the kernel's
    queue-scoped ``user_roles`` row is what ``permitted_queue_ids`` resolves a nurse's ``own``-tier
    grant from. Writing only one of them is the bug this asserts against.
    """
    room_2, room_3 = _rooms(clinics, ("Room 2", "Room 3"))
    nurse_id = _user_id(clinics, "nurse.a@clinicq.example")
    manager = _manager(clinics)

    manager.put(
        f"/api/v1/sites/{clinics.site_a}/staff/{nurse_id}/queues",
        json={"queue_ids": [room_2, room_3]},
    )
    with clinics.session() as db:
        assert (
            _active_rooms(db, nurse_id)
            == _queue_scopes(db, nurse_id)
            == {room_2, room_3}
        )

    manager.put(
        f"/api/v1/sites/{clinics.site_a}/staff/{nurse_id}/queues",
        json={"queue_ids": [room_3]},
    )
    with clinics.session() as db:
        assert _active_rooms(db, nurse_id) == _queue_scopes(db, nurse_id) == {room_3}
        # The row for Room 2 is kept, switched off: "who was on Room 2 last Tuesday" still answers.
        kept = db.execute(
            select(StaffQueueAssignment).where(
                StaffQueueAssignment.user_id == nurse_id,
                StaffQueueAssignment.queue_id == room_2,
            )
        ).scalar_one()
        assert kept.is_active is False


def _active_rooms(db, user_id: str) -> set[str]:
    """The queues a staff member is currently on, from the clinic's own record."""
    return {
        row.queue_id
        for row in db.execute(
            select(StaffQueueAssignment).where(
                StaffQueueAssignment.user_id == user_id,
                StaffQueueAssignment.is_active.is_(True),
            )
        ).scalars()
    }


def _queue_scopes(db, user_id: str) -> set[str]:
    """The queues the kernel's scope resolver would admit, from ``user_roles``."""
    return {
        row.scope_id
        for row in db.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.scope_type == AssignmentScopeType.QUEUE.value,
            )
        ).scalars()
        if row.scope_id
    }


def test_another_clinics_queue_cannot_be_assigned(clinics: SimpleNamespace) -> None:
    """A mistyped or stale queue id is refused here, not silently dropped.

    Different from the services catalogue (Issue 26), which drops one: an assignment names a person
    and a room by hand, so a manager who gets it wrong should be told.
    """
    at_b = (
        clinics.client("manager.b@clinicq.example")
        .post(
            f"/api/v1/sites/{clinics.site_b}/queues",
            json={"name": "Their room", "slug": "their-room"},
        )
        .json()["id"]
    )
    nurse_id = _user_id(clinics, "nurse.a@clinicq.example")

    refused = _manager(clinics).put(
        f"/api/v1/sites/{clinics.site_a}/staff/{nurse_id}/queues",
        json={"queue_ids": [at_b]},
    )

    assert refused.status_code == status.HTTP_404_NOT_FOUND


# --- timing: the next request, not the next sign-in ------------------------------------------


def test_removing_an_assignment_takes_effect_on_the_very_next_request(
    clinics: SimpleNamespace,
) -> None:
    """The criterion, proven the only way it can be: with a token minted before the change.

    The token below is signed, unexpired and would be accepted for fifteen minutes. It stops
    reaching this clinic the moment the removal commits, because *which clinics somebody reaches*
    is read from ``user_roles`` on every request and is not a claim in the token.
    """
    desk_id = _user_id(clinics, "desk.a@clinicq.example")
    token = create_access_token(
        sub="desk.a@clinicq.example",
        email="desk.a@clinicq.example",
        uid=desk_id,
        role=UserRole.RECEPTIONIST.value,
        sites=[clinics.site_a],
    )
    headers = {"Authorization": f"Bearer {token}"}
    from fastapi.testclient import TestClient

    caller = TestClient(clinics.app)
    before = caller.get(f"/api/v1/sites/{clinics.site_a}/staff", headers=headers)
    assert before.status_code == status.HTTP_200_OK

    removed = _manager(clinics).delete(
        f"/api/v1/sites/{clinics.site_a}/staff/{desk_id}/roles/receptionist"
    )
    assert removed.status_code == status.HTTP_200_OK, removed.text

    # The same token, one request later.
    after = caller.get(f"/api/v1/sites/{clinics.site_a}/staff", headers=headers)
    assert after.status_code == status.HTTP_404_NOT_FOUND


def test_losing_the_last_role_at_a_clinic_clears_the_rooms_there(
    clinics: SimpleNamespace,
) -> None:
    """Somebody who no longer works at a clinic cannot be on one of its rooms.

    Leaving the rows on would put them back on Room 2 the day they are re-added, which is the kind
    of thing nobody notices until a nurse is called for a room they have not worked in months.
    """
    room_2 = _rooms(clinics, ("Room 2",))[0]
    nurse_id = _user_id(clinics, "nurse.a@clinicq.example")
    manager = _manager(clinics)
    manager.put(
        f"/api/v1/sites/{clinics.site_a}/staff/{nurse_id}/queues",
        json={"queue_ids": [room_2]},
    )

    manager.delete(
        f"/api/v1/sites/{clinics.site_a}/staff/{nurse_id}/roles/nurse_doctor"
    )

    with clinics.session() as db:
        nurse = db.get(User, nurse_id)
        assert nurse is not None
        assert _active_rooms(db, nurse_id) == set()
        assert may_work_queue(db, nurse, room_2) is False


# --- one person, two clinics ------------------------------------------------------------------


def test_somebody_at_two_clinics_switches_between_them_without_signing_out(
    clinics: SimpleNamespace,
) -> None:
    """The site switcher's premise (Issue 48 builds the screen; this is what it reads)."""
    desk_id = _user_id(clinics, "desk.a@clinicq.example")
    granted = clinics.client("manager.b@clinicq.example").post(
        f"/api/v1/sites/{clinics.site_b}/staff/{desk_id}/roles",
        json={"role": UserRole.RECEPTIONIST.value},
    )

    assert granted.status_code == status.HTTP_200_OK, granted.text
    desk = clinics.client("desk.a@clinicq.example")
    assert desk.get(f"/api/v1/sites/{clinics.site_a}/staff").status_code == 200
    assert desk.get(f"/api/v1/sites/{clinics.site_b}/staff").status_code == 200


def test_a_clinic_cannot_grant_the_platform_admin_role(
    clinics: SimpleNamespace,
) -> None:
    """Making another operator is the operator's business, not a clinic's (as in Issue 22)."""
    desk_id = _user_id(clinics, "desk.a@clinicq.example")
    refused = _manager(clinics).post(
        f"/api/v1/sites/{clinics.site_a}/staff/{desk_id}/roles",
        json={"role": UserRole.PLATFORM_ADMIN.value},
    )
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    assert "operator's" in refused.json()["detail"]


# --- a clinic always keeps a manager -----------------------------------------------------------


def test_removing_the_last_clinic_manager_is_refused(
    clinics: SimpleNamespace,
) -> None:
    """A clinic with no manager cannot configure itself, invite anybody or close for the afternoon."""
    manager_id = _user_id(clinics, "manager.a@clinicq.example")

    refused = _manager(clinics).delete(
        f"/api/v1/sites/{clinics.site_a}/staff/{manager_id}/roles/clinic_manager"
    )

    assert refused.status_code == status.HTTP_409_CONFLICT
    assert "at least one clinic manager" in refused.json()["detail"]
    assert (
        _manager(clinics).get(f"/api/v1/sites/{clinics.site_a}/staff").status_code
        == status.HTTP_200_OK
    )


def test_the_second_to_last_manager_may_go_once_there_are_two(
    clinics: SimpleNamespace,
) -> None:
    """The rule is "at least one", not "never remove a manager": the check is discriminating."""
    manager = _manager(clinics)
    desk_id = _user_id(clinics, "desk.a@clinicq.example")
    manager.post(
        f"/api/v1/sites/{clinics.site_a}/staff/{desk_id}/roles",
        json={"role": UserRole.CLINIC_MANAGER.value},
    )

    manager_id = _user_id(clinics, "manager.a@clinicq.example")
    removed = manager.delete(
        f"/api/v1/sites/{clinics.site_a}/staff/{manager_id}/roles/clinic_manager"
    )

    assert removed.status_code == status.HTTP_200_OK
    assert removed.json()["roles"] == []


# --- the trail and the guard --------------------------------------------------------------------


def test_every_assignment_change_is_audited_with_the_acting_manager(
    clinics: SimpleNamespace,
) -> None:
    """Who put whom on which room, and who took a role away, with the clinic on every row."""
    room_2 = _rooms(clinics, ("Room 2",))[0]
    nurse_id = _user_id(clinics, "nurse.a@clinicq.example")
    manager = _manager(clinics)
    manager.put(
        f"/api/v1/sites/{clinics.site_a}/staff/{nurse_id}/queues",
        json={"queue_ids": [room_2]},
    )
    manager.delete(
        f"/api/v1/sites/{clinics.site_a}/staff/{nurse_id}/roles/nurse_doctor"
    )

    with clinics.session() as db:
        rows = (
            db.execute(
                select(AuditEvent)
                .where(AuditEvent.entity_type == AuditEntityType.USER.value)
                .order_by(AuditEvent.created_at)
            )
            .scalars()
            .all()
        )
    assert [row.action for row in rows] == [AuditAction.UPDATE.value] * 2
    assert {row.actor for row in rows} == {"manager.a@clinicq.example"}
    assert {row.site_id for row in rows} == {clinics.site_a}
    assert "room assignment: Room 2" in (rows[0].context or "")
    assert "removed the nurse_doctor role" in (rows[1].context or "")


def test_a_receptionist_may_read_the_rooms_and_not_change_them(
    clinics: SimpleNamespace,
) -> None:
    """Reading who is on which room is the staff list's grant; deciding it is the manager's."""
    nurse_id = _user_id(clinics, "nurse.a@clinicq.example")
    desk = clinics.client("desk.a@clinicq.example")

    assert (
        desk.get(f"/api/v1/sites/{clinics.site_a}/staff/{nurse_id}/queues").status_code
        == status.HTTP_200_OK
    )
    assert (
        desk.put(
            f"/api/v1/sites/{clinics.site_a}/staff/{nurse_id}/queues",
            json={"queue_ids": []},
        ).status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_another_clinics_staff_assignments_are_not_found(
    clinics: SimpleNamespace,
) -> None:
    """Non-negotiable 3: a colleague at Clinic B is a 404, for reads and for writes alike."""
    their_nurse = _user_id(clinics, "desk.b@clinicq.example")
    manager = _manager(clinics)

    assert (
        manager.get(
            f"/api/v1/sites/{clinics.site_b}/staff/{their_nurse}/queues"
        ).status_code
        == status.HTTP_404_NOT_FOUND
    )
    assert (
        manager.get(
            f"/api/v1/sites/{clinics.site_a}/staff/{their_nurse}/queues"
        ).status_code
        == status.HTTP_404_NOT_FOUND
    )
