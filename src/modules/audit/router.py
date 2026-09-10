"""HTTP routes for the audit trail (Issue #78).

All routes sit under ``/audit`` and are admin surfaces:

* ``GET /audit/info`` — module metadata (unauthenticated, like every other ``/info``).
* ``GET /audit/events`` — search the append-only audit trail. Gated by the ``logs`` READ verb
  (the same admin audience as the log viewer). **The search is itself audited**: an
  ``AuditAction.READ`` event is written naming who searched and with what filter.

Handlers stay thin: search logic lives in :mod:`src.modules.audit.service`, and the meta audit
event for the read is written here where the actor and client IP are in scope.

A data-subject access or erasure route (POPIA/GDPR) belongs here too, gated the same way — see
:mod:`src.modules.audit.service` for why the kernel does not ship one.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from src.api.rbac_deps import CurrentUser, DbSession, require
from src.commons.enums import AuditAction, AuditEntityType, GrantScope
from src.core.audit import record_audit_event
from src.core.client_ip import resolve_client_ip
from src.modules.audit import service
from src.modules.audit.schemas import AuditSearchResult

router = APIRouter(prefix="/audit", tags=["audit"])

# Issue #152: resolved through the generic ``require`` factory (src.api.rbac_deps) instead of a
# named ``require_logs_read`` function — no separate Audit manifest is needed, this router simply
# enforces on ``logs``, an already-registered manifest.
# Issue #166 (M28): ``business`` tier — this backs a whole-business back-office console,
# and its nav destination declares the same tier, a parity
# ``tests/unit/security/test_nav_enforcement_parity.py`` now asserts. No seeded role loses
# access: every role that holds this grant holds it at ``business`` already.
LogsReadDep = Annotated[
    None, Depends(require("logs", "read", scope=GrantScope.BUSINESS))
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


@router.get("/events", response_model=AuditSearchResult, operation_id="auditSearch")
def search_events(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    _authz: LogsReadDep,
    entity_type: Annotated[AuditEntityType | None, Query()] = None,
    entity_id: Annotated[str | None, Query(max_length=64)] = None,
    actor: Annotated[str | None, Query(max_length=255)] = None,
    action: Annotated[AuditAction | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditSearchResult:
    """Search the audit trail; the search is itself recorded as a READ audit event."""
    result = service.search_audit_events(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        action=action,
        limit=limit,
        offset=offset,
    )
    actor_label, actor_id = _actor(current_user)
    # Reading the log is itself audited: record who searched and the filter they used.
    filter_summary = (
        f"entity_type={entity_type} entity_id={entity_id} actor={actor} action={action}"
    )
    record_audit_event(
        db,
        action=AuditAction.READ,
        entity_type=AuditEntityType.AUDIT_LOG,
        entity_id=entity_id or "*",
        actor=actor_label,
        actor_id=actor_id,
        ip_address=resolve_client_ip(request),
        context=filter_summary,
    )
    db.commit()
    return result
