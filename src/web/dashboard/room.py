"""What a clinician's room view shows: their queues, the patients with them, and those patients' notes (Issue 53).

A nurse needs one queue, one button and somewhere to write a short note, not the whole clinic. So the
room view reads only the queues the nurse is assigned to (Issue 28), through an access the site guard
would give them for ``visits.notes``: narrowed to those rooms, so a ticket in another room is never
read here, let alone shown.

For each room it reads the patients **with the clinician now** (called, called again or being seen)
from Issue 49's card for that queue, each with its buttons (:mod:`src.web.dashboard.actions`,
Issue 50), this visit's notes and, where the patient agreed, notes from earlier visits
(:func:`src.modules.visits.notes.read_notes`). What the buttons do is the API's: *Call next*, *Start*,
*Done*, *Recall*, *No-show*, *Undo call* and *Transfer* are the queue engine's routes (Issues 41, 45 and
50), and *Add note* is the notes route.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from src.commons.enums import TransferReason
from src.commons.time import now_sast, stored_sast
from src.core.site_scope import SiteAccess
from src.database.models import VisitNote
from src.modules.queue.transfer import TRANSFER_REASON_LABELS
from src.modules.queues.service import list_queues
from src.modules.visits.notes import VisitNotes, read_notes
from src.web.dashboard.board import BoardCard, WithStaffTicket, read_board


@dataclass(frozen=True, slots=True)
class NoteView:
    """A note as the room shows it: the words, the author and when, in Johannesburg time."""

    text: str
    author: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RoomNotes:
    """This visit's notes, and earlier visits' when shared (``None``, with the reason, when not)."""

    notes: tuple[NoteView, ...]
    previous: tuple[NoteView, ...] | None
    previous_withheld: str | None


def _notes_view(found: VisitNotes) -> RoomNotes:
    """Notes read from storage, with their times brought into Johannesburg for display."""

    def view(note: VisitNote) -> NoteView:
        return NoteView(
            text=note.note_text,
            author=note.author,
            created_at=stored_sast(note.created_at),
        )

    return RoomNotes(
        notes=tuple(view(note) for note in found.notes),
        previous=None
        if found.previous is None
        else tuple(view(note) for note in found.previous),
        previous_withheld=found.previous_withheld,
    )


@dataclass(frozen=True, slots=True)
class RoomPatient:
    """A patient with the clinician now: the card's ticket, with its buttons, and its notes."""

    ticket: WithStaffTicket
    notes: RoomNotes


@dataclass(frozen=True, slots=True)
class Room:
    """One of the clinician's queues: its card, and the patients with them in it."""

    card: BoardCard
    patients: tuple[RoomPatient, ...]


@dataclass(frozen=True, slots=True)
class TransferTarget:
    """A queue a patient may be sent on to."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class RoomView:
    """Everything the room page renders."""

    rooms: tuple[Room, ...]
    transfer_targets: tuple[TransferTarget, ...]
    transfer_reasons: tuple[tuple[str, str], ...]
    as_of: datetime


def read_room(
    db: Session, access: SiteAccess, *, moment: datetime | None = None
) -> RoomView:
    """The clinician's rooms, read through ``access`` (which the caller narrowed to their queues)."""
    now = moment or now_sast()
    board = read_board(db, access, only=access.queue_ids, moment=now)
    rooms = tuple(
        Room(
            card=card,
            patients=tuple(
                RoomPatient(
                    ticket=ticket,
                    notes=_notes_view(read_notes(db, access, ticket.id, moment=now)),
                )
                for ticket in card.with_staff_tickets
            ),
        )
        for card in board.cards
    )
    # Where a patient may be sent on to: any open queue at the clinic, not only the clinician's own.
    # The page leaves out the patient's current queue; the API refuses it anyway.
    targets = tuple(
        TransferTarget(id=queue.id, name=queue.name)
        for queue in list_queues(db, access.whole_site(), include_inactive=False).items
    )
    return RoomView(
        rooms=rooms,
        transfer_targets=targets,
        transfer_reasons=tuple(
            (reason.value, TRANSFER_REASON_LABELS[reason]) for reason in TransferReason
        ),
        as_of=now,
    )
