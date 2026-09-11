"""Widget model — what a table in this codebase looks like.

Four conventions every model here follows, and the example exists partly to show them:

* a **string UUIDv7 primary key** (:func:`src.commons.ids.new_id`), defaulted in Python, so an id
  exists before the flush and a row can be referenced (in an audit event, say) inside the same
  transaction, and ids sort by creation time;
* the **mixins** carry the columns every table wants — :class:`TimestampMixin` for
  ``created_at``/``modified_at``, :class:`ActiveMixin` for a reversible enable/disable, and
  :class:`SoftDeleteMixin` because an audit trail that references a deleted row must still be able
  to resolve it;
* **indexes are named explicitly** and prefixed with the schema, matching the naming convention in
  :mod:`src.database.models.base` — Alembic autogenerate stays quiet when the names are stable;
* the model is imported by :mod:`src.database.models` — a model absent from that file is invisible
  to ``Base.metadata``, so ``alembic revision --autogenerate`` will silently propose dropping its
  table.
"""

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import ActiveMixin, SoftDeleteMixin, TimestampMixin


class Widget(Base, TimestampMixin, ActiveMixin, SoftDeleteMixin):
    """One widget: the example module's record."""

    __tablename__ = "widget"
    __table_args__ = (
        # The list endpoint reads live rows newest-first; index the pair it filters and orders by.
        Index(
            "ix_clinicq_widget_is_deleted_created_at",
            "is_deleted",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=new_id,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    """What the widget is called. Not unique — two widgets may share a name."""
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Optional free text."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"Widget(id={self.id!r}, name={self.name!r})"
