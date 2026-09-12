"""Who works where: site membership and room assignment (Issue 28).

A nurse signed into Room 2 should see Room 2, and a receptionist who has left should stop being a
receptionist **on their next request**, not on their next sign-in. Both of those are properties of
where this module stores things, so they are worth stating before the code:

* **Site membership is a role held at a site** (``user_roles`` with ``scope_type='site'``, Issue
  15), which is the same record Issue 19's site guard resolves by. There is deliberately no
  ``staff_site_assignments`` table: a second answer to "who works here" is a second answer that can
  disagree with the one authorization uses.
* **Room membership is** :class:`~src.database.models.staff_queue_assignment.StaffQueueAssignment`,
  and this module writes the kernel's queue-scoped ``user_roles`` row beside it in the same
  transaction, because ``permitted_queue_ids`` reads that one. One writer, both rows.
* **Nothing about either lives in a token.** An access token carries the caller's identity;
  everything about which clinics and queues they reach is read from these tables on every request
  (``active_role_assignments``). That is what makes removal take effect on the next request rather
  than the next sign-in, and it is why the test for that criterion makes a call with a token minted
  *before* the change.

**A clinic always keeps at least one clinic manager.** Removing the last one is refused, because a
clinic with no manager is a clinic nobody can configure, invite staff to or close — and the way out
of it would be a platform admin editing rows by hand.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import AssignmentScopeType, UserRole
from src.core.site_scope import SiteAccess, scoped_select, staff_at_site
from src.database.models import (
    Queue,
    StaffQueueAssignment,
    User,
    UserRoleAssignment,
)

#: The role a clinic may never be left without. A clinic with no manager cannot configure itself,
#: invite anybody or close for the afternoon, and the only way back is somebody editing rows.
INDISPENSABLE_ROLE = UserRole.CLINIC_MANAGER

#: The roles a clinic manager may grant and remove at their own clinic. A platform admin is not on
#: this list: making another platform admin is the operator's business, not a clinic's (Issue 22
#: holds the same line for invitations).
ASSIGNABLE_AT_A_SITE: frozenset[UserRole] = frozenset(
    {UserRole.CLINIC_MANAGER, UserRole.RECEPTIONIST, UserRole.NURSE_DOCTOR}
)


class LastManagerError(PermissionError):
    """Removing this would leave the clinic with no manager."""


class RoleNotAssignableError(PermissionError):
    """That role is not one a clinic manager may grant at their own clinic."""


class NotAtThisClinicError(LookupError):
    """That queue, or that person, does not belong to this clinic."""


@dataclass(frozen=True, slots=True)
class RoomAssignment:
    """One staff member's place in one queue, as the API reports it."""

    queue_id: str
    queue_name: str
    room_label: str | None
    is_active: bool


# --------------------------------------------------------------------------------------
# Site membership: a role held at a site
# --------------------------------------------------------------------------------------


def _assignments_at(
    db: Session, user_id: str, access: SiteAccess
) -> list[UserRoleAssignment]:
    """Every role assignment ``user_id`` holds **at this clinic**, expired ones included.

    Not site-guarded by :func:`scoped_select` because ``user_roles`` has no ``site_id`` column —
    the clinic *is* its ``scope_id``. The filter below is the same one
    :func:`~src.core.site_scope.roles_held_at_site` applies, written once here for the writes.
    """
    return list(
        db.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.scope_type == AssignmentScopeType.SITE.value,
                UserRoleAssignment.scope_id == access.site_id,
            )
        ).scalars()
    )


def managers_at(db: Session, access: SiteAccess) -> list[User]:
    """Everyone holding the clinic-manager role at this clinic, by email.

    Built on :func:`~src.core.site_scope.staff_at_site`, so the site filter is the guard's.
    """
    return list(
        db.execute(
            staff_at_site(access, roles=[INDISPENSABLE_ROLE]).order_by(User.email)
        ).scalars()
    )


