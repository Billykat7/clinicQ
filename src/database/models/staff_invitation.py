"""A staff invitation: one row per person invited to work at one clinic (Issue 22).

The row is the authority, not the link. The link is a typed JWT that carries an invitation id and
nothing else, so the role, the clinic and the deadline cannot be claimed by whoever holds it — they
are read from here, at the moment it is used. That is also what makes the link **single use**: a
used invitation has ``accepted_at`` set, and an accepted or revoked invitation is refused however
valid its signature still looks (the pattern the password-reset link uses through the password
fingerprint, Issue 16, applied to a row that exists anyway).

The row is kept after it is accepted, expired or revoked: who invited whom, to what, and when is
part of a clinic's record of its own staff.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin


class StaffInvitation(Base, TimestampMixin):
    """An invitation to join one clinic in one role, valid until it is used, revoked or expires."""

    __tablename__ = "staff_invitation"
    __table_args__ = (
        Index("ix_clinicq_staff_invitation_site", "site_id", "created_at"),
        Index("ix_clinicq_staff_invitation_email", "email"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    """Lower-cased. The account this invitation creates, and the only address it may be sent to."""
    phone_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)
    """Optional second channel for the link: a nurse with a phone and no work address (Issue 17's
    normalisation is used, so one number is one spelling)."""
    site_id: Mapped[str] = mapped_column(String(36), nullable=False)
    """The clinic the role is held at. The invitation can only be issued by someone who passes the
    site guard for it (Issue 19), and acceptance assigns the role *at this site*, never globally."""
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    """One of :class:`~src.commons.enums.UserRole`, and never a role above the inviter's own."""
    invited_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    """The staff member who issued it. ``RESTRICT``: an invitation keeps naming its author even
    after that account is deactivated, which is what makes the trail attributable."""
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """72 hours after it was issued, by default (``STAFF_INVITE_EXPIRE_HOURS``)."""
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """Set the moment the invitation creates an account. Non-NULL means used: never again."""
    accepted_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    """The account it created, so "who invited this person" is answerable from either end."""
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """Set when a manager withdraws an invitation before it is used."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures: the clinic and the role, not the person."""
        return f"StaffInvitation(site_id={self.site_id!r}, role={self.role!r})"
