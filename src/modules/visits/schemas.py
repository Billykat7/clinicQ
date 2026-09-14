"""Request and response shapes for private visit notes (Issue 53). Staff-only, never public."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.commons.time import stored_sast
from src.database.models.visit_note import MAX_VISIT_NOTE_LENGTH, VisitNote


class VisitNoteIn(BaseModel):
    """A note to add. A correction is a new note: notes are never edited."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_VISIT_NOTE_LENGTH)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Trim, and refuse a note that is only spaces."""
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("A note needs some words.")
        return trimmed


class VisitNoteOut(BaseModel):
    """One note, attributed and timestamped."""

    id: str
    ticket_id: str
    visit_id: str
    queue_id: str
    author: str
    text: str
    created_at: datetime
    expires_at: datetime

    @classmethod
    def of(cls, note: VisitNote) -> VisitNoteOut:
        """The wire form of a note."""
        return cls(
            id=note.id,
            ticket_id=note.ticket_id,
            visit_id=note.visit_id,
            queue_id=note.queue_id,
            author=note.author,
            text=note.note_text,
            created_at=stored_sast(note.created_at),
            expires_at=stored_sast(note.expires_at),
        )


class VisitNotesOut(BaseModel):
    """A ticket's notes for its visit, and earlier visits' notes when the patient agreed to share them."""

    ticket_id: str
    visit_id: str
    notes: list[VisitNoteOut]
    previous: list[VisitNoteOut] | None
    """``null`` when earlier notes may not be shown; ``previous_withheld`` says why."""
    previous_withheld: str | None
