"""HTTP routes for widgets — the shape every module's router should have.

Each handler does four things, in this order, and nothing else:

1. **authorize** — a ``Depends(require(...))`` on the manifest-declared resource. The verb is the
   one the handler actually exercises, so a reader cannot reach a writer's endpoint;
2. **narrow** — :func:`~src.core.scope.scoped_instance_ids` resolves what this caller may see, once,
   and hands it to the service. The same call in the list and detail handlers is what keeps them
   from drifting apart;
3. **delegate** — the query and the rules live in :mod:`src.modules.widgets.service`;
4. **audit** — a mutation writes an :class:`~src.database.models.audit_event.AuditEvent` naming the
   actor and their IP, then commits. Reads do not.

Register the router in :mod:`src.api.v1.router` and the manifest in
:mod:`src.core.rbac_manifest_registry`; those two lines are the whole wiring.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.rbac_deps import CurrentUser, DbSession, require
from src.commons.enums import AuditAction, AuditEntityType, GrantScope
from src.core.audit import record_audit_event
from src.core.client_ip import resolve_client_ip
from src.core.scope import scoped_instance_ids
from src.modules.widgets import service
from src.modules.widgets.schemas import WidgetIn, WidgetListOut, WidgetOut

router = APIRouter(prefix="/widgets", tags=["widgets"])

WidgetsRead = Annotated[None, Depends(require("widgets", "read"))]
WidgetsCreate = Annotated[None, Depends(require("widgets", "create"))]
WidgetsUpdate = Annotated[None, Depends(require("widgets", "update"))]
WidgetsDelete = Annotated[
    None, Depends(require("widgets", "delete", scope=GrantScope.BUSINESS))
]

_MAX_LIMIT = 200
_NOT_FOUND = "Widget not found"


def _actor(current_user: dict[str, Any]) -> tuple[str, str | None]:
    """Return ``(actor label, actor id)`` from the caller's token claims."""
    actor = current_user.get("email") or current_user.get("sub") or "unknown"
    return actor, current_user.get("uid")


def _audit(
    db: DbSession,
    request: Request,
    current_user: dict[str, Any],
    action: AuditAction,
    widget_id: str,
) -> None:
    """Record one widget mutation. Called before the commit so both land in one transaction."""
    actor, actor_id = _actor(current_user)
    record_audit_event(
        db,
        action=action,
        entity_type=AuditEntityType.WIDGET,
        entity_id=widget_id,
        actor=actor,
        actor_id=actor_id,
        ip_address=resolve_client_ip(request),
    )


@router.get("/info", summary="Module metadata", operation_id="widgetsInfo")
def widgets_info() -> dict[str, str]:
    """Return widgets module metadata (unauthenticated, like every other ``/info``)."""
    info = service.get_module_info()
    return {"context": info.context.value, "summary": info.summary}


@router.get("", response_model=WidgetListOut, operation_id="widgetsList")
def list_widgets(
    db: DbSession,
    current_user: CurrentUser,
    _authz: WidgetsRead,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WidgetListOut:
    """List the widgets this caller's grant reaches, newest first."""
    return service.list_widgets(
        db,
        instance_ids=scoped_instance_ids(db, current_user, "widgets"),
        limit=limit,
        offset=offset,
    )


@router.get("/{widget_id}", response_model=WidgetOut, operation_id="widgetsGet")
def get_widget(
    widget_id: str,
    db: DbSession,
    current_user: CurrentUser,
    _authz: WidgetsRead,
) -> WidgetOut:
    """Return one widget, or 404 if it does not exist *or* is outside the caller's scope."""
    widget = service.get_widget(
        db, widget_id, instance_ids=scoped_instance_ids(db, current_user, "widgets")
    )
    if widget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return WidgetOut.model_validate(widget)


@router.post(
    "",
    response_model=WidgetOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="widgetsCreate",
)
def create_widget(
    payload: WidgetIn,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    _authz: WidgetsCreate,
) -> WidgetOut:
    """Create a widget; records a CREATE audit event."""
    widget = service.create_widget(db, payload)
    _audit(db, request, current_user, AuditAction.CREATE, widget.id)
    db.commit()
    return WidgetOut.model_validate(widget)


@router.put("/{widget_id}", response_model=WidgetOut, operation_id="widgetsUpdate")
def update_widget(
    widget_id: str,
    payload: WidgetIn,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    _authz: WidgetsUpdate,
) -> WidgetOut:
    """Replace a widget's editable fields; records an UPDATE audit event."""
    widget = service.get_widget(
        db, widget_id, instance_ids=scoped_instance_ids(db, current_user, "widgets")
    )
    if widget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    service.update_widget(db, widget, payload)
    _audit(db, request, current_user, AuditAction.UPDATE, widget.id)
    db.commit()
    return WidgetOut.model_validate(widget)


@router.delete(
    "/{widget_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="widgetsDelete",
)
def delete_widget(
    widget_id: str,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    _authz: WidgetsDelete,
) -> None:
    """Soft-delete a widget; records a DELETE audit event."""
    widget = service.get_widget(
        db, widget_id, instance_ids=scoped_instance_ids(db, current_user, "widgets")
    )
    if widget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    service.delete_widget(db, widget)
    _audit(db, request, current_user, AuditAction.DELETE, widget.id)
    db.commit()
