"""PermissionAuditLog model: an append-only trail of RBAC-admin grant/revoke changes (Issue #138).

DENY (Issue #134), role inheritance (Issue #135) and scoped assignments (Issue #136) each add a new
*mutable* grant surface whose changes an operator must be able to explain later — auditing a DENY
carve-out is exactly the kind of change that needs a "who granted this, and when". This table records
every mutating RBAC-admin operation: a matrix cell set/revoked, an inheritance edge added/removed, an
assignment added/removed.

Modelled on :class:`~src.database.models.audit_event.AuditEvent` (Issue #78): one immutable row per
change, carrying **who** (``actor`` free-form text — durable across a rename/removal — plus
``actor_id`` as a search convenience), **what** (``action`` GRANT/REVOKE), on **which** grant
(``target_type`` + ``target_id``), the **before/after** JSON of the affected grant, and **when**
(``created_at``, Africa/Johannesburg business time). The row is written in the *same transaction* as
the change, so a rolled-back mutation leaves no orphan row.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from src.database.models.base import Base

# JSONB on Postgres (indexable, compact), plain JSON elsewhere (SQLite tests) — the before/after is
# a small object of the affected grant, so the portable variant is enough for the test dialect.
_StateType = JSON().with_variant(JSONB(), "postgresql")


class PermissionAuditLog(Base):
    """One immutable audit record for an RBAC-admin grant/revoke."""

    __tablename__ = "permission_audit_log"
    __table_args__ = (
        CheckConstraint(
            "action in ('grant', 'revoke')",
            name="ck_permission_audit_log_action",
        ),
        # The trail is browsed by actor (all changes by one person) and by target (the history of
        # one grant), each newest-first.
        Index(
            "ix_clinicq_permission_audit_log_actor",
            "actor",
            "created_at",
        ),
        Index(
            "ix_clinicq_permission_audit_log_target",
            "target_type",
            "target_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    """Human-readable identity of who made the change (email or identifier); ``system`` when not a
    signed-in user. Free-form text, never a FK, so it survives a later user rename or removal."""
    actor_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The acting ``user.id`` when known, kept for search convenience; ``SET NULL`` on user removal."""
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    """``grant`` or ``revoke`` (:class:`~src.commons.enums.PermissionAuditAction`)."""
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    """``role_permission`` / ``role_hierarchy`` / ``user_role``
    (:class:`~src.commons.enums.PermissionAuditTargetType`)."""
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    """Identifier of the affected grant (e.g. ``role:resource``, ``role->inherits_role``, or the
    user/assignment id)."""
    before: Mapped[dict[str, Any] | None] = mapped_column(_StateType, nullable=True)
    """The grant's state before the change (null when it did not exist)."""
    after: Mapped[dict[str, Any] | None] = mapped_column(_StateType, nullable=True)
    """The grant's state after the change (null on a revoke)."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    """When the change was recorded (timezone-aware; Africa/Johannesburg business time)."""
