"""site_queue_snapshot: the last known length of each queue (Issue 36).

Rendering a list of twenty clinics must not run twenty ticket counts. This row is the durable half
of the snapshot: one per queue, written by the write-through path when a queue changes (join,
call-next, cancel, no-show) and by the reconciliation sweep. The fast half is Redis
(:mod:`src.modules.queue.snapshot`), which serves most reads; this table is what a cold or flushed
cache falls back to, and what the sweep repairs.

``waiting`` and the wait columns are nullable on purpose: ``None`` is stored as "not measured",
which a surface shows as such, never as zero. ``waiting`` counts today's waiting tickets (Issue 39).
The wait is a **range and a confidence**, never an average (Issue 42): ``wait_low_minutes`` and
``wait_high_minutes`` are the estimate for somebody joining now, ``wait_confidence`` how far to trust
it, and ``wait_approximate`` whether it came from the queue's expected minutes rather than its
recent visits. A single "average wait" column is gone, so no surface can be tempted to show one.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.database.models.base import Base

SCHEMA = DbSchema.CLINICQ.value


class SiteQueueSnapshot(Base):
    """One queue's length and average wait, and when they were taken."""

    __tablename__ = "site_queue_snapshot"
    __table_args__ = (Index("ix_clinicq_site_queue_snapshot_site_id", "site_id"),)

    queue_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"),
        primary_key=True,
    )
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    waiting: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """People waiting when the snapshot was taken. ``None``: not measured."""
    wait_low_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """The low end of the wait for somebody joining now (Issue 42)."""
    wait_high_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """The high end: always greater than the low end when both are set."""
    wait_confidence: Mapped[str | None] = mapped_column(String(8), nullable=True)
    """:class:`~src.commons.enums.EstimateConfidence` of that range."""
    wait_approximate: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    """Whether the range came from the expected minutes, not from recent visits."""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """When the snapshot was taken (Africa/Johannesburg). A read older than the bound recounts."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"SiteQueueSnapshot(queue_id={self.queue_id!r}, waiting={self.waiting!r})"
        )
