"""Precomputed effective-permission cache: the flattened role closure (Issue #137).

Resolving a role's effective grants means flattening its inheritance closure (Issue #135) and
applying deny-beats-allow (Issue #134) — work that only changes when an **admin edits roles**, a
rare event, not on every request. This table precomputes it: one row per
``(role, resource, effect)`` holding the pooled verb over the role's whole ``role_hierarchy``
closure (max ALLOW verb, min DENY verb). A Postgres **statement-level** trigger rebuilds it whenever
``role_permission`` or ``role_hierarchy`` changes, so the per-request check is a single indexed read
instead of a graph walk.

The **resource-tree** inheritance (parent→child cascade) is deliberately *not* flattened here — it
stays code-driven for now and resolves in :func:`src.core.rbac.effective_max_verb_from_grants`
downstream (the ``resources``/``resource_descendants`` cascade arrives in M25). So feeding either
these cached rows *or* the raw closure rows into that resolver yields the same decision — the pure
Python resolver remains the correctness oracle (parity-tested).

Triggers and the PL/pgSQL rebuild are Postgres-only; under SQLite (tests) the table exists but stays
empty, and the request path falls back to walking the closure in Python.
"""

from sqlalchemy import PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class EffectiveRolePermission(Base):
    """One pooled grant for a role over its inheritance closure: verb + ALLOW/DENY effect."""

    __tablename__ = "effective_role_permissions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "role", "resource", "effect", name="pk_effective_role_permissions"
        ),
    )

    # ``role`` leads the primary key, so the per-role request read is already indexed.
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    verb: Mapped[str] = mapped_column(String(16), nullable=False)
    effect: Mapped[str] = mapped_column(String(16), nullable=False)
