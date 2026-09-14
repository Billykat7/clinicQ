"""A private visit note: a few lines a nurse or doctor writes in the consulting room (Issue 53).

Health information, so four properties are structural rather than remembered:

* **Encrypted at rest.** ``note_text`` is an :class:`~src.core.encryption.EncryptedString`: Python sees
  the words, the database holds a Fernet token, and a database dump or a backup reveals nothing.
* **Attributed and timestamped.** Every note names its author (the staff member's audit label and id)
  and when it was written, and cannot be edited: a correction is a new note.
* **Short-lived.** ``expires_at`` is set when the note is written, from ``VISIT_NOTE_RETENTION_DAYS``,
  and the nightly sweep (:func:`src.modules.visits.notes.purge_expired_notes`) deletes the row after
  it. Issue 95's data map will own the window; the column is what it will read.
* **Never public.** No board, patient or discovery shape has a field for it, and it is read only
  through ``visits.notes``, which only clinicians hold (``tests/integration/dashboard/test_room_view.py``
  writes a note and inspects every public and patient response for it).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import DbSchema
from src.commons.ids import new_id
from src.core.encryption import EncryptedString
from src.database.models.base import Base

SCHEMA = DbSchema.CLINICQ.value

#: The longest note: a few lines for the next clinician, never a clinical record.
MAX_VISIT_NOTE_LENGTH = 1000


class VisitNote(Base):
    """One note on one ticket of a visit, by one clinician."""

    __tablename__ = "visit_note"
    __table_args__ = (
        # A visit's notes in order, and a patient's earlier notes at a clinic.
        Index("ix_clinicq_visit_note_ticket", "ticket_id", "created_at"),
        Index("ix_clinicq_visit_note_patient", "site_id", "patient_id", "created_at"),
        # The nightly sweep's read.
        Index("ix_clinicq_visit_note_expires", "expires_at"),
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
        ForeignKey(f"{SCHEMA}.ticket.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The visit the ticket belongs to: a transfer keeps it, so the notes follow the patient.
    visit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: The patient the ticket belongs to; ``None`` for a walk-in with no record.
    patient_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    author_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.user.id", ondelete="SET NULL"), nullable=True
    )
    #: The author as the audit log names them, kept if the account is removed.
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    note_text: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    #: Business time, Africa/Johannesburg.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
