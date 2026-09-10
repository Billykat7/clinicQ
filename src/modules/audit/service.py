"""Audit-trail search and the data-subject hooks that hang off it (Issue #78).

Over the append-only :class:`~src.database.models.audit_event.AuditEvent` table:

* :func:`search_audit_events` — the admin audit search (filter by entity, actor and action),
  newest first, paginated. The router records the search itself as an ``AuditAction.READ`` event,
  so **reading the log is itself audited**.
* :func:`audit_trail_for` — every event about one subject, newest first. The building block a
  data-subject access request (POPIA/GDPR) assembles its answer from.

**Data-subject export and erasure are yours to finish.** The trail half is generic and complete;
the other half is not, because "everything held about this person" and "which of it may be erased
and which must be retained" are policy questions about *your* records. When you add them, keep
them thin and explicit rather than a generic cascade — the line between "personal data to erase"
and "record to retain under a legal obligation" is a decision that should be readable in one
place, not inferred from a relationship graph. :class:`~src.modules.audit.schemas.DataSubjectExport`
and :class:`~src.modules.audit.schemas.ErasureResult` are the shapes to fill.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.commons.enums import AuditAction, AuditEntityType, BoundedContext
from src.commons.schemas import ModuleInfo
from src.core.s3_logging import APP_TIMEZONE
from src.database.models.audit_event import AuditEvent
from src.modules.audit.schemas import AuditEventOut, AuditSearchResult


def get_module_info() -> ModuleInfo:
    """Return module metadata for the audit bounded context."""
    return ModuleInfo(
        context=BoundedContext.AUDIT,
        summary=(
            "Audit: the append-only record-level trail and POPIA data-subject export/erasure."
        ),
    )


def _now() -> datetime:
    """Current time in the app timezone (Africa/Johannesburg)."""
    return datetime.now(APP_TIMEZONE)


def _to_out(event: AuditEvent) -> AuditEventOut:
    """Project an :class:`AuditEvent` row to its wire shape."""
    return AuditEventOut.model_validate(event, from_attributes=True)


def search_audit_events(
    db: Session,
    *,
    entity_type: AuditEntityType | None = None,
    entity_id: str | None = None,
    actor: str | None = None,
    action: AuditAction | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AuditSearchResult:
    """Search the audit trail, newest first, with an exact-match filter and pagination.

    Args:
        db: The session.
        entity_type: Restrict to one entity kind (e.g. all tenant events).
        entity_id: Restrict to one record (requires the record's primary key).
        actor: Restrict to one actor identity (exact match on the stored text).
        action: Restrict to one action kind.
        limit: Page size (1-based count), capped by the router.
        offset: Rows to skip for pagination.

    Returns:
        An :class:`AuditSearchResult` with the page of events and the total match count.
    """
    filters = []
    if entity_type is not None:
        filters.append(AuditEvent.entity_type == entity_type.value)
    if entity_id is not None:
        filters.append(AuditEvent.entity_id == entity_id)
    if actor is not None:
        filters.append(AuditEvent.actor == actor)
    if action is not None:
        filters.append(AuditEvent.action == action.value)

    total = db.execute(
        select(func.count()).select_from(AuditEvent).where(*filters)
    ).scalar_one()
    rows = (
        db.execute(
            select(AuditEvent)
            .where(*filters)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return AuditSearchResult(
        total=total,
        limit=limit,
        offset=offset,
        events=[_to_out(row) for row in rows],
    )


def audit_trail_for(
    db: Session, entity_type: AuditEntityType, entity_id: str
) -> list[AuditEventOut]:
    """Return every audit event about one record, newest first."""
    rows = (
        db.execute(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == entity_type.value,
                AuditEvent.entity_id == entity_id,
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        )
        .scalars()
        .all()
    )
    return [_to_out(row) for row in rows]
