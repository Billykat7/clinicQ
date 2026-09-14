"""A request key: the record that one staff action on the queue has already been done (Issue 50).

*Call next* is pressed hundreds of times a day, often on a tablet with a slow connection. A double tap,
or a browser retrying a request whose answer was lost, must not call **two** patients. So the dashboard
sends an ``Idempotency-Key`` header with each press, and the queue routes record the key here **in the
same transaction** as the move it made:

* the same key again, from the same person, for the same operation, answers with the ticket the first
  request moved, and moves nothing;
* two requests with one key arriving at the same instant cannot both act: the second's insert waits on
  the unique constraint until the first commits, then finds the key taken and replays it;
* a request that failed (nobody waiting, a stale screen) rolled its key back with it, so trying again
  with the same key is a real second attempt.

Keys are per user (``uq_queue_request_key_user_key``) and are only needed for as long as a retry can
arrive, so the hourly sweep deletes rows older than a day.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.commons.ids import new_id
from src.database.models.base import Base

SCHEMA = DbSchema.CLINICQ.value

#: The longest key a client may send; a UUID is 36 characters.
MAX_REQUEST_KEY_LENGTH = 64


class QueueRequestKey(Base):
    """One staff member's key for one queue action, and the ticket that action moved."""

    __tablename__ = "queue_request_key"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_queue_request_key_user_key"),
        # The hourly sweep's read.
        Index("ix_clinicq_queue_request_key_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.user.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(MAX_REQUEST_KEY_LENGTH), nullable=False)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    #: What was asked: ``call_next``, ``transition`` or ``undo_call``.
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    #: What it was asked of: the queue for *Call next*, the ticket otherwise, with the requested status.
    target: Mapped[str] = mapped_column(String(80), nullable=False)
    #: The ticket the action moved; set before the transaction commits.
    ticket_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.ticket.id", ondelete="CASCADE"), nullable=True
    )
    #: Business time, Africa/Johannesburg.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
