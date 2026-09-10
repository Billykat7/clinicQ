"""Action catalog: the verb vocabulary, separated from resources (Issue #139, M25).

``.btk/RBAC/rbac-design.md`` §1 keeps *actions* (``read``, ``create``, ``sign``, ``approve`` …) in
their own catalog so a capability is an explicit ``(resource, action)`` pair rather than a column on
every resource type — which is what lets a non-cumulative action like ``sign`` exist alongside the
CRUD verbs without duplicating it per resource (the pairing lands in Issue #140). This revision
seeds the catalog from the current cumulative verbs (``read``/``create``/``update``/``delete``) so
nothing changes behaviourally; ``key`` mirrors a ``PermissionVerb`` value while the enum survives as
the generated-constants compat shim.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class Action(Base):
    """One catalog action (verb): the atomic thing a permission lets a role *do*."""

    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    # Stable key (``read``, ``sign``); the value a ``PermissionVerb`` mirrors for the CRUD verbs.
    key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ``true`` protects a built-in verb from deletion in the admin UI (Issue #141); the four seeded
    # CRUD verbs are all system rows.
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
