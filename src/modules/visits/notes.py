"""Private visit notes: write, read for the visit, read earlier visits with consent, purge (Issue 53).

What a note is and how it is stored is :mod:`src.database.models.visit_note`. This module decides the
few things about notes that are this issue's to decide, all on the server:

* **Where.** A note is written on a ticket in a queue the clinician acts in: the request's
  :class:`~src.core.site_scope.SiteAccess` is narrowed to their rooms by the site guard, so another
  room's ticket is the same 404 as another clinic's.
* **When.** On a patient who has been seen or is being seen. A waiting patient has not been seen, and a
  cancelled ticket or a no-show never will be, so a note on either is refused (``409``,
  :data:`NOT_SEEN_CODE`).
* **Who reads what.** The notes of *this* visit, including legs in other queues before a transfer, are
  read by the clinician who holds the ticket now. Notes from the patient's **earlier** visits to this
  clinic are read only if the patient agreed (:attr:`~src.commons.enums.ConsentPurpose.VISIT_NOTE_HISTORY`),
  checked at the read, so a withdrawal hides them on the next one.
* **How long.** Each note carries its ``expires_at`` from ``VISIT_NOTE_RETENTION_DAYS``. Reads never
  return an expired note, and :func:`purge_expired_notes` deletes it. Issue 95's data map will own the
  window; this is the mechanism it configures.

Writing a note is audited without its words: the audit row says a note was added to which ticket, by
whom, and never what it says.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.commons.enums import AuditAction, AuditEntityType, ConsentPurpose, TicketStatus
from src.commons.exceptions import ConflictError
from src.commons.time import now_sast
from src.core.audit import record_audit_event
from src.core.config import Settings, get_settings
from src.core.site_scope import SiteAccess, get_in_site_or_404, scoped_select
from src.database.models import Ticket, VisitNote
from src.modules.patients.consent import has_consent
from src.modules.queue.lifecycle import Actor

#: A note on a patient who has not been seen, and never will be at this ticket.
NOT_SEEN_CODE: Final = "ticket.note.not_seen"
NOT_SEEN: Final = (
    "A visit note is written once the patient has been called, not before."
)
#: The statuses a note is refused on: not yet seen, or never to be seen at this ticket.
_UNSEEN: Final[frozenset[TicketStatus]] = frozenset(
    {TicketStatus.WAITING, TicketStatus.CANCELLED, TicketStatus.NO_SHOW}
)
#: How many earlier-visit notes are shown: the recent history, not the whole record.
PREVIOUS_NOTES_LIMIT: Final = 20
#: What the room view says when earlier notes exist but the patient has not agreed to share them.
HISTORY_NOT_SHARED: Final = "This patient has not agreed to share notes from earlier visits, so only this visit's notes are shown."
#: What it says for a walk-in with no patient record, whose visits cannot be linked.
HISTORY_NO_RECORD: Final = (
    "A walk-in without a patient record has no earlier visits to show."
)


class NoteRefusedError(ConflictError):
    """A note on a ticket that has not been seen: HTTP 409, and nothing is written."""

    def __init__(self) -> None:
        super().__init__(NOT_SEEN, code=NOT_SEEN_CODE)


@dataclass(frozen=True, slots=True)
class VisitNotes:
    """What the room view shows for one ticket: this visit's notes, and earlier ones if shared."""

    ticket: Ticket
    notes: Sequence[VisitNote]
    #: Earlier visits' notes, newest first; ``None`` when they may not be shown.
    previous: Sequence[VisitNote] | None
    #: Why ``previous`` is ``None``, in words for the clinician.
    previous_withheld: str | None


