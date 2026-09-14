"""The waiting-room board's one privacy projection (Issue 58, non-negotiable 4).

A board is a public screen, so what it may say about a person is decided **here, on the server,
before anything is serialised**, and in one place:

* :func:`project_board` is the only function that reads a clinic's tickets for a board. It reads
  them through :func:`~src.core.site_scope.displayed_select`, which only this module may call, and
  turns every ticket it will show into a :class:`BoardTicket` through
  :func:`~src.modules.patients.consent.board_projection`, the rule that asks the site's display mode
  and the patient's consent (:func:`~src.modules.patients.consent.has_consent`, read afresh on every
  call, so a withdrawal applies on the next update).
* What it returns, :class:`BoardState`, is plain data with no patient, ticket or clinic object
  inside it. :meth:`BoardState.payload` is the JSON every board response carries: the page, its JSON
  endpoint and its live stream. **A name or reason the mode forbids is not in the payload at all**:
  under ``number_only`` there is no ``name`` or ``comment`` key anywhere, and
  :func:`ensure_payload_private` refuses to hand over a payload that has one, so a future bug in the
  projection fails loudly instead of leaking quietly.
* :func:`ensure_projected` is what a template or a stream calls on everything it is given: anything
  that is not the projection's own data (a ``Ticket``, a ``Patient``, a ``Site``, any other object)
  raises :class:`UnprojectedBoardDataError` before a byte is rendered.

**What a comment needs.** The reason for a visit is health information, so it appears only under
``full``, only beside a name the patient agreed to show, only when the clinic switched reasons on
(``display_show_comment``), and only with **both** the patient's standing
:attr:`~src.commons.enums.ConsentPurpose.DISPLAY_COMMENT` and this visit's own
``ticket.comment_consent``. Never under ``number_only`` or ``name_lite``.

**Who is looking.** The site's mode is what its **own screen** may show. A board opened from an
address anyone can type is capped at numbers only (:class:`~src.modules.display.enums.BoardViewer`),
so a clinic that shows first names in its waiting room is not showing them to the internet.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.commons.enums import BoardLanguage, DisplayMode, TicketStatus
from src.commons.time import business_date, now_sast, stored_sast
from src.core.site_scope import displayed_select, publicly_visible_site_clauses
from src.database.models import Patient, Queue, Site, Ticket
from src.modules.display.enums import BoardPersonalField, BoardViewer
from src.modules.patients.consent import board_projection
from src.modules.queue.tickets import CALL_ORDER

#: How many tickets a queue's "now serving" panel lists: the most recent calls, newest first. A
#: multi-room queue (Issue 25) calls several at once; more than three would push the numbers down.
NOW_SERVING_LIMIT: Final = 3
#: How many waiting tickets "up next" lists, in call order (the product brief asks for three to five).
UP_NEXT_LIMIT: Final = 5
#: The statuses a board shows as being served: called, called again, or with a clinician.
NOW_SERVING_STATUSES: Final[frozenset[TicketStatus]] = frozenset(
    {TicketStatus.CALLED, TicketStatus.RECALLED, TicketStatus.IN_PROGRESS}
)
#: Everything a board reads: the served statuses and the waiting line.
_BOARD_STATUSES: Final = sorted(
    status.value for status in NOW_SERVING_STATUSES | {TicketStatus.WAITING}
)
#: The personal keys each mode may carry. Anything outside its set is a leak (see
#: :func:`ensure_payload_private`).
PERSONAL_FIELDS_ALLOWED: Final[Mapping[DisplayMode, frozenset[BoardPersonalField]]] = {
    DisplayMode.NUMBER_ONLY: frozenset(),
    DisplayMode.NAME_LITE: frozenset({BoardPersonalField.NAME}),
    DisplayMode.FULL: frozenset({BoardPersonalField.NAME, BoardPersonalField.COMMENT}),
}


class UnprojectedBoardDataError(TypeError):
    """A board template or stream was handed something other than the privacy projection."""


class BoardPrivacyError(RuntimeError):
    """A board payload carries a personal field its display mode forbids. Never sent."""


# --------------------------------------------------------------------------------------
# What a board may hold
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BoardTicket:
    """One ticket as a board may show it: a number, and only what the projection allowed.

    Built only by :func:`project_board` (``tests/unit/display/test_board_privacy.py`` fails the
    build on a construction anywhere else), from a
    :class:`~src.modules.patients.consent.BoardEntry`, so ``name`` and ``comment`` are ``None``
    unless the mode and the patient's consent permit them.
    """

    number: str
    status: TicketStatus
    #: When the ticket was called, or called again (Africa/Johannesburg); ``None`` while waiting.
    called_at: datetime | None = None
    name: str | None = None
    comment: str | None = None

    def payload(self) -> dict[str, Any]:
        """The ticket's JSON. A personal key is **present only when it has a value**."""
        body: dict[str, Any] = {"number": self.number, "status": self.status.value}
        if self.called_at is not None:
            body["called_at"] = self.called_at.isoformat()
        if self.name is not None:
            body[BoardPersonalField.NAME.value] = self.name
        if self.comment is not None:
            body[BoardPersonalField.COMMENT.value] = self.comment
        return body


