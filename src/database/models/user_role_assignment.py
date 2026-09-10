"""User→role assignments: multi-role, optionally scoped and time-boxed (Issue #136).

A user's role used to be a single string column (``User.role``), applied **everywhere, forever**.
This table lets a user hold **several** roles, each optionally **scoped** to one instance
(``scope_type``/``scope_id`` — e.g. a role that applies only for one property or lease) and/or
**time-boxed** (``expires_at``). RBAC resolution unions a user's *active, in-scope* assignments;
an assignment with ``expires_at`` in the past grants nothing (lazy, query-time — no scheduler).

``User.role`` is retained this milestone as a **compat mirror** of the user's single *unscoped*
assignment (kept in sync by the admin API); its removal is a later cleanup. The idempotent
migration backfills one unscoped, non-expiring row per existing ``User.role`` so effective access
is unchanged on day one.

* ``scope_type``/``scope_id`` are both ``NULL`` for an **unscoped** assignment (applies globally,
  as today). A scoped assignment applies only when the request presents a matching scope; scope
  narrows *which instances* a role applies to and composes with — but is distinct from — the
  ownership gate already enforced in module services.
* The unique constraint keys ``(user_id, role, scope_type, scope_id)`` so the same scoped role is
  not assigned twice; ``user_id`` is indexed for the per-user resolution read. ``granted_at`` /
  ``expires_at`` are business time (Africa/Johannesburg).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class UserRoleAssignment(Base):
    """One role assignment for one user, optionally scoped to an instance and/or time-boxed."""

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role",
            "scope_type",
            "scope_id",
            name="uq_user_roles_user_role_scope",
        ),
        Index("ix_user_roles_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    # NULL scope = unscoped (applies globally, as today). A non-null scope narrows the role to one
    # instance (e.g. scope_type='property', scope_id=<property id>).
    scope_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scope_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The actor who granted this assignment (a user id; no FK so the record survives their deletion).
    granted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # NULL = never expires. A timestamp in the past makes the assignment inactive at query time.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