def grant_role_at_site(
    db: Session, access: SiteAccess, user_id: str, role: UserRole
) -> UserRoleAssignment:
    """Give ``user_id`` ``role`` **at this clinic**. The caller commits.

    Idempotent: granting a role somebody already holds here returns the existing row rather than
    creating a second one the unique constraint would refuse.

    Raises:
        RoleNotAssignableError: If the role is not one a clinic may grant.
    """
    if role not in ASSIGNABLE_AT_A_SITE:
        raise RoleNotAssignableError(
            f"A clinic cannot grant the {role.value!r} role; that is the operator's to give."
        )
    existing = next(
        (row for row in _assignments_at(db, user_id, access) if row.role == role.value),
        None,
    )
    if existing is not None:
        return existing
    assignment = UserRoleAssignment(
        user_id=user_id,
        role=role.value,
        scope_type=AssignmentScopeType.SITE.value,
        scope_id=access.site_id,
        granted_by=str(access.user.id),
    )
    db.add(assignment)
    db.flush()
    return assignment


def revoke_role_at_site(
    db: Session, access: SiteAccess, user_id: str, role: UserRole
) -> None:
    """Take ``role`` away from ``user_id`` at this clinic. The caller commits.

    Removing somebody's **last** role at a clinic also clears their room assignments there: a
    person who no longer works at a clinic cannot be on one of its rooms, and leaving the rows
    behind would put them back on Room 2 the day they are re-added.

    Raises:
        LastManagerError: If this would leave the clinic with no manager.
        NotAtThisClinicError: If they do not hold that role here.
    """
    here = _assignments_at(db, user_id, access)
    target = next((row for row in here if row.role == role.value), None)
    if target is None:
        raise NotAtThisClinicError("Not found.")
    if role is INDISPENSABLE_ROLE and len(managers_at(db, access)) <= 1:
        raise LastManagerError(
            "A clinic must always have at least one clinic manager. Add another before "
            "removing this one."
        )
    db.delete(target)
    db.flush()
    if len(here) == 1:  # that was their last role here
        clear_room_assignments(db, access, user_id)


# --------------------------------------------------------------------------------------
# Room membership: staff_queue_assignment, plus the kernel's queue-scoped row
# --------------------------------------------------------------------------------------


def _queues_at(
    db: Session, access: SiteAccess, queue_ids: Sequence[str]
) -> list[Queue]:
    """This clinic's queues among ``queue_ids``, in display order.

    Raises:
        NotAtThisClinicError: If any id is not one of this clinic's queues. Unlike the services
            catalogue (Issue 26), which drops a stale id, an assignment names people and rooms by
            hand: a manager who mistypes a queue should be told, not silently given a shorter list.
    """
    if not queue_ids:
        return []
    found = list(
        db.execute(
            scoped_select(Queue, access)
            .where(Queue.id.in_(queue_ids), Queue.is_deleted.is_(False))
            .order_by(Queue.display_order, Queue.name)
        ).scalars()
    )
    if len(found) != len(set(queue_ids)):
        raise NotAtThisClinicError("Not found.")
    return found


def room_assignments(
    db: Session, access: SiteAccess, user_id: str
) -> list[RoomAssignment]:
    """The rooms ``user_id`` is on at this clinic, active ones first, in the clinic's own order."""
    rows = list(
        db.execute(
            scoped_select(StaffQueueAssignment, access).where(
                StaffQueueAssignment.user_id == user_id
            )
        ).scalars()
    )
    by_id = {
        queue.id: queue for queue in db.execute(scoped_select(Queue, access)).scalars()
    }
    out = [
        RoomAssignment(
            queue_id=row.queue_id,
            queue_name=by_id[row.queue_id].name if row.queue_id in by_id else "",
            room_label=by_id[row.queue_id].room_label
            if row.queue_id in by_id
            else None,
            is_active=row.is_active,
        )
        for row in rows
        if row.queue_id in by_id
    ]
    return sorted(out, key=lambda item: (not item.is_active, item.queue_name))


