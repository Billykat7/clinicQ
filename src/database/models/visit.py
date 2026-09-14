"""Visit model: one patient's journey through a clinic, across the queues it passes (Issue 45).

A clinic visit is rarely one line: triage, then a consulting room, then the pharmacy window. Each
queue gives the patient a ticket of its own (a number is per queue and day), and the **visit** is
what those tickets share. It is created with the first ticket of the journey
(:func:`src.modules.queue.sequence.issue_ticket`), and every ticket issued by a transfer carries the
same ``visit_id``, with ``transferred_from_id`` pointing at the leg before it.

So the whole journey is a query, and nothing about it is stored twice: the legs are the visit's
tickets in the order they were issued, the visit began when the first one joined (``started_at``),
and it ended when its last leg reached a terminal status other than ``transferred``. Total visit
time is derived from those (:func:`src.modules.queue.transfer.visit_summary`), which is what the
reports read (Issue 90).

``patient_id`` is nullable for the same reason as on a ticket: a walk-in without a phone number has a
visit too. Business time is Africa/Johannesburg.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value


class Visit(Base, TimestampMixin):
    """The tickets of one patient's journey through one clinic on one day."""

    __tablename__ = "visit"
    __table_args__ = (Index("ix_clinicq_visit_site_started", "site_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="RESTRICT"), nullable=False
    )
    patient_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="RESTRICT"),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """When the first ticket of the visit was issued (Africa/Johannesburg)."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"Visit(id={self.id!r}, site_id={self.site_id!r})"
