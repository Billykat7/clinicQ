"""HTTP routes for a clinic's staff (Issues 19, 22).

Two routers, because they have two different audiences:

* ``router`` hangs everything off ``/sites/{site_id}/``, so the site is part of the request and
  :func:`~src.core.site_scope.require_site_access` answers "whose clinic" before anything else: a
  Clinic A token asking for Clinic B gets 404, the same answer a site that does not exist gets. The
  verb decides the rest — reading the staff list needs ``sites.staff:read``, which a receptionist
  holds; inviting and deactivating need ``create``/``update``, which only a clinic manager and a
  platform admin do.
* ``invitations_router`` is how someone who has **no account yet** accepts one. It is public by
  necessity, and it is safe because the token names one invitation row and that row decides
  everything: the clinic, the role, the deadline and whether it has already been used (Issue 22).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.commons.enums import AuditAction, AuditEntityType, UserRole
from src.core.audit import record_audit_event
from src.core.client_ip import resolve_client_ip
from src.core.rbac_language import role_label
from src.core.request_logging import bind_request_context
from src.core.site_scope import SiteAccess, require_site_access
from src.database.models import StaffInvitation
from src.database.session import get_db
from src.modules.staff import assignments, invitations, service
from src.modules.staff.schemas import (
    InvitationAcceptedOut,
    InvitationAcceptIn,
    InvitationPreviewOut,
    RoomAssignmentIn,
    RoomAssignmentListOut,
    RoomAssignmentOut,
    SiteRoleIn,
    StaffActiveIn,
    StaffInvitationListOut,
    StaffInvitationOut,
    StaffInviteIn,
    StaffListOut,
    StaffMemberOut,
)

router = APIRouter(prefix="/sites", tags=["staff"])
#: Accepting an invitation: no session, because the point is that there is not an account yet.
invitations_router = APIRouter(prefix="/staff/invitations", tags=["staff"])

DbSession = Annotated[Session, Depends(get_db)]
#: Reading the staff of the clinic in the path: the site gate and the ``sites.staff`` grant.
StaffRead = Annotated[SiteAccess, Depends(require_site_access("sites.staff", "read"))]
#: Inviting someone into the clinic in the path. A receptionist holds ``read`` and not this.
StaffCreate = Annotated[
    SiteAccess, Depends(require_site_access("sites.staff", "create"))
]
#: Switching an account off, or withdrawing an invitation.
StaffUpdate = Annotated[
    SiteAccess, Depends(require_site_access("sites.staff", "update"))
]


def _invitation_out(invitation: StaffInvitation) -> StaffInvitationOut:
    """One invitation as the API reports it — never carrying its link."""
    return StaffInvitationOut(
        id=str(invitation.id),
        email=invitation.email,
        role=invitation.role,
        site_id=invitation.site_id,
        state=invitations.invitation_state(invitation),
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        accepted_at=invitation.accepted_at,
        invited_by=invitation.invited_by,
    )


@router.get("/staff/info", summary="Module metadata", operation_id="staffInfo")
def staff_info() -> dict[str, str]:
    """Return staff module metadata (unauthenticated, like every other ``/info``)."""
    info = service.get_module_info()
    return {"context": info.context.value, "summary": info.summary}


@router.get(
    "/{site_id}/staff", response_model=StaffListOut, operation_id="staffListForSite"
)
def list_staff(access: StaffRead, db: DbSession) -> StaffListOut:
    """Everyone who works at this clinic."""
    return service.list_staff(db, access)


# --------------------------------------------------------------------------------------
# Invitations: issued for one clinic, accepted without an account (Issue 22)
#
# Declared **before** ``/{site_id}/staff/{user_id}``: FastAPI matches in declaration order, so
# a literal path segment that could also be read as an id has to come first, or
# ``/staff/invitations`` resolves as a staff member with the id "invitations" and 404s.
# --------------------------------------------------------------------------------------


@router.post(
    "/{site_id}/staff/invitations",
    response_model=StaffInvitationOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="staffInviteForSite",
)
def invite_staff_member(
    request: Request, body: StaffInviteIn, access: StaffCreate, db: DbSession
) -> StaffInvitationOut:
    """Invite someone to work at this clinic, and send them the link.

    The row and its audit line are committed **before** the message is sent, so a gateway that is
    down costs a delivery, never an invitation that half exists.
    """
    issued = invitations.invite_staff(
        db,
        access,
        email=str(body.email),
        role=UserRole(body.role),
        phone=body.phone,
        base_url=str(request.base_url),
    )
    db.commit()
    db.refresh(issued.invitation)
    invitations.deliver_invitation(db, issued)
    db.commit()
    return _invitation_out(issued.invitation)


@router.get(
    "/{site_id}/staff/invitations",
    response_model=StaffInvitationListOut,
    operation_id="staffInvitationsForSite",
)
def list_site_invitations(access: StaffRead, db: DbSession) -> StaffInvitationListOut:
    """Every invitation this clinic has issued, newest first."""
    items = [
        _invitation_out(invitation)
        for invitation in invitations.list_invitations(db, access)
    ]
    return StaffInvitationListOut(site_id=access.site_id, total=len(items), items=items)


@router.delete(
    "/{site_id}/staff/invitations/{invitation_id}",
    response_model=StaffInvitationOut,
    operation_id="staffRevokeInvitation",
)
def revoke_site_invitation(
    invitation_id: str, access: StaffUpdate, db: DbSession
) -> StaffInvitationOut:
    """Withdraw an invitation that has not been accepted yet."""
    invitation = invitations.revoke_invitation(db, access, invitation_id)
    db.commit()
    db.refresh(invitation)
    return _invitation_out(invitation)


@router.get(
    "/{site_id}/staff/{user_id}",
    response_model=StaffMemberOut,
    operation_id="staffGetForSite",
)
def get_staff_member(user_id: str, access: StaffRead, db: DbSession) -> StaffMemberOut:
    """One colleague at this clinic, or 404 for anyone else's id."""
    return service.get_staff_member(db, user_id, access)