def add_note(
    db: Session,
    access: SiteAccess,
    ticket_id: str,
    text: str,
    *,
    actor: Actor,
    moment: datetime | None = None,
    settings: Settings | None = None,
) -> VisitNote:
    """Write one note on a ticket in the caller's rooms. The caller commits.

    Raises:
        NotFound (404): The ticket is not in a queue the caller acts in at this clinic.
        NoteRefusedError: The patient has not been seen at this ticket.
    """
    moment = moment or now_sast()
    cfg = settings or get_settings()
    ticket = get_in_site_or_404(db, Ticket, ticket_id, access)
    if ticket.status_enum in _UNSEEN:
        raise NoteRefusedError
    note = VisitNote(
        site_id=ticket.site_id,
        queue_id=ticket.queue_id,
        ticket_id=ticket.id,
        visit_id=ticket.visit_id,
        patient_id=ticket.patient_id,
        author_user_id=actor.user_id,
        author=actor.label,
        note_text=text,
        created_at=moment,
        expires_at=moment + timedelta(days=cfg.visit_note_retention_days),
    )
    db.add(note)
    db.flush()
    record_audit_event(
        db,
        action=AuditAction.CREATE,
        entity_type=AuditEntityType.VISIT_NOTE,
        entity_id=note.id,
        actor=actor.label,
        actor_id=actor.user_id,
        actor_role=actor.kind.value,
        site_id=ticket.site_id,
        # Never the words: who wrote a note on which ticket, and when it will be deleted.
        context=f"{ticket.number}: visit note added, kept until {note.expires_at.date().isoformat()}",
    )
    return note


def read_notes(
    db: Session, access: SiteAccess, ticket_id: str, *, moment: datetime | None = None
) -> VisitNotes:
    """This visit's notes for a ticket in the caller's rooms, and earlier visits' if the patient agreed.

    The ticket is found through the room-narrowed access (another room's ticket is 404). Its visit's
    notes are then read clinic-wide, so the notes written in triage travel with a patient transferred
    to the doctor (Issue 45); earlier visits are read only with the patient's consent, at this read.
    """
    moment = moment or now_sast()
    ticket = get_in_site_or_404(db, Ticket, ticket_id, access)
    clinic = access.whole_site()
    notes = (
        db.execute(
            scoped_select(VisitNote, clinic)
            .where(VisitNote.visit_id == ticket.visit_id, VisitNote.expires_at > moment)
            .order_by(VisitNote.created_at)
        )
        .scalars()
        .all()
    )
    if ticket.patient_id is None:
        return VisitNotes(ticket, notes, None, HISTORY_NO_RECORD)
    if not has_consent(db, ticket.patient_id, ConsentPurpose.VISIT_NOTE_HISTORY):
        return VisitNotes(ticket, notes, None, HISTORY_NOT_SHARED)
    previous = (
        db.execute(
            scoped_select(VisitNote, clinic)
            .where(
                VisitNote.patient_id == ticket.patient_id,
                VisitNote.visit_id != ticket.visit_id,
                VisitNote.expires_at > moment,
            )
            .order_by(VisitNote.created_at.desc())
            .limit(PREVIOUS_NOTES_LIMIT)
        )
        .scalars()
        .all()
    )
    return VisitNotes(ticket, notes, previous, None)


def purge_expired_notes(db: Session, *, moment: datetime | None = None) -> int:
    """Delete every note past its ``expires_at``, at every clinic; return how many. The caller commits.

    A system sweep, like the recall timers, so it reads no clinic's access. The rows are deleted, not
    blanked: the words are gone from the table, and the audit rows never held them. One audit row
    records the sweep and the count, never the content.
    """
    moment = moment or now_sast()
    result = db.execute(delete(VisitNote).where(VisitNote.expires_at <= moment))
    purged = int(getattr(result, "rowcount", 0) or 0)
    if purged:
        record_audit_event(
            db,
            action=AuditAction.DELETE,
            entity_type=AuditEntityType.VISIT_NOTE,
            entity_id="retention-sweep",
            actor="system:visit-note-retention",
            context=f"purged {purged} expired visit note(s)",
        )
    return purged