@dataclass(frozen=True, slots=True)
class BoardQueue:
    """One queue's panel: who is being served, who is next, and how many are waiting."""

    id: str
    #: The queue's name ("Triage"). Called ``label`` on the wire, so ``name`` is only ever a person's.
    label: str
    #: Where to go when called ("Room 2"); ``None`` when the queue has no room.
    room: str | None
    now_serving: tuple[BoardTicket, ...]
    up_next: tuple[BoardTicket, ...]
    waiting: int

    def payload(self) -> dict[str, Any]:
        """The panel's JSON."""
        return {
            "id": self.id,
            "label": self.label,
            "room": self.room,
            "now_serving": [ticket.payload() for ticket in self.now_serving],
            "up_next": [ticket.payload() for ticket in self.up_next],
            "waiting": self.waiting,
        }


@dataclass(frozen=True, slots=True)
class BoardState:
    """Everything a clinic's board shows at one moment, already through the privacy rule."""

    site_id: str
    clinic_name: str
    #: The mode this response was projected under: the site's, or ``number_only`` for an anonymous
    #: viewer. Never wider than the site's.
    display_mode: DisplayMode
    language: BoardLanguage
    announce_audio: bool
    as_of: datetime
    queues: tuple[BoardQueue, ...]

    def payload(self) -> dict[str, Any]:
        """The JSON every board response carries, checked for personal fields before it is returned.

        Raises:
            BoardPrivacyError: A personal key the mode forbids is present (a projection bug).
        """
        body = {
            "site_id": self.site_id,
            "clinic_name": self.clinic_name,
            "display_mode": self.display_mode.value,
            "language": self.language.value,
            "announce_audio": self.announce_audio,
            "as_of": self.as_of.isoformat(),
            "queues": [queue.payload() for queue in self.queues],
        }
        ensure_payload_private(body, self.display_mode)
        return body


# --------------------------------------------------------------------------------------
# The guards
# --------------------------------------------------------------------------------------

#: Values a board context may carry besides the projection's own dataclasses: text, numbers, times
#: and enum members. No object with attributes a template could walk to a patient.
_ALLOWED_TYPES: Final[tuple[type, ...]] = (
    str,
    int,
    float,
    bool,
    type(None),
    datetime,
    date,
    Enum,
    BoardState,
    BoardQueue,
    BoardTicket,
)
#: Every personal key, as it appears on the wire.
_PERSONAL_KEYS: Final = frozenset(field.value for field in BoardPersonalField)


def ensure_projected(value: object, *, path: str = "board") -> None:
    """Refuse anything a board is handed that is not the privacy projection's output.

    An allowlist, not a blocklist: the projection's dataclasses, plain values, and mappings and
    sequences of those pass; a ``Ticket``, a ``Patient``, a ``Site``, a query result or any other
    object raises, naming where it was found. A template or a stream calls this on its whole context
    before rendering, so raw ticket data cannot reach one by mistake.

    Raises:
        UnprojectedBoardDataError: ``value`` holds something other than projected or plain data.
    """
    if isinstance(value, _ALLOWED_TYPES):
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            ensure_projected(item, path=f"{path}[{key!r}]")
        return
    if isinstance(value, Sequence | set | frozenset):
        for index, item in enumerate(value):
            ensure_projected(item, path=f"{path}[{index}]")
        return
    raise UnprojectedBoardDataError(
        f"{path} is a {type(value).__name__}: a board may only be given the privacy projection "
        "(src.modules.display.projection.project_board), never raw ticket, patient or clinic "
        "data (non-negotiable 4)"
    )


def _personal_keys(value: object) -> set[str]:
    """Every personal key anywhere in a JSON-shaped value."""
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _PERSONAL_KEYS:
                found.add(str(key))
            found |= _personal_keys(item)
    elif isinstance(value, list | tuple):
        for item in value:
            found |= _personal_keys(item)
    return found


def ensure_payload_private(payload: Mapping[str, Any], mode: DisplayMode) -> None:
    """Refuse a payload carrying a personal key its display mode does not allow.

    The projection already leaves those keys out; this is the second lock on the same door, run on
    the serialised form, so a mistake in the first shows up as an error rather than as a name on a
    screen.

    Raises:
        BoardPrivacyError: A forbidden key is present, naming the keys and the mode.
    """
    allowed = {field.value for field in PERSONAL_FIELDS_ALLOWED[mode]}
    forbidden = _personal_keys(payload) - allowed
    if forbidden:
        raise BoardPrivacyError(
            f"a {mode.value} board payload carries {sorted(forbidden)}; it was not sent"
        )


