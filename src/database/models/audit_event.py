"""AuditEvent model: an append-only, record-level audit trail (Issue #78).

M1 gave request-level logging (the ``SECURITY_AUDIT`` JSON lines keyed by
:class:`~src.commons.enums.SecurityAuditEvent`). This table is the deeper, *record-level*
complement: one immutable row per mutation of a sensitive record (tenant identity, lease,
payment, document), carrying **who** did it, **what** action, on **which** entity, a
before/after **diff**, the caller's **IP** and **when**. Reading the trail is itself recorded
(an ``AuditAction.READ`` row), and the POPIA data-subject export/erasure operations write
``EXPORT`` / ``ERASE`` rows — so the log answers "who looked at this person's data" too.

The row is a **historical fact**: written once and never updated or deleted through the
application. It carries no :class:`~src.database.models.mixins.ActiveMixin` /
:class:`~src.database.models.mixins.SoftDeleteMixin` (an audit record is never toggled or
soft-removed) and only a ``created_at`` (there is no ``modified_at`` because it never changes).
The database enforces append-only independently of the application: migration ``0038`` installs
a trigger that raises on any ``UPDATE`` or ``DELETE`` (see ``docs/CICD/KEY-ROTATION.md`` and the
migration), so "audit records cannot be edited or deleted through the application" holds even
against a bug or a compromised code path.

The ``actor`` is free-form text (an email or identifier), never a foreign key, so the trail
survives a later user rename or removal — the same choice ``accounting_export.exported_by``
makes. ``actor_id`` keeps the ``user.id`` when it is known (``SET NULL`` on user removal) purely
as a convenience for search; the durable identity is the text.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from src.commons.enums import AuditAction, AuditEntityType
from src.database.models.base import Base

# JSONB on Postgres (indexable, compact), plain JSON elsewhere (SQLite tests) — the diff is a
# small object of changed fields, so the portable variant is enough for the test dialect.
_DiffType = JSON().with_variant(JSONB(), "postgresql")


class AuditEvent(Base):
    """One immutable audit record: actor, action, entity, before/after diff, IP and timestamp."""

    __tablename__ = "audit_event"
    __table_args__ = (
        # The audit search filters by entity (all events for one record) and by actor (all
        # events by one person), each newest-first; index both together with the timestamp.
        Index(
            "ix_clinicq_audit_event_entity",
            "entity_type",
            "entity_id",
            "created_at",
        ),
        Index(
            "ix_clinicq_audit_event_actor",
            "actor",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    """Human-readable identity of who performed the action (email or identifier). Free-form
    text, never a FK, so it survives a later user rename or removal. ``system`` when a job (not
    a signed-in user) made the change."""
    actor_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    """The acting ``user.id`` when known, kept for search convenience; ``SET NULL`` on user
    removal because :attr:`actor` is the durable identity."""
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    """What happened; one of :class:`~src.commons.enums.AuditAction`
    (``create``/``update``/``delete``/``read``/``export``/``erase``)."""
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    """The kind of record; one of :class:`~src.commons.enums.AuditEntityType`."""
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    """Primary key of the affected record (or the data-subject id for POPIA meta-events)."""
    diff: Mapped[dict[str, Any] | None] = mapped_column(_DiffType, nullable=True)
    """Changed fields as ``{field: {"before": ..., "after": ...}}``. Sensitive/encrypted field
    values are **redacted** to ``"<redacted>"`` (see :data:`~src.commons.enums.AUDIT_REDACTED_FIELDS`)
    so the trail records *that* a secret changed without becoming a second plaintext copy of it.
    Null for actions with no field delta (a read/export)."""
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    """Caller IP (IPv4 or IPv6, up to 45 chars); null when not resolvable (a background job)."""
    context: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Optional free-text note giving the action context (e.g. the search filter that produced
    a read event, or the retention rule that blocked an erase)."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    """When the event was recorded. There is deliberately no ``modified_at``: the row is
    append-only and never changes."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"AuditEvent(action={self.action!r}, entity_type={self.entity_type!r}, "
            f"entity_id={self.entity_id!r}, actor={self.actor!r})"
        )


# Re-exported so callers reaching for the enums alongside the model find them in one import.
__all__ = ["AuditAction", "AuditEntityType", "AuditEvent"]
