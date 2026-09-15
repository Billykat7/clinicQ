"""A clinic's SMS budget over HTTP: what it has sent and spent today, and its own cap (Issue 65).

* ``GET /sites/{site_id}/sms-budget``: the cap, the count, what is left and the spend, for a day. The
  clinic's settings readers (``sites.settings:read``) see it; the M12 reports (Issue 90) read the same
  numbers from :func:`src.modules.notifications.budget.site_budget`.
* ``PUT /sites/{site_id}/sms-budget``: the clinic manager sets the clinic's own daily cap
  (``sites.settings:update``), audited. ``null`` goes back to the platform default.

The platform-wide kill switch is not here: it is an operator's, in the notifications router.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.commons.enums import AuditAction, AuditEntityType
from src.core.audit import record_audit_event
from src.core.site_scope import SiteAccess, require_site_access
from src.database.models.site import Site
from src.database.session import get_db
from src.modules.notifications import budget
from src.modules.notifications.schemas import SmsBudgetIn, SmsBudgetOut

router = APIRouter(tags=["notifications"])

DbSession = Annotated[Session, Depends(get_db)]
SettingsRead = Annotated[
    SiteAccess, Depends(require_site_access("sites.settings", "read"))
]
SettingsUpdate = Annotated[
    SiteAccess, Depends(require_site_access("sites.settings", "update"))
]


def _out(db: Session, access: SiteAccess, day: date | None) -> SmsBudgetOut:
    """The clinic's budget for ``day`` (today when ``None``)."""
    state = budget.site_budget(db, access.site_id, day=day)
    own = db.get(Site, access.site_id)
    return SmsBudgetOut(
        site_id=state.site_id,
        day=state.day,
        cap=state.cap,
        own_cap=own.sms_daily_cap if own is not None else None,
        sent=state.sent,
        remaining=state.remaining,
        spend=state.spend,
        currency=state.currency,
    )


@router.get(
    "/sites/{site_id}/sms-budget",
    response_model=SmsBudgetOut,
    operation_id="notificationsSmsBudget",
)
def get_sms_budget(
    access: SettingsRead,
    db: DbSession,
    day: Annotated[
        date | None, Query(description="A Johannesburg day; today by default.")
    ] = None,
) -> SmsBudgetOut:
    """How many SMS this clinic has sent on a day, what they cost, and how many are left under its cap."""
    return _out(db, access, day)


@router.put(
    "/sites/{site_id}/sms-budget",
    response_model=SmsBudgetOut,
    operation_id="notificationsSetSmsBudget",
)
def set_sms_budget(
    payload: SmsBudgetIn, request: Request, access: SettingsUpdate, db: DbSession
) -> SmsBudgetOut:
    """Set this clinic's own daily SMS cap, or ``null`` for the platform default. Audited."""
    site = db.get(Site, access.site_id)
    before = site.sms_daily_cap if site is not None else None
    budget.set_site_cap(db, access.site_id, payload.daily_cap)
    record_audit_event(
        db,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.SITE,
        entity_id=access.site_id,
        actor=access.user.email,
        actor_id=str(access.user.id),
        site_id=access.site_id,
        diff={"sms_daily_cap": {"before": before, "after": payload.daily_cap}},
        context=f"SMS daily cap: {payload.daily_cap if payload.daily_cap is not None else 'default'}",
    )
    db.commit()
    return _out(db, access, None)
