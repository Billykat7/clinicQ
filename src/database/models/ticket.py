"""Ticket model: one patient's place in one queue on one service day (Issue 39).

The foundation of the queue engine, and the table every later milestone reads: the board (M8), the
dashboard (M7), notifications (M9), the channels (M10) and the reports (M12). What it guarantees is
worth stating before the columns:

* **A number is unique for its queue and service day, and the database says so.**
  ``uq_ticket_queue_id_service_day_sequence`` is a unique constraint on
  ``(queue_id, service_day, sequence)``: two
  receptionists tapping *Add walk-in* in the same millisecond cannot both be issued ``A043``, because
  the second insert is refused by PostgreSQL, not by a check in Python that a race walks straight
  past. The number itself comes from :class:`TicketSequence`, allocated inside the database
  (:func:`src.modules.queue.sequence.allocate_sequence`), never from ``COUNT(*) + 1``.
* **The service day is the Johannesburg date** (:func:`src.commons.time.business_date`), stored in
  its own column rather than derived from ``joined_at`` in every query. Numbering restarts at SAST
  midnight, not at UTC midnight (02:00 in Johannesburg), and the unique constraint and the board's
  index both read the column directly.
* **A walk-in needs no patient.** ``patient_id`` is nullable, so a receptionist can issue a ticket to
  somebody with no phone without inventing a placeholder patient row; ``display_name`` carries what
  the desk was told. ``ck_ticket_patient_or_walk_in`` makes the reverse impossible: a remote join
  (web, USSD, WhatsApp) always belongs to the patient whose number joined.
* **``status`` is vocabulary here, not behaviour.** It is written only by ``transition_ticket()``
  (non-negotiable 2, Issue 41); this model does not know which transitions are legal.
* **``number`` is stored, not recomputed.** It is what was printed on the stub and read out in the
  waiting room, so a queue whose prefix is renamed at noon must not renumber the morning's tickets.
* **``reference_code`` is for people, not machines.** Six characters from an alphabet with no
  ``0``/``O`` and no ``1``/``I``/``L`` (:data:`src.modules.queue.sequence.REFERENCE_ALPHABET`), so it
  survives being read aloud across a reception desk and typed back in. It is unique across the
  platform, and it is **not a secret**: it finds a ticket at the desk and on a kiosk (Issue 70); it
  never authenticates a patient.

Datetimes are timezone-aware; business time is Africa/Johannesburg. ``reason_text`` is personal
information and is redacted in every audit diff (``AUDIT_REDACTED_FIELDS``).
"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema, TicketSource, TicketStatus
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin

SCHEMA = DbSchema.CLINICQ.value

#: The longest reason a patient may give when joining. Short on purpose: it is a hint for triage
#: ("chest pain", "repeat script"), never a clinical history, and it is deleted after the site's
#: retention window (Issue 95).
MAX_REASON_LENGTH = 140
#: Length of a ticket's reference code (see :mod:`src.modules.queue.sequence`).
REFERENCE_CODE_LENGTH = 6


def _in_clause(column: str, values: type[TicketStatus] | type[TicketSource]) -> str:
    """``column IN ('a', 'b')`` from an enum, sorted so the DDL string is stable across runs."""
    members = ", ".join(f"'{member.value}'" for member in sorted(values))
    return f"{column} IN ({members})"


class TicketSequence(Base):
    """The last number issued in one queue on one service day: the counter a ticket number comes from.

    One row per ``(queue_id, service_day)``, created by the first ticket of the day and incremented
    by every one after it with a single ``INSERT … ON CONFLICT DO UPDATE … RETURNING`` statement
    (:func:`src.modules.queue.sequence.allocate_sequence`). That statement takes the row's lock, so
    concurrent joins on one queue queue up behind each other for the length of their transactions
    and each reads the value the previous one committed. A join that rolls back rolls its increment
    back with it, so the day's numbers have no gaps.

    A new service day is a new row, which is the whole of the "reset at midnight" logic: there is no
    job that resets anything, and so no job that can fail to.
    """

    __tablename__ = "ticket_sequence"
    __table_args__ = (CheckConstraint("last_value >= 1", name="last_value_positive"),)

    queue_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"),
        primary_key=True,
    )
    service_day: Mapped[date] = mapped_column(Date, primary_key=True)
    """The Johannesburg calendar date the counter numbers."""
    last_value: Mapped[int] = mapped_column(Integer, nullable=False)
    """The sequence of the most recent ticket issued in this queue on this day."""

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return (
            f"TicketSequence(queue_id={self.queue_id!r}, "
            f"service_day={self.service_day.isoformat()}, last_value={self.last_value})"
        )


class Ticket(Base, TimestampMixin):
    """One patient's place in one queue on one Johannesburg service day."""

    __tablename__ = "ticket"
    __table_args__ = (
        # The invariant this issue exists for: one number per queue per service day, enforced by
        # the database whatever the application does.
        UniqueConstraint(
            "queue_id",
            "service_day",
            "sequence",
            name="uq_ticket_queue_id_service_day_sequence",
        ),
        UniqueConstraint("reference_code", name="uq_ticket_reference_code"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint(_in_clause("status", TicketStatus), name="status"),
        CheckConstraint(_in_clause("source", TicketSource), name="source"),
        # A walk-in may have no patient; a remote join never lacks one (the number that joined).
        CheckConstraint(
            f"patient_id IS NOT NULL OR source = '{TicketSource.WALK_IN.value}'",
            name="patient_or_walk_in",
        ),
        # The board query: one queue's tickets today in a set of statuses, in sequence order. The
        # leading equality columns make it an index range scan; ``EXPLAIN`` is asserted in
        # tests/integration/queue/test_ticket_indexes.py.
        Index(
            "ix_clinicq_ticket_board",
            "queue_id",
            "service_day",
            "status",
            "sequence",
        ),
        # A patient's own tickets ("where am I?", "you already hold A041"), newest day first.
        Index(
            "ix_clinicq_ticket_patient",
            "patient_id",
            "service_day",
            postgresql_where=text("patient_id IS NOT NULL"),
            sqlite_where=text("patient_id IS NOT NULL"),
        ),
        # A clinic's tickets on a day, across its queues: the per-site daily cap and the reports.
        Index("ix_clinicq_ticket_site_day", "site_id", "service_day"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="RESTRICT"), nullable=False
    )
    """The clinic. Denormalised from the queue so the site guard can scope a ticket directly."""
    queue_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.queue.id", ondelete="RESTRICT"),
        nullable=False,
    )
    """The line the ticket is in. ``RESTRICT``: a queue with history is deactivated, never deleted."""
    patient_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """The patient, when known. ``None`` only for a walk-in the desk issued without a number."""
    service_day: Mapped[date] = mapped_column(Date, nullable=False)
    """The Johannesburg calendar date the ticket was issued on; numbering restarts with each one."""
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    """The ticket's position in the day's issuing order, 1-based. Allocated by the database."""
    number: Mapped[str] = mapped_column(String(10), nullable=False)
    """What the patient is called by: the queue's prefix and the sequence, ``A043``."""
    reference_code: Mapped[str] = mapped_column(
        String(REFERENCE_CODE_LENGTH), nullable=False
    )
    """Six unambiguous characters for finding the ticket at the desk. Not a secret."""
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    """:class:`~src.commons.enums.TicketSource`. Recorded for reporting, never used for ordering."""
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    """What the desk or the patient gave as a name. Never on the board without consent (Issue 27)."""
    reason_text: Mapped[str | None] = mapped_column(
        String(MAX_REASON_LENGTH), nullable=True
    )
    """The optional short reason for the visit. Personal information: redacted in audit diffs."""
    comment_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    """Per-visit consent to show the reason on the board (non-negotiable 4). Off unless given."""
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TicketStatus.WAITING.value,
        server_default=TicketStatus.WAITING.value,
    )
    """:class:`~src.commons.enums.TicketStatus`. Written only by ``transition_ticket()`` (Issue 41)."""
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """When the ticket was issued (Africa/Johannesburg)."""
    called_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the patient was first called to a room (Africa/Johannesburg)."""
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the consultation began, the ticket going ``in_progress`` (Africa/Johannesburg)."""
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """When the ticket reached a terminal status (Africa/Johannesburg)."""

    @property
    def status_enum(self) -> TicketStatus:
        """``status`` as its enum member, for code that compares rather than renders."""
        return TicketStatus(self.status)

    @property
    def source_enum(self) -> TicketSource:
        """``source`` as its enum member."""
        return TicketSource(self.source)

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures: never the name or the reason."""
        return (
            f"Ticket(id={self.id!r}, queue_id={self.queue_id!r}, "
            f"service_day={self.service_day.isoformat()}, number={self.number!r})"
        )
