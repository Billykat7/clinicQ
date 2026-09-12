"""Which rooms a nurse or doctor works (Issue 28).

A nurse signed into Room 2 should see Room 2. **Site** membership is not stored here — it is a role
held *at* a site (``user_roles`` with ``scope_type='site'``, Issue 15), and a parallel
``staff_site_assignments`` table would be a second answer to "who works here" that could disagree
with the one RBAC resolves by. **Room** membership is new, and this is it.

The row carries ``site_id`` as well as ``queue_id``, even though the queue already knows its clinic.
Two reasons, and both are about the tenancy guard rather than about normalisation: it makes every
read of this table go through :func:`~src.core.site_scope.scoped_select` like every other
site-scoped read (``tests/unit/security/test_site_scoped_queries.py`` discovers the model *by* that
column), and it makes "everyone assigned to a room at this clinic" one query rather than a join a
caller could forget to filter.

**This table is the record; the kernel's queue-scoped ``user_roles`` row is its shadow.**
:mod:`src.modules.staff.assignments` writes both in one transaction, because Issue 19's
``permitted_queue_ids`` resolves a nurse's ``own``-tier grant from ``user_roles``. Writing only the
shadow would lose the audit trail and the "who assigned them" the clinic needs; writing only this
table would leave the scope resolver blind. One writer, both rows, and a test that they stay in step.
"""

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value


class StaffQueueAssignment(Base, TimestampMixin):
    """One staff member working one queue at one clinic."""

    __tablename__ = "staff_queue_assignment"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "queue_id", name="uq_staff_queue_assignment_user_queue"
        ),
        Index(
            "ix_clinicq_staff_queue_assignment_site_user",
            "site_id",
            "user_id",
            "is_active",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.user.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    """Cleared rather than deleted, so "who was on Room 2 last Tuesday" stays answerable."""
    assigned_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The manager who made the assignment. No foreign key, so the record survives their removal."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"StaffQueueAssignment(user_id={self.user_id!r}, queue_id={self.queue_id!r}, "
            f"active={self.is_active})"
        )