@router.put(
    "/{site_id}/staff/{user_id}/active",
    response_model=StaffMemberOut,
    operation_id="staffSetActive",
)
def set_staff_member_active(
    user_id: str, body: StaffActiveIn, access: StaffUpdate, db: DbSession
) -> StaffMemberOut:
    """Deactivate or reactivate a colleague at this clinic.

    Deactivation takes effect on their very next request, and revokes every live session so a new
    access token cannot be minted from a refresh cookie either.
    """
    invitations.set_staff_active(db, access, user_id, active=body.is_active)
    db.commit()
    return service.get_staff_member(db, user_id, access)


@invitations_router.get(
    "/preview",
    response_model=InvitationPreviewOut,
    operation_id="staffInvitationPreview",
)
def preview_invitation(token: str, db: DbSession) -> InvitationPreviewOut:
    """What this invitation is for, so the acceptance page can say it in words.

    Refused with one message when the link is used, revoked, expired or unknown — the four are
    indistinguishable from outside.
    """
    invitation = invitations.usable_invitation(db, token)
    return InvitationPreviewOut(
        email=invitation.email,
        role=invitation.role,
        role_label=role_label(invitation.role),
        site_id=invitation.site_id,
        expires_at=invitation.expires_at,
    )


@invitations_router.post(
    "/accept",
    response_model=InvitationAcceptedOut,
    operation_id="staffInvitationAccept",
)
def accept_staff_invitation(
    body: InvitationAcceptIn, db: DbSession
) -> InvitationAcceptedOut:
    """Accept an invitation: the account is created and holds its role at that clinic.

    No session is issued: the account is made, and the person signs in with it. Deliberate — a flow
    that both creates a credential *and* hands out a session turns one stolen link into a live
    session, and sign-in is where the rate limits and the audit line for "someone signed in" live.
    """
    accepted = invitations.accept_invitation(
        db,
        body.token,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
    )
    email, site_id, role = (
        accepted.user.email,
        accepted.invitation.site_id,
        accepted.invitation.role,
    )
    db.commit()
    return InvitationAcceptedOut(email=email, site_id=site_id, role=role)


# --------------------------------------------------------------------------------------
# Site membership and room assignment (Issue 28)
#
# Both are ``sites.staff`` writes, the same grant that invites and deactivates: deciding who works
# here and which room they work is one job, and it is the clinic manager's.
# --------------------------------------------------------------------------------------


def _audit_assignment(
    db: Session,
    request: Request,
    access: SiteAccess,
    action: AuditAction,
    user_id: str,
    context: str,
) -> None:
    """Record one membership or room change, before the commit, with the clinic on it."""
    bind_request_context(site_id=access.site_id)
    record_audit_event(
        db,
        action=action,
        entity_type=AuditEntityType.USER,
        entity_id=user_id,
        actor=access.user.email,
        actor_id=str(access.user.id),
        ip_address=resolve_client_ip(request),
        context=context,
    )


