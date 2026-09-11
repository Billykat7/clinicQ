"""Request and response models for the staff API (Issues 19, 22)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.commons.enums import UserRole
from src.schemas.auth import NewPassword


class StaffMemberOut(BaseModel):
    """One staff member of a clinic, as a colleague at that clinic sees them."""

    id: str
    email: str
    first_name: str | None
    last_name: str | None
    roles: list[str] = Field(description="The roles this person holds at this clinic.")
    is_active: bool
    last_login: datetime | None


class StaffListOut(BaseModel):
    """The staff of one clinic."""

    site_id: str
    total: int = Field(ge=0)
    items: list[StaffMemberOut]


# --------------------------------------------------------------------------------------
# Invitations (Issue 22)
# --------------------------------------------------------------------------------------


class StaffInviteIn(BaseModel):
    """Invite one person to work at the clinic in the path.

    The clinic is **not** a field: it comes from the URL, which the site guard has already checked
    (Issue 19), so an invitation cannot be aimed at a clinic the inviter never passed the guard for.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(
        description="Where the invitation is sent, and the account it makes."
    )
    role: UserRole = Field(description="The role they will hold at this clinic.")
    phone: str | None = Field(
        default=None,
        min_length=6,
        max_length=32,
        description="Optional: also send the link by SMS, for staff without a work address.",
    )


class StaffInvitationOut(BaseModel):
    """One invitation, as the clinic that issued it sees it. Never the link.

    The link is a credential — whoever holds it sets that account's password — so it is sent to the
    person invited and appears in no response body, not even the inviter's.
    """

    id: str
    email: str
    role: str
    site_id: str
    state: str = Field(description="pending, accepted, revoked or expired.")
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None = None
    invited_by: str


class StaffInvitationListOut(BaseModel):
    """Every invitation this clinic has issued."""

    site_id: str
    total: int = Field(ge=0)
    items: list[StaffInvitationOut]


class InvitationPreviewOut(BaseModel):
    """What the acceptance page may show before anyone has signed in.

    The clinic and the role, so the person can see what they are accepting — and the address it was
    sent to, which they already know, so that it is obvious when a link was forwarded to the wrong
    person. Nothing else: this is an unauthenticated read.
    """

    email: str
    role: str
    role_label: str = Field(
        description='The role in words: "nurse or doctor", not the wire value.'
    )
    site_id: str
    expires_at: datetime


class InvitationAcceptIn(BaseModel):
    """Accept an invitation and choose a password."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, description="The token from the invitation link.")
    password: NewPassword = Field(description="The password for the new account.")
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)


class InvitationAcceptedOut(BaseModel):
    """The account now exists and holds its role at the clinic. No session is issued here."""

    email: str
    site_id: str
    role: str
    detail: str = (
        "Your account is ready. Sign in with your email address and new password."
    )


class StaffActiveIn(BaseModel):
    """Switch a colleague's account on or off."""

    model_config = ConfigDict(extra="forbid")

    is_active: bool = Field(
        description="False deactivates and revokes their live sessions."
    )
