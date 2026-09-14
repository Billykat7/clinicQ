"""HTTP routes for private visit notes (Issue 53): read and add, on a ticket in the caller's rooms.

Both go through :func:`~src.core.site_scope.require_site_access` with ``visits.notes``, which only a
nurse or doctor holds, at the ``own`` tier: the guard narrows the request to the queues they are
assigned to, so another clinic's ticket **and another room's** answer the same 404. A receptionist or
clinic manager is refused with 403 before any ticket is read.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.commons.enums import ActorKind
from src.core.site_scope import SiteAccess, require_site_access
from src.database.session import get_db
from src.modules.queue.lifecycle import Actor
from src.modules.visits import notes
from src.modules.visits.schemas import VisitNoteIn, VisitNoteOut, VisitNotesOut

router = APIRouter(tags=["queue"])

DbSession = Annotated[Session, Depends(get_db)]
NotesRead = Annotated[SiteAccess, Depends(require_site_access("visits.notes", "read"))]
NotesWrite = Annotated[
    SiteAccess, Depends(require_site_access("visits.notes", "update"))
]


@router.get(
    "/sites/{site_id}/tickets/{ticket_id}/notes",
    response_model=VisitNotesOut,
    operation_id="visitNotesForTicket",
    summary="The private notes of a ticket's visit (clinicians only)",
)
def read_notes(ticket_id: str, access: NotesRead, db: DbSession) -> VisitNotesOut:
    """This visit's notes, and earlier visits' at this clinic when the patient agreed to share them."""
    found = notes.read_notes(db, access, ticket_id)
    return VisitNotesOut(
        ticket_id=found.ticket.id,
        visit_id=found.ticket.visit_id,
        notes=[VisitNoteOut.of(note) for note in found.notes],
        previous=(
            None
            if found.previous is None
            else [VisitNoteOut.of(note) for note in found.previous]
        ),
        previous_withheld=found.previous_withheld,
    )


@router.post(
    "/sites/{site_id}/tickets/{ticket_id}/notes",
    response_model=VisitNoteOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="visitNoteAdd",
    summary="Add a private note to a ticket's visit (clinicians only)",
)
def add_note(
    ticket_id: str, payload: VisitNoteIn, access: NotesWrite, db: DbSession
) -> VisitNoteOut:
    """Write a note, attributed to the caller and timestamped; audited without its words."""
    note = notes.add_note(
        db,
        access,
        ticket_id,
        payload.text,
        actor=Actor(
            kind=ActorKind.STAFF, label=access.user.email, user_id=str(access.user.id)
        ),
    )
    db.commit()
    db.refresh(note)
    return VisitNoteOut.of(note)