@router.post(
    "/{site_id}/staff/{user_id}/roles",
    response_model=StaffMemberOut,
    operation_id="staffGrantRoleAtSite",
)
def grant_role_at_site(
    user_id: str,
    body: SiteRoleIn,
    request: Request,
    access: StaffUpdate,
    db: DbSession,
) -> StaffMemberOut:
    """Give somebody a role **at this clinic**. Idempotent; audited.

    Not a way in from outside: the person must already have an account (Issue 22's invitation is
    how one is made), and another clinic's id is a 404 before any of this is considered.
    """
    try:
        assignments.grant_role_at_site(db, access, user_id, body.role)
    except assignments.RoleNotAssignableError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    _audit_assignment(
        db,
        request,
        access,
        AuditAction.UPDATE,
        user_id,
        f"granted the {body.role.value} role at this clinic",
    )
    db.commit()
    return service.get_staff_member(db, user_id, access)


@router.delete(
    "/{site_id}/staff/{user_id}/roles/{role}",
    response_model=StaffMemberOut,
    operation_id="staffRevokeRoleAtSite",
)
def revoke_role_at_site(
    user_id: str,
    role: UserRole,
    request: Request,
    access: StaffUpdate,
    db: DbSession,
) -> StaffMemberOut:
    """Take a role away at this clinic. **Refused if it would leave the clinic without a manager.**

    Takes effect on the person's very next request: which clinics and queues somebody reaches is
    read from these rows on every request and never from their token, so an access token minted a
    minute ago stops reaching this clinic the moment this commits.
    """
    member = service.get_staff_member(db, user_id, access)
    try:
        assignments.revoke_role_at_site(db, access, user_id, role)
    except assignments.LastManagerError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except assignments.NotAtThisClinicError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    _audit_assignment(
        db,
        request,
        access,
        AuditAction.UPDATE,
        user_id,
        f"removed the {role.value} role at this clinic",
    )
    db.commit()
    return StaffMemberOut(
        **{**member.model_dump(), "roles": _roles_now(db, user_id, access)}
    )


@router.get(
    "/{site_id}/staff/{user_id}/queues",
    response_model=RoomAssignmentListOut,
    operation_id="staffListRoomAssignments",
)
def list_room_assignments(
    user_id: str, access: StaffRead, db: DbSession
) -> RoomAssignmentListOut:
    """Which of this clinic's rooms somebody works, the ones they are on first."""
    service.get_staff_member(db, user_id, access)  # 404s for anyone else's id
    return RoomAssignmentListOut(
        site_id=access.site_id,
        user_id=user_id,
        items=[
            RoomAssignmentOut(
                queue_id=row.queue_id,
                queue_name=row.queue_name,
                room_label=row.room_label,
                is_active=row.is_active,
            )
            for row in assignments.room_assignments(db, access, user_id)
        ],
    )


@router.put(
    "/{site_id}/staff/{user_id}/queues",
    response_model=RoomAssignmentListOut,
    operation_id="staffSetRoomAssignments",
)
def set_room_assignments(
    user_id: str,
    body: RoomAssignmentIn,
    request: Request,
    access: StaffUpdate,
    db: DbSession,
) -> RoomAssignmentListOut:
    """Put somebody on exactly these rooms at this clinic; audited.

    The whole set in one request, and a room they come off keeps its row so "who was on Room 2 last
    Tuesday" stays answerable. Takes effect on their next request, for the same reason as above.
    """
    service.get_staff_member(db, user_id, access)
    try:
        rooms = assignments.set_room_assignments(db, access, user_id, body.queue_ids)
    except assignments.NotAtThisClinicError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    on = [row.queue_name for row in rooms if row.is_active]
    _audit_assignment(
        db,
        request,
        access,
        AuditAction.UPDATE,
        user_id,
        f"room assignment: {', '.join(on) if on else 'none'}",
    )
    db.commit()
    return RoomAssignmentListOut(
        site_id=access.site_id,
        user_id=user_id,
        items=[
            RoomAssignmentOut(
                queue_id=row.queue_id,
                queue_name=row.queue_name,
                room_label=row.room_label,
                is_active=row.is_active,
            )
            for row in assignments.room_assignments(db, access, user_id)
        ],
    )


def _roles_now(db: Session, user_id: str, access: SiteAccess) -> list[str]:
    """The roles somebody holds at this clinic *after* a change, without re-running the 404.

    ``get_staff_member`` would 404 for someone whose last role here has just been removed, which is
    correct for a read and wrong for the response to the removal itself: the caller asked what
    happened, and "they now hold nothing here" is the answer.
    """
    from src.core.site_scope import roles_held_at_site

    return roles_held_at_site(db, user_id, access)
