"""Staff invitations: a manager onboards their own clinic, and the platform team is not in it.

Issue 22. Four decisions carry this module:

1. **The row is the authority.** The link carries an invitation id and nothing else. Role, clinic
   and deadline are read from the row when the link is used, so a forwarded or edited link cannot
   claim a role it was never issued for.
2. **Single use.** ``accepted_at`` is set in the same transaction that creates the account; an
   invitation that has been accepted, revoked, or has passed its deadline is refused with the same
   message, so a caller cannot tell the three apart. (The signup link this is modelled on is
   *idempotent* rather than single use — it only ever sets a flag. An invitation sets a password
   and grants a role, so it needs the stronger property, and the row is what provides it.)
3. **Nobody invites above themselves.** A clinic manager may invite the roles that work in a clinic;
   only a platform admin may invite a platform admin. Privilege cannot be widened by handing someone
   else a role you do not hold — the check is here, on the server, not on the form.
4. **A role is held at a site.** Acceptance writes one ``user_roles`` row with ``scope_type='site'``
   and the invitation's clinic (Issue 15), never an unscoped assignment — an unscoped row would make
   a receptionist at one clinic a receptionist at all of them.

Deactivation lives here too, because it is the other half of the same job: it flips ``is_active``
**and** revokes every live refresh token, so both the API and the HTML shell stop at the next
request rather than when an access token happens to expire.

Every query is built through :mod:`src.core.site_scope`, with one named exception:
:func:`usable_invitation`, which is reached by someone who is not signed in and therefore has no
site to be scoped by (``tests/unit/security/test_site_scoped_queries.py`` records it by name).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import (
    AssignmentScopeType,
    AuditAction,
    AuditEntityType,
    NotificationTemplate,
    UserRole,
)
from src.commons.phone import normalize_phone
from src.commons.time import APP_TIMEZONE, now_sast
from src.core.audit import record_audit_event
from src.core.config import get_settings
from src.core.rbac import active_role_assignments
from src.core.refresh_token_policy import revoke_families_except
from src.core.security import (
    account_for_email,
    create_staff_invite_token,
    decode_staff_invite_token,
    hash_password,
)
from src.core.site_scope import (
    SiteAccess,
    get_in_site_or_404,
    scoped_select,
    staff_at_site,
    staff_member_in_site_or_404,
)
from src.database.models import StaffInvitation, User, UserRoleAssignment

#: The roles an invitation may carry, and who may issue each one. A clinic manager staffs their own
#: clinic; only a platform admin creates another platform admin. Read as "to invite this role you
#: must hold one of these". ``patient`` and the kernel's ``user``/``admin`` are absent on purpose: a
#: patient is not staff (Issue 17), and the kernel roles are not a clinic's to hand out.
INVITABLE_ROLES: dict[UserRole, frozenset[UserRole]] = {
    UserRole.RECEPTIONIST: frozenset(
        {UserRole.CLINIC_MANAGER, UserRole.PLATFORM_ADMIN}
    ),
    UserRole.NURSE_DOCTOR: frozenset(
        {UserRole.CLINIC_MANAGER, UserRole.PLATFORM_ADMIN}
    ),
    UserRole.CLINIC_MANAGER: frozenset(
        {UserRole.CLINIC_MANAGER, UserRole.PLATFORM_ADMIN}
    ),
    UserRole.PLATFORM_ADMIN: frozenset({UserRole.PLATFORM_ADMIN}),
}

#: One message for "no such invitation", "already used", "revoked" and "expired". A link that is no
#: longer good tells its holder only that, which is all they can act on.
NOT_USABLE = "This invitation link is no longer valid."


class InvitationError(HTTPException):
    """A refusal a caller can act on, with the status the route returns."""

    def __init__(self, status_code: int, detail: str) -> None:
        """Build the refusal."""
        super().__init__(status_code=status_code, detail=detail)


@dataclass(frozen=True, slots=True)
class IssuedInvitation:
    """An invitation and the link issued for it. The link never reaches a response body."""

    invitation: StaffInvitation
    link: str


@dataclass(frozen=True, slots=True)
class Acceptance:
    """The account an invitation made (or found), and the invitation it closed."""

    user: User
    invitation: StaffInvitation


def _now() -> datetime:
    """Now, in Africa/Johannesburg: "72 hours" is a business deadline, not a protocol field."""
    return now_sast()


def _aware(moment: datetime) -> datetime:
    """A stored timestamp as an aware one (SQLite hands back naive datetimes, in business time)."""
    return moment if moment.tzinfo else moment.replace(tzinfo=APP_TIMEZONE)


def roles_held_by(db: Session, user_id: str) -> frozenset[UserRole]:
    """Every role ``user_id`` holds anywhere, as an enum set (unknown values grant nothing).

    Deliberately *not* scoped to the clinic: "may you hand out this role" is a question about the
    inviter, and a platform admin holds their role without being assigned to any one clinic.
    """
    held: set[UserRole] = set()
    for role, _scope_type, _scope_id in active_role_assignments(db, user_id):
        try:
            held.add(UserRole(role))
        except ValueError:  # a role no longer in the vocabulary
            continue
    return frozenset(held)


def may_invite(inviter_roles: frozenset[UserRole], role: UserRole) -> bool:
    """Whether someone holding ``inviter_roles`` may hand out ``role``."""
    return bool(INVITABLE_ROLES.get(role, frozenset()) & inviter_roles)


def invite_staff(
    db: Session,
    access: SiteAccess,
    *,
    email: str,
    role: UserRole,
    phone: str | None = None,
    base_url: str,
) -> IssuedInvitation:
    """Invite one person to work at ``access.site_id`` in ``role``. The caller commits.

    The site guard has already answered "whose clinic" (Issue 19), so the clinic is the one the
    inviter passed it for and cannot be another. What is left is the two things the guard cannot
    know: whether the inviter may hand out *this* role, and whether this person is already here.
    """
    if role not in INVITABLE_ROLES:
        raise InvitationError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{role.value} is not a role a clinic invites people into.",
        )
    if not may_invite(roles_held_by(db, str(access.user.id)), role):
        raise InvitationError(
            status.HTTP_403_FORBIDDEN,
            "You cannot invite someone into a role you do not hold yourself.",
        )

    address = email.strip().lower()
    already_here = db.execute(
        staff_at_site(access).where(User.email == address)
    ).scalar_one_or_none()
    if already_here is not None:
        raise InvitationError(
            status.HTTP_409_CONFLICT, "That person already works at this clinic."
        )

    settings = get_settings()
    invitation = StaffInvitation(
        email=address,
        phone_e164=normalize_phone(phone) if phone else None,
        site_id=access.site_id,
        role=role.value,
        invited_by=str(access.user.id),
        expires_at=_now() + timedelta(hours=settings.staff_invite_expire_hours),
    )
    db.add(invitation)
    db.flush()

    link = (
        f"{base_url.rstrip('/')}/invite"
        f"?token={create_staff_invite_token(str(invitation.id), expires_at=invitation.expires_at)}"
    )
    record_audit_event(
        db,
        action=AuditAction.CREATE,
        entity_type=AuditEntityType.STAFF_INVITATION,
        entity_id=str(invitation.id),
        actor=access.user.email,
        actor_id=str(access.user.id),
        site_id=access.site_id,
        # The role and the address it went to — never the link, which is a credential.
        context=f"invited {address} as {role.value}",
    )
    return IssuedInvitation(invitation=invitation, link=link)


def deliver_invitation(db: Session, issued: IssuedInvitation) -> None:
    """Send the link to the person invited: by email, and by SMS when a number was given.

    Delivery is best effort and never undoes the invitation — the row exists, and a manager can
    revoke it and invite again. The SMS goes through the notification service like every other
    message, so it is on the ledger with the link itself withheld (Issue 17's rule for a message
    that *is* a secret).
    """
    from src.core.email_send import send_staff_invitation_email

    invitation = issued.invitation
    hours = get_settings().staff_invite_expire_hours
    send_staff_invitation_email(
        to_email=invitation.email,
        invite_link=issued.link,
        role=invitation.role,
        expire_hours=hours,
    )
    if invitation.phone_e164:
        from src.modules.notifications import service as notification_service

        notification_service.send_sms(
            db,
            to=invitation.phone_e164,
            template=NotificationTemplate.STAFF_INVITATION,
            context={"link": issued.link, "hours": hours},
        )


def usable_invitation(db: Session, token: str) -> StaffInvitation:
    """The invitation a link names, if it may still be used; otherwise 400 with one message.

    The one query in this module that is not site-scoped, and the reason is structural: whoever
    opens an invitation link is not signed in, so there is no clinic to scope by yet. The row's own
    ``site_id`` is what acceptance then writes. Every reason a link is no longer good — never
    existed, already accepted, revoked, past its deadline — answers identically, so the link cannot
    be used to learn anything. The deadline is checked **against the row**, not only against the
    JWT's ``exp``, so it holds even if a token were minted with a longer life.
    """
    invitation_id = decode_staff_invite_token(token)
    invitation = (
        db.execute(
            select(StaffInvitation).where(StaffInvitation.id == invitation_id)
        ).scalar_one_or_none()
        if invitation_id
        else None
    )
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or _aware(invitation.expires_at) <= _now()
    ):
        raise InvitationError(status.HTTP_400_BAD_REQUEST, NOT_USABLE)
    return invitation


def accept_invitation(
    db: Session,
    token: str,
    *,
    password: str,
    first_name: str | None = None,
    last_name: str | None = None,
) -> Acceptance:
    """Turn a valid invitation into an account holding its role at its clinic. The caller commits.

    One transaction, so the invitation is closed by the same commit that creates the account: there
    is no window in which the link has made an account and is still usable.

    An address that already has an account — someone who works at another clinic — keeps their own
    password and gains only the role at this clinic. The password in the request is ignored rather
    than applied, because an invitation is not a way to set someone else's password.
    """
    invitation = usable_invitation(db, token)
    user = account_for_email(db, invitation.email)
    created = user is None
    if user is None:
        user = User(
            email=invitation.email,
            password=hash_password(password),
            first_name=first_name,
            last_name=last_name,
            is_verified=True,
            role=invitation.role,
        )
        db.add(user)
        db.flush()
    db.add(
        UserRoleAssignment(
            user_id=str(user.id),
            role=invitation.role,
            scope_type=AssignmentScopeType.SITE.value,
            scope_id=invitation.site_id,
        )
    )
    invitation.accepted_at = _now()
    invitation.accepted_user_id = str(user.id)
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.STAFF_INVITATION,
        entity_id=str(invitation.id),
        actor=user.email,
        actor_id=str(user.id),
        site_id=invitation.site_id,
        context=(
            f"accepted as {invitation.role}"
            f" ({'new account' if created else 'existing account'})"
        ),
    )
    db.flush()
    return Acceptance(user=user, invitation=invitation)


def revoke_invitation(
    db: Session, access: SiteAccess, invitation_id: str
) -> StaffInvitation:
    """Withdraw an invitation that has not been used yet. The caller commits.

    Through :func:`~src.core.site_scope.get_in_site_or_404`, so another clinic's invitation id is a
    404 rather than a refusal that confirms it exists.
    """
    invitation = get_in_site_or_404(db, StaffInvitation, invitation_id, access)
    if invitation.accepted_at is not None:
        raise InvitationError(
            status.HTTP_409_CONFLICT,
            "That invitation has been accepted; deactivate the account instead.",
        )
    invitation.revoked_at = _now()
    record_audit_event(
        db,
        action=AuditAction.DELETE,
        entity_type=AuditEntityType.STAFF_INVITATION,
        entity_id=str(invitation.id),
        actor=access.user.email,
        actor_id=str(access.user.id),
        site_id=access.site_id,
        context=f"revoked an invitation for {invitation.role}",
    )
    db.flush()
    return invitation


def list_invitations(db: Session, access: SiteAccess) -> list[StaffInvitation]:
    """Every invitation issued for this clinic, newest first — used, revoked and live alike."""
    return list(
        db.execute(
            scoped_select(StaffInvitation, access).order_by(
                StaffInvitation.created_at.desc()
            )
        )
        .scalars()
        .all()
    )


def invitation_state(
    invitation: StaffInvitation, *, now: datetime | None = None
) -> str:
    """One word for where an invitation stands, derived rather than stored.

    Derived, so there is no status column to fall out of step with the timestamps that decide it.
    """
    moment = now or _now()
    if invitation.accepted_at is not None:
        return "accepted"
    if invitation.revoked_at is not None:
        return "revoked"
    if _aware(invitation.expires_at) <= moment:
        return "expired"
    return "pending"


def set_staff_active(
    db: Session, access: SiteAccess, user_id: str, *, active: bool
) -> User:
    """Deactivate or reactivate a colleague at this clinic. The caller commits.

    Deactivation closes both doors at once:

    * ``is_active`` false makes :func:`src.core.security.find_active_user` refuse the **next**
      request carrying a still-valid access token (Issue 15), and
    * every live refresh token is revoked, so no new access token can be minted and the HTML shell,
      which resolves a visitor from the refresh cookie, stops rendering too.

    Nobody may switch off their own account: an administrator who locks themselves out of their own
    clinic needs the platform team to get back in, which is the loop this issue exists to remove.
    """
    if str(access.user.id) == user_id:
        raise InvitationError(
            status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account."
        )
    member = staff_member_in_site_or_404(db, user_id, access)
    if member.is_active == active:
        return member
    member.is_active = active
    if not active:
        revoke_families_except(db, user_id, None)
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.USER,
        entity_id=user_id,
        actor=access.user.email,
        actor_id=str(access.user.id),
        site_id=access.site_id,
        diff={"is_active": {"before": not active, "after": active}},
        context="reactivated" if active else "deactivated; live sessions revoked",
    )
    db.flush()
    return member
