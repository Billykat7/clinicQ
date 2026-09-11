"""HTTP routes for a clinic's staff (Issue 19).

Both routes hang off ``/sites/{site_id}/``, so the site is part of the request and
:func:`~src.core.site_scope.require_site_access` answers "whose clinic" before anything else: a
Clinic A token asking for Clinic B gets 404, the same answer a site that does not exist gets.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.site_scope import SiteAccess, require_site_access
from src.database.session import get_db
from src.modules.staff import service
from src.modules.staff.schemas import StaffListOut, StaffMemberOut

router = APIRouter(prefix="/sites", tags=["staff"])

DbSession = Annotated[Session, Depends(get_db)]
#: Reading the staff of the clinic in the path: the site gate and the ``sites.staff`` grant.
StaffRead = Annotated[SiteAccess, Depends(require_site_access("sites.staff", "read"))]


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


@router.get(
    "/{site_id}/staff/{user_id}",
    response_model=StaffMemberOut,
    operation_id="staffGetForSite",
)
def get_staff_member(user_id: str, access: StaffRead, db: DbSession) -> StaffMemberOut:
    """One colleague at this clinic, or 404 for anyone else's id."""
    return service.get_staff_member(db, user_id, access)
