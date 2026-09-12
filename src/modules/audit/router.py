"""HTTP routes for the audit trail (Issue #78).

Two reads, one per audience (Issue 20):

* ``GET /audit/info`` — module metadata (unauthenticated, like every other ``/info``).
* ``GET /audit/events`` — **every** clinic's trail, behind a ``business``-tier grant on ``audit``
  (the operator's console), with an optional ``site_id`` filter.
* ``GET /sites/{site_id}/audit/events`` — **one clinic's** trail, through the site guard, so a
  clinic manager reads their own and gets 404 for anyone else's (Issue 19).

**Both searches are themselves audited**: an ``AuditAction.READ`` event is written naming who
searched and with what filter.

Handlers stay thin: search logic lives in :mod:`src.modules.audit.service`, and the meta audit
event for the read is written here where the actor and client IP are in scope.

A data-subject access or erasure route (POPIA/GDPR) belongs here too, gated the same way — see
:mod:`src.modules.audit.service` for why the kernel does not ship one.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from src.api.rbac_deps import CurrentUser, DbSession, require
from src.commons.enums import AuditAction, AuditEntityType, GrantScope
from src.core.audit import record_audit_event
from src.core.client_ip import resolve_client_ip
from src.core.site_scope import SiteAccess, require_site_access
from src.modules.audit import service
from src.modules.audit.schemas import AuditSearchResult

router = APIRouter(prefix="/audit", tags=["audit"])
#: The per-clinic read hangs off ``/sites/{site_id}/`` like every other site-scoped surface, so the
#: site is part of the path the guard reads (Issue 19), not a parameter a caller chooses.
site_router = APIRouter(prefix="/sites", tags=["audit"])

# Issue #152: resolved through the generic ``require`` factory (src.api.rbac_deps) instead of a
# named ``require_logs_read`` function — no separate Audit manifest is needed, this router simply
# enforces on ``logs``, an already-registered manifest.
# Issue #166 (M28): ``business`` tier — this backs a whole-business back-office console,
# and its nav destination declares the same tier, a parity
# ``tests/unit/security/test_nav_enforcement_parity.py`` now asserts. No seeded role loses
# access: every role that holds this grant holds it at ``business`` already.
#: The platform-wide trail: a ``business``-tier grant on ``audit``, which only the operator's role
#: and the kernel administrator hold (Issue 20). A clinic's own people use the per-clinic route.
PlatformAuditRead = Annotated[
    None, Depends(require("audit", "read", scope=GrantScope.BUSINESS))
]

#: One clinic's own trail, through the site guard: 404 for any other clinic (Issue 19).
SiteAuditRead = Annotated[
    SiteAccess, Depends(require_site_access("sites.audit", "read"))
]

# Search page-size ceiling so a single call cannot pull the whole trail.
_MAX_LIMIT = 200


def _actor(current_user: dict[str, Any]) -> tuple[str, str | None]:
    """Return the ``(actor, actor_id)`` for the caller from their token claims.

    The durable actor label is the email (falling back to the subject id); the ``actor_id`` is the
    ``uid`` claim (the ``user.id``, a UUID) which is what ``audit_event.actor_id`` references via a
    foreign key. It is absent for the anonymous stand-in and for tokens minted before ``uid`` existed.
    """
    sub = current_user.get("sub")
    email = current_user.get("email")
    actor = email or sub or "unknown"
    actor_id = current_user.get("uid")
    return actor, actor_id


@router.get("/info", summary="Module metadata", operation_id="auditInfo")
def audit_info() -> dict[str, str]:
    """Return audit module metadata (unauthenticated)."""
    info = service.get_module_info()
    return {"context": info.context.value, "summary": info.summary}


#: The filters both read routes take, so the platform-wide and the per-clinic search cannot drift.
EntityTypeQuery = Annotated[AuditEntityType | None, Query()]
EntityIdQuery = Annotated[str | None, Query(max_length=64)]
ActorQuery = Annotated[str | None, Query(max_length=255)]
ActionQuery = Annotated[AuditAction | None, Query()]
SinceQuery = Annotated[
    datetime | None, Query(description="Events at or after this moment.")
]
UntilQuery = Annotated[
    datetime | None, Query(description="Events at or before this moment.")
]
LimitQuery = Annotated[int, Query(ge=1, le=_MAX_LIMIT)]
OffsetQuery = Annotated[int, Query(ge=0)]


def _audit_the_read(
    db: DbSession,
    request: Request,
    actor: tuple[str, str | None],
    *,
    entity_id: str | None,
    summary: str,
) -> None:
    """Record that someone read the trail: who, with which filter, and from where."""
    actor_label, actor_id = actor
    record_audit_event(
        db,
        action=AuditAction.READ,
        entity_type=AuditEntityType.AUDIT_LOG,
        entity_id=entity_id or "*",
        actor=actor_label,
        actor_id=actor_id,
        ip_address=resolve_client_ip(request),
        context=summary,
    )
    db.commit()


def _summary(**filters: object) -> str:
    """The filter, as one line on the read's own audit row."""
    return " ".join(f"{name}={value}" for name, value in filters.items())


@router.get("/events", response_model=AuditSearchResult, operation_id="auditSearch")
def search_events(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    _authz: PlatformAuditRead,
    entity_type: EntityTypeQuery = None,
    entity_id: EntityIdQuery = None,
    actor: ActorQuery = None,
    action: ActionQuery = None,
    site_id: Annotated[str | None, Query(max_length=36)] = None,
    since: SinceQuery = None,
    until: UntilQuery = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> AuditSearchResult:
    """Search **every** clinic's trail; the search is itself recorded as a READ audit event.

    The platform-wide view, behind a ``business``-tier grant on ``logs`` (the operator's console).
    ``site_id`` narrows it to one clinic without changing who may call it; a clinic's own people
    use ``GET /sites/{site_id}/audit/events`` instead, which is scoped by the site guard.
    """
    result = service.search_audit_events(
        db,
        access=None,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        action=action,
        site_id=site_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    _audit_the_read(
        db,
        request,
        _actor(current_user),
        entity_id=entity_id,
        summary=_summary(
            entity_type=entity_type,
            entity_id=entity_id,
            actor=actor,
            action=action,
            site_id=site_id,
            since=since,
            until=until,
        ),
    )
    return result


@site_router.get(
    "/{site_id}/audit/events",
    response_model=AuditSearchResult,
    operation_id="auditSearchForSite",
)
def search_site_events(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    access: SiteAuditRead,
    entity_type: EntityTypeQuery = None,
    entity_id: EntityIdQuery = None,
    actor: ActorQuery = None,
    action: ActionQuery = None,
    since: SinceQuery = None,
    until: UntilQuery = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> AuditSearchResult:
    """Search **this clinic's** trail: what happened here, and who did it.

    The site guard answers 404 for any other clinic before the search runs (Issue 19), and the
    query is filtered to the clinic in the path, so a manager cannot widen it with a parameter.
    This read is audited too, against this clinic.
    """
    result = service.search_audit_events(
        db,
        access=access,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        action=action,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    _audit_the_read(
        db,
        request,
        _actor(current_user),
        entity_id=entity_id,
        summary=_summary(
            site_id=access.site_id,
            entity_type=entity_type,
            entity_id=entity_id,
            actor=actor,
            action=action,
            since=since,
            until=until,
        ),
    )
    return result