# --------------------------------------------------------------------------------------
# The projection
# --------------------------------------------------------------------------------------


def displayed_site(db: Session, site_id: str) -> Site | None:
    """The clinic whose board this is, or ``None`` when it has no public board (unknown or hidden)."""
    return db.execute(
        select(Site).where(Site.id == site_id, *publicly_visible_site_clauses())
    ).scalar_one_or_none()


def effective_mode(site: Site, viewer: BoardViewer) -> DisplayMode:
    """The mode a response is projected under: the site's for its own screen, numbers otherwise."""
    return site.display_mode_enum if viewer.sees_site_mode else DisplayMode.NUMBER_ONLY


def _last_called(ticket: Ticket) -> datetime | None:
    """When the ticket was most recently called: called again beats called."""
    moment = ticket.recalled_at or ticket.called_at
    return stored_sast(moment) if moment is not None else None


def project_board(
    db: Session,
    site: Site,
    *,
    viewer: BoardViewer,
    moment: datetime | None = None,
) -> BoardState:
    """The clinic's board today, as ``viewer`` may see it. The only reader of tickets for a board.

    One query for the clinic's live queues and one for today's board tickets. Patients are read
    only when the mode could show a name, and only those whose tickets are on the screen; each
    shown ticket then goes through
    :func:`~src.modules.patients.consent.board_projection`, which asks for consent afresh.

    Args:
        db: The session.
        site: The clinic, from :func:`displayed_site`.
        viewer: Who the response is for; an anonymous viewer gets numbers only.
        moment: The time to project at (aware); ``None`` means now in Johannesburg.
    """
    now = moment or now_sast()
    mode = effective_mode(site, viewer)
    queues = (
        db.execute(
            displayed_select(Queue, site.id)
            .where(Queue.is_active.is_(True), Queue.is_deleted.is_(False))
            .order_by(Queue.display_order, Queue.name)
        )
        .scalars()
        .all()
    )
    by_queue: dict[str, list[Ticket]] = defaultdict(list)
    for ticket in db.execute(
        displayed_select(Ticket, site.id)
        .where(
            Ticket.service_day == business_date(now),
            Ticket.status.in_(_BOARD_STATUSES),
        )
        .order_by(Ticket.queue_id, *CALL_ORDER)
    ).scalars():
        by_queue[ticket.queue_id].append(ticket)

    panels: list[tuple[Queue, list[Ticket], list[Ticket], int]] = []
    for queue in queues:
        day = by_queue.get(queue.id, [])
        waiting = [t for t in day if t.status_enum is TicketStatus.WAITING]
        serving = sorted(
            (t for t in day if t.status_enum in NOW_SERVING_STATUSES),
            key=lambda t: _last_called(t) or now,
            reverse=True,
        )
        panels.append(
            (queue, serving[:NOW_SERVING_LIMIT], waiting[:UP_NEXT_LIMIT], len(waiting))
        )

    patients: dict[str, Patient] = {}
    if mode is not DisplayMode.NUMBER_ONLY:
        shown_ids = {
            t.patient_id
            for _, serving, next_up, _ in panels
            for t in (*serving, *next_up)
            if t.patient_id is not None
        }
        if shown_ids:
            patients = {
                patient.id: patient
                for patient in db.execute(
                    select(Patient).where(
                        Patient.id.in_(sorted(shown_ids)),
                        Patient.is_deleted.is_(False),
                    )
                ).scalars()
            }

    def shown(ticket: Ticket) -> BoardTicket:
        """One ticket through the consent rule: the only place a board ticket is built."""
        entry = board_projection(
            db,
            patients.get(ticket.patient_id) if ticket.patient_id else None,
            ticket_number=ticket.number,
            display_mode=mode,
            comment=ticket.reason_text if site.display_show_comment else None,
            visit_comment_consent=ticket.comment_consent,
        )
        return BoardTicket(
            number=entry.ticket_number,
            status=ticket.status_enum,
            called_at=_last_called(ticket),
            name=entry.name,
            comment=entry.comment,
        )

    return BoardState(
        site_id=site.id,
        clinic_name=site.name,
        display_mode=mode,
        language=site.board_language_enum,
        announce_audio=site.announce_audio,
        as_of=now,
        queues=tuple(
            BoardQueue(
                id=queue.id,
                label=queue.name,
                room=queue.room_label,
                now_serving=tuple(shown(t) for t in serving),
                up_next=tuple(shown(t) for t in next_up),
                waiting=count,
            )
            for queue, serving, next_up, count in panels
        ),
    )
