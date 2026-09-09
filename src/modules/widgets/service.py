"""Widget persistence — everything that touches the database, and nothing that touches HTTP.

The router below stays thin because this layer is where the rules live. Two of them are worth
copying:

* **Scope narrowing happens here, not in the router.** :func:`list_widgets` takes an
  ``instance_ids`` filter and applies it; the router resolves it once with
  :func:`~src.core.scope.scoped_instance_ids`. That keeps the list endpoint and the detail endpoint
  narrowing identically — the drift this split exists to prevent.
* **Deletes are soft.** ``is_deleted`` is set, the row stays. An audit trail that references a
  deleted row must still resolve it.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.commons.enums import BoundedContext
from src.commons.schemas import ModuleInfo
from src.database.models.widget import Widget
from src.modules.widgets.schemas import WidgetIn, WidgetListOut, WidgetOut


def get_module_info() -> ModuleInfo:
    """Return this module's metadata for its ``/info`` endpoint."""
    return ModuleInfo(
        context=BoundedContext.WIDGETS,
        summary="The example module: a worked vertical slice, deletable in one command.",
    )


def _live(instance_ids: set[str] | None):
    """The base query every read starts from: live rows, narrowed to ``instance_ids`` if given.

    ``None`` means "apply no narrowing" — a ``business``-tier caller. An **empty set** means no
    rows, which is a real answer and not the same thing; conflating the two is how a scoped caller
    ends up seeing everything.
    """
    stmt = select(Widget).where(Widget.is_deleted.is_(False))
    if instance_ids is not None:
        stmt = stmt.where(Widget.id.in_(instance_ids))
    return stmt


def list_widgets(
    db: Session,
    *,
    instance_ids: set[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> WidgetListOut:
    """Return a page of live widgets, newest first, narrowed to the caller's scope."""
    base = _live(instance_ids)
    total = int(
        db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    )
    rows = (
        db.execute(
            base.order_by(Widget.created_at.desc(), Widget.id)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return WidgetListOut(
        total=total,
        limit=limit,
        offset=offset,
        items=[WidgetOut.model_validate(row) for row in rows],
    )


def get_widget(
    db: Session, widget_id: str, *, instance_ids: set[str] | None = None
) -> Widget | None:
    """Return one live widget the caller's scope admits, or ``None``.

    ``None`` covers both "no such row" and "out of your scope" deliberately: the router turns it
    into the same 404, so a caller cannot use the status code to learn that a row they may not see
    exists.
    """
    return db.execute(
        _live(instance_ids).where(Widget.id == widget_id)
    ).scalar_one_or_none()


def create_widget(db: Session, payload: WidgetIn) -> Widget:
    """Insert a widget. The caller commits."""
    widget = Widget(name=payload.name, description=payload.description)
    db.add(widget)
    db.flush()
    return widget


def update_widget(db: Session, widget: Widget, payload: WidgetIn) -> Widget:
    """Apply an update in place. The caller commits."""
    widget.name = payload.name
    widget.description = payload.description
    db.flush()
    return widget


def delete_widget(db: Session, widget: Widget) -> None:
    """Soft-delete a widget: the row stays, reads stop returning it. The caller commits."""
    widget.is_deleted = True
    db.flush()
