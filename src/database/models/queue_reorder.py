"""queue_reorder: the record of every priority override (Issue 46).

When staff move a patient forward for clinical priority, two rows are written in the same
transaction, and each has its own job:

* this row is the **queue-specific detail**: which ticket, in which queue, who moved it, the reason
  code, an optional short note, and the position before and after. It is what the reorder trail on
  the dashboard (Issue 52) and the override counts in the reports (Issue 90) read;
* the **audit row** (Issue 20) is the **proof**: append-only by database trigger, with the actor
  and the request it came from.

``reason_code`` is a :class:`~src.commons.enums.PriorityReason`, never free text, and a check
constraint holds the vocabulary. ``note`` is optional and short; it is redacted in the audit diff like
every other free text about a patient. The positions are the ticket's place among the waiting
tickets at the moment of the override, 1 being next to be called: a record of what happened, not a
position anyone reads back to order the queue (that is ``ticket.order_key``).

The staff member is kept twice, as a name and as an id: the name is the durable identity on the
record, and the id, which may be cleared if the account is removed, is for search. Business time is
Africa/Johannesburg. Nothing here is ever shown on the public board.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema, PriorityReason
from src.commons.ids import new_id
from src.database.models.base import Base

SCHEMA = DbSchema.CLINICQ.value

#: The longest note an override may carry: a few words beside the reason code, never a history.
MAX_REORDER_NOTE_LENGTH = 140


class QueueReorder(Base):
    """One priority override: a ticket moved forward, by whom, why, and from which place to which."""

    __tablename__ = "queue_reorder"
    __table_args__ = (
        CheckConstraint(
            "reason_code IN ("
            + ", ".join(f"'{reason.value}'" for reason in sorted(PriorityReason))
            + ")",
            name="reason_code",
        ),
        CheckConstraint(
            "position_before >= 1 AND position_after >= 1 AND position_after < position_before",
            name="moves_forward",
        ),
        # The clinic's trail for a day, newest first, and a staff member's overrides.
        Index("ix_clinicq_queue_reorder_site_created", "site_id", "created_at"),
        Index(
            "ix_clinicq_queue_reorder_staff", "site_id", "staff_user_id", "created_at"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="RESTRICT"), nullable=False
    )
    queue_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.queue.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ticket_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.ticket.id", ondelete="RESTRICT"),
        nullable=False,
    )
    staff_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.user.id", ondelete="SET NULL"), nullable=True
    )
    """Who made the override, for search. Cleared if the account is ever removed."""
    staff: Mapped[str] = mapped_column(String(255), nullable=False)
    """Who made the override, as they signed in: the durable identity on the record."""
    reason_code: Mapped[str] = mapped_column(String(24), nullable=False)
    """:class:`~src.commons.enums.PriorityReason`."""
    note: Mapped[str | None] = mapped_column(
        String(MAX_REORDER_NOTE_LENGTH), nullable=True
    )
    """A few optional words beside the reason. Redacted in the audit diff."""
    position_before: Mapped[int] = mapped_column(Integer, nullable=False)
    position_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    """When the override was made (Africa/Johannesburg)."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures: never the note."""
        return (
            f"QueueReorder(ticket_id={self.ticket_id!r}, {self.position_before}->"
            f"{self.position_after}, reason={self.reason_code!r})"
        )
