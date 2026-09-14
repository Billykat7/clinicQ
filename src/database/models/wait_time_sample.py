"""wait_time_sample: one completed visit, as the wait estimator learns from it (Issue 42).

Written once, when a ticket reaches ``done`` (:func:`src.modules.queue.waits.record_visit`, called
by the lifecycle in the same transaction), and never updated. Each row keeps three durations of the
visit, in minutes:

* ``wait_minutes``: joining to being called, what the patient experienced;
* ``service_minutes``: being seen to finishing (``None`` when the visit was never marked in
  progress);
* ``interval_minutes``: this call and the call before it in the same queue and day, **only when the
  patient was already waiting at that earlier call**, so the gap measures how fast the queue moved
  rather than an empty waiting room. This is what the estimator reads
  (:mod:`src.modules.queue.estimate`); ``None`` for the first call of a day, or after an idle spell.

``called_hour`` is the Johannesburg hour of the call, for the hour-of-day weighting. The rows are
the last N visits per queue that the estimate reads (``ix_clinicq_wait_time_sample_queue_recorded``),
and they feed the nightly statistics later (Issue 88). No personal data: a queue, a ticket id and
four numbers.
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.commons.ids import new_id
from src.database.models.base import Base

SCHEMA = DbSchema.CLINICQ.value


class WaitTimeSample(Base):
    """The durations of one completed visit, for the queue's wait estimate."""

    __tablename__ = "wait_time_sample"
    __table_args__ = (
        # The estimate's read: a queue's most recent samples, newest first.
        Index("ix_clinicq_wait_time_sample_queue_recorded", "queue_id", "recorded_at"),
        Index("ix_clinicq_wait_time_sample_site_day", "site_id", "service_day"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"), nullable=False
    )
    ticket_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.ticket.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    """One sample per visit: recording the same ticket twice is refused."""
    service_day: Mapped[date] = mapped_column(Date, nullable=False)
    """The Johannesburg calendar date of the visit."""
    called_hour: Mapped[int] = mapped_column(Integer, nullable=False)
    """The Johannesburg hour (0–23) the patient was called at."""
    wait_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    service_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    interval_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """When the visit finished and the sample was written (Africa/Johannesburg)."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"WaitTimeSample(queue_id={self.queue_id!r}, ticket_id={self.ticket_id!r})"
        )
