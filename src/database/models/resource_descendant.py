"""Resource closure cache: flattened ancestor→descendant pairs (Issue #139, M25).

The opt-in ``applies_to_descendants`` cascade (Issue #140) needs, per grant, every descendant
resource of the granted one — a recursive walk of the ``resources`` tree. ``.btk/RBAC`` §3.1 takes
the "precompute the rare-to-change part" trick used for the role closure (Issue #137) and applies it
to the resource tree: this table holds every ``(ancestor_id, descendant_id)`` pair *including the
self-pair*, so the cascade expansion becomes a plain indexed join instead of a recursive CTE at
request time. The resource catalog is close to static after setup, so the rebuild is an
admin-frequency event.

A Postgres ``refresh_resource_descendants()`` function rebuilds the whole table from a recursive
CTE, fired by a **statement-level** ``AFTER`` trigger on ``resources`` (which also rebuilds the
effective-permission cache, since a tree change can move which sub-resources a cascading grant
reaches). Triggers and PL/pgSQL are Postgres-only; under SQLite (tests) the table exists but is
populated by the Python twin :func:`src.core.rbac.refresh_resource_descendants_py`.
"""

from sqlalchemy import ForeignKey, Index, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.database.models.base import Base

_SCHEMA = DbSchema.CLINICQ.value


class ResourceDescendant(Base):
    """One ``(ancestor, descendant)`` closure pair; the self-pair is included."""

    __tablename__ = "resource_descendants"
    __table_args__ = (
        PrimaryKeyConstraint(
            "ancestor_id", "descendant_id", name="pk_resource_descendants"
        ),
        Index("ix_resource_descendants_descendant_id", "descendant_id"),
    )

    ancestor_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{_SCHEMA}.resources.id", ondelete="CASCADE"),
        nullable=False,
    )
    descendant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{_SCHEMA}.resources.id", ondelete="CASCADE"),
        nullable=False,
    )
