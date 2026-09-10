"""Permission catalog: the atomic ``(resource, action)`` capability (Issue #139, M25).

``.btk/RBAC/rbac-design.md`` §1–2 makes a permission the pairing of a resource with an action — the
smallest thing enforcement checks. This is the table grants will point at once enforcement moves off
cumulative verbs (Issue #140): a ``role_permission`` will reference a ``permission_id`` instead of a
``(resource, max_verb)`` string pair. Seeded in this revision with one row per current
``(resource, verb)`` pair the cumulative model implies (the full resource x verb cross-product), so
every grant expressible today has a permission to map onto with no change in behaviour.

The ``key`` a human reads (``lease.signature:sign``) is *not* stored — Postgres generated columns
cannot reference other tables (``.btk/RBAC`` §6) — it is composed in app code
(:func:`src.core.rbac.permission_key`) from the joined resource + action keys.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.database.models.base import Base

_SCHEMA = DbSchema.CLINICQ.value


class Permission(Base):
    """One atomic capability: a ``(resource_id, action_id)`` pair, unique per pair."""

    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("resource_id", "action_id", name="uq_permissions_resource_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    # The unique ``(resource_id, action_id)`` index covers resource-led lookups; no separate index.
    resource_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{_SCHEMA}.resources.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{_SCHEMA}.actions.id", ondelete="CASCADE"),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