def set_room_assignments(
    db: Session, access: SiteAccess, user_id: str, queue_ids: Sequence[str]
) -> list[RoomAssignment]:
    """Put ``user_id`` on exactly these rooms at this clinic. The caller commits.

    The **whole set** rather than one room at a time, because "which rooms does this nurse work" is
    one decision and a per-room edit is how somebody ends up on a room nobody meant to leave them
    on. A room they are taken off keeps its row with ``is_active`` cleared, so "who was on Room 2
    last Tuesday" stays answerable, and the kernel's queue-scoped ``user_roles`` row is written and
    removed alongside — the two are kept in step here and nowhere else.

    Raises:
        NotAtThisClinicError: If any queue is not one of this clinic's.
    """
    queues = _queues_at(db, access, queue_ids)
    wanted = {queue.id for queue in queues}
    existing = {
        row.queue_id: row
        for row in db.execute(
            scoped_select(StaffQueueAssignment, access).where(
                StaffQueueAssignment.user_id == user_id
            )
        ).scalars()
    }

    for queue_id, row in existing.items():
        row.is_active = queue_id in wanted
    for queue_id in wanted - set(existing):
        db.add(
            StaffQueueAssignment(
                site_id=access.site_id,
                user_id=user_id,
                queue_id=queue_id,
                assigned_by=str(access.user.id),
            )
        )
    db.flush()
    _sync_queue_scoped_roles(db, access, user_id, wanted)
    return room_assignments(db, access, user_id)


def clear_room_assignments(db: Session, access: SiteAccess, user_id: str) -> None:
    """Take ``user_id`` off every room at this clinic, keeping the rows. The caller commits."""
    set_room_assignments(db, access, user_id, [])


def _sync_queue_scoped_roles(
    db: Session, access: SiteAccess, user_id: str, queue_ids: set[str]
) -> None:
    """Make the kernel's ``user_roles(scope_type='queue')`` rows match ``queue_ids``.

    This is the shadow of the assignment table, and the only reason it exists is that Issue 19's
    :func:`~src.core.site_scope.permitted_queue_ids` — and therefore a nurse's ``own``-tier
    call-next grant — resolves from ``user_roles``. Writing it here, in the same transaction as the
    assignment, is what keeps the two from disagreeing; a test asserts they always do.

    The role written is the one the person holds **at this clinic**, so a receptionist put on a room
    gets a queue-scoped receptionist row and a nurse gets a nurse one.
    """
    roles_here = {row.role for row in _assignments_at(db, user_id, access)}
    scoped = list(
        db.execute(
            select(UserRoleAssignment).where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.scope_type == AssignmentScopeType.QUEUE.value,
            )
        ).scalars()
    )
    this_clinic = {
        queue.id for queue in db.execute(scoped_select(Queue, access)).scalars()
    }
    for row in scoped:
        # Only this clinic's rows are ours to remove: the same person may work elsewhere.
        if row.scope_id in this_clinic and row.scope_id not in queue_ids:
            db.delete(row)
    held = {row.scope_id for row in scoped if row.scope_id in this_clinic}
    for queue_id in queue_ids - held:
        for role in sorted(roles_here):
            db.add(
                UserRoleAssignment(
                    user_id=user_id,
                    role=role,
                    scope_type=AssignmentScopeType.QUEUE.value,
                    scope_id=queue_id,
                    granted_by=str(access.user.id),
                )
            )
    db.flush()


# --------------------------------------------------------------------------------------
# The check a call-next route makes (Issue 42 calls it; the rule is decided here)
# --------------------------------------------------------------------------------------


def may_work_queue(db: Session, user: User, queue_id: str) -> bool:
    """Whether ``user`` is assigned to ``queue_id``.

    The row-level half of "a nurse assigned to Room 2 cannot call next on Room 3". The grant's
    **tier** decides whether it is asked at all: a receptionist holds ``queues.call`` at
    ``assigned``, which reaches every queue at their clinics, so the route does not ask this; a
    nurse holds it at ``own``, which is exactly this question.

    Read from the database on every call, never from a token, which is what makes taking somebody
    off a room take effect on their next request.
    """
    from src.core.site_scope import permitted_queue_ids

    return queue_id in permitted_queue_ids(db, user)
