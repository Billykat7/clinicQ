"""Staff reads, always through the site guard (Issue 19).

Every query here starts at :func:`src.core.site_scope.staff_at_site`, so the site filter is applied
in one place and a service cannot widen it. ``tests/unit/security/test_site_scoped_queries.py``
fails if this module ever builds its own ``select(User)``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.commons.enums import BoundedContext
from src.commons.schemas import ModuleInfo
from src.core.site_scope import (
    SiteAccess,
    roles_held_at_site,
    staff_at_site,
    staff_member_in_site_or_404,
)
from src.database.models import User
from src.modules.staff.schemas import StaffListOut, StaffMemberOut


def get_module_info() -> ModuleInfo:
    """Return this module's metadata for its ``/info`` endpoint."""
    return ModuleInfo(
        context=BoundedContext.STAFF,
        summary="Who works at a clinic: read through the site guard, never across clinics.",
    )


def _to_out(db: Session, member: User, access: SiteAccess) -> StaffMemberOut:
    """One staff member, with the roles they hold at this clinic and nothing from another."""
    return StaffMemberOut(
        id=member.id,
        email=member.email,
        first_name=member.first_name,
        last_name=member.last_name,
        roles=roles_held_at_site(db, member.id, access),
        is_active=member.is_active,
        last_login=member.last_login,
    )


def list_staff(db: Session, access: SiteAccess) -> StaffListOut:
    """Everyone holding a role at the request's clinic, by email."""
    members = list(db.execute(staff_at_site(access).order_by(User.email)).scalars())
    return StaffListOut(
        site_id=access.site_id,
        total=len(members),
        items=[_to_out(db, member, access) for member in members],
    )


def get_staff_member(db: Session, user_id: str, access: SiteAccess) -> StaffMemberOut:
    """One staff member of the request's clinic, or 404 (another clinic's id included)."""
    return _to_out(db, staff_member_in_site_or_404(db, user_id, access), access)
