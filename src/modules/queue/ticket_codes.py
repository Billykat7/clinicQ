"""A ticket's code: the QR a patient shows and the short reference reception types (Issue 70).

The reference code is made with the ticket (Issue 39, :func:`~src.modules.queue.sequence.new_reference_code`):
six characters from an alphabet with no ``0 O 1 I L``, drawn with :mod:`secrets`, written ``K7M-4QP`` to be
read aloud. This module draws it and resolves it.

* **One code, two forms.** The QR encodes ``CLINICQ:K7M-4QP``: the printed reference behind a prefix that says
  whose code it is. A USB scanner at the desk types exactly that into the lookup field; a person types
  ``K7M-4QP`` (or ``k7m 4qp``). Both resolve through :func:`read_code`. The ticket page, its offline copy and
  the printed stub all draw the QR from :func:`qr_for`, so they carry the same code.
* **Drawn as data, not as an image.** :func:`qr_for` returns the QR's modules as one SVG path. The ticket page
  data carries it, so the page and the phone's offline copy (Issue 69) draw the QR with no network and no
  QR library in the browser, and the stub prints it.
* **Resolved at one clinic, on one day.** :func:`lookup` finds a code only among the tickets the caller's
  clinic access reaches, so a code from another clinic is "not found", exactly like a code that does not
  exist. A code from an earlier service day is refused with the day it was for. A ticket that has ended
  cannot be used again; a transferred ticket's code follows the visit to the leg that is open now.
* **Not a way in.** A code is random, not derived from the ticket's id, number or time, and it finds a ticket
  only for staff signed in at that ticket's clinic. It never opens the patient's ticket page (that is the
  page token, Issue 68), so a photographed QR shows nothing to whoever holds the photo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import lru_cache
from typing import Final

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from sqlalchemy.orm import Session

from src.commons.enums import TICKET_TERMINAL_STATUSES, TicketStatus
from src.commons.exceptions import ConflictError, NotFoundError, UnprocessableError
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models.queue import Queue
from src.database.models.ticket import Ticket
from src.modules.queue.schemas import TicketLookupOut, TicketOut, WaitOut
from src.modules.queue.sequence import format_reference_code, parse_reference_code
from src.modules.queue.tickets import waiting_ahead
from src.modules.queue.waits import estimates_for

#: What the QR says before the code, so a scan of anything else is recognised as not a ticket.
QR_PREFIX: Final = "CLINICQ:"
#: The quiet margin around the QR, in modules: the four the QR standard asks for, so phones read it.
QR_QUIET_ZONE: Final = 4
#: How each character of a code is said aloud across a counter: the ICAO spelling alphabet for letters, the
#: digit itself for digits. The alphabet has no ``0 O 1 I L``, so nothing here sounds like anything else.
SPOKEN: Final[dict[str, str]] = {
    **{digit: digit for digit in "23456789"},
    "A": "Alfa",
    "B": "Bravo",
    "C": "Charlie",
    "D": "Delta",
    "E": "Echo",
    "F": "Foxtrot",
    "G": "Golf",
    "H": "Hotel",
    "J": "Juliett",
    "K": "Kilo",
    "M": "Mike",
    "N": "November",
    "P": "Papa",
    "Q": "Quebec",
    "R": "Romeo",
    "S": "Sierra",
    "T": "Tango",
    "U": "Uniform",
    "V": "Victor",
    "W": "Whiskey",
    "X": "X-ray",
    "Y": "Yankee",
    "Z": "Zulu",
}
#: How many transfers a code is followed through before giving up (a visit rarely has more than three legs).
MAX_LEGS: Final = 10


@dataclass(frozen=True, slots=True)
class TicketQr:
    """A QR ready to draw: ``<svg viewBox="0 0 size size"><path d=path/></svg>``, dark modules filled."""

    payload: str
    size: int
    path: str


def qr_payload(reference_code: str) -> str:
    """What the QR encodes for a stored code (``K7M4QP``): ``CLINICQ:K7M-4QP``."""
    return f"{QR_PREFIX}{format_reference_code(reference_code)}"


@lru_cache(maxsize=2048)
def qr_for(reference_code: str) -> TicketQr:
    """The QR for a stored reference code: the smallest symbol (21 modules), medium error correction.

    Everything in the payload is in the QR alphanumeric set (capitals, digits, ``:`` and ``-``), which keeps
    it to version 1, the largest modules a phone screen or a 58 mm stub can show.
    """
    payload = qr_payload(reference_code)
    symbol = qrcode.QRCode(
        error_correction=ERROR_CORRECT_M, border=QR_QUIET_ZONE, box_size=1
    )
    symbol.add_data(payload, optimize=0)
    symbol.make(fit=True)
    matrix = symbol.get_matrix()
    runs: list[str] = []
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if not row[x]:
                x += 1
                continue
            start = x
            while x < len(row) and row[x]:
                x += 1
            runs.append(f"M{start} {y}h{x - start}v1h-{x - start}z")
    return TicketQr(payload=payload, size=len(matrix), path="".join(runs))


def spoken(reference_code: str) -> str:
    """A stored code as it is read aloud: ``K7M4QP`` is ``Kilo 7 Mike, 4 Quebec Papa``."""
    code = parse_reference_code(reference_code) or reference_code
    half = len(code) // 2
    return ", ".join(
        " ".join(SPOKEN[ch] for ch in part) for part in (code[:half], code[half:])
    )


def read_code(raw: str) -> str | None:
    """A scanned or typed code as stored (``K7M4QP``), or ``None`` when it cannot be a ticket code."""
    text = raw.strip()
    if text.upper().startswith(QR_PREFIX):
        text = text[len(QR_PREFIX) :]
    return parse_reference_code(text)


class LookupRefusal(StrEnum):
    """Why a code did not open a ticket. The wire code is ``queue.lookup.<value>``."""

    NOT_A_CODE = "not_a_code"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    ENDED = "ended"


class LookupNotACodeError(UnprocessableError):
    """What was typed or scanned cannot be a ticket code: HTTP 422."""

    def __init__(self) -> None:
        super().__init__(
            "That is not a ticket code. A code is six letters and numbers, like K7M-4QP.",
            code=f"queue.lookup.{LookupRefusal.NOT_A_CODE.value}",
        )


class LookupNotFoundError(NotFoundError):
    """No ticket at this clinic has the code: HTTP 404, the same for another clinic's ticket."""

    def __init__(self, code: str) -> None:
        super().__init__(
            f"No ticket at this clinic has the code {format_reference_code(code)}. Check it with the patient.",
            code=f"queue.lookup.{LookupRefusal.NOT_FOUND.value}",
        )


class LookupExpiredError(UnprocessableError):
    """The code is from an earlier service day: HTTP 422, saying which day."""

    def __init__(self, code: str, day: date) -> None:
        super().__init__(
            f"Code {format_reference_code(code)} was for {day:%A %d %B %Y}. A ticket code works only on the day "
            "it was issued: the patient needs a new ticket today.",
            code=f"queue.lookup.{LookupRefusal.EXPIRED.value}",
        )
        self.day = day


class LookupEndedError(ConflictError):
    """The code's ticket has already ended, so its code is used up: HTTP 409."""

    def __init__(self, ticket: Ticket) -> None:
        super().__init__(
            f"Ticket {ticket.number} ({format_reference_code(ticket.reference_code)}) has ended: "
            f"{ticket.status_enum.value.replace('_', ' ')}. Its code cannot be used again.",
            code=f"queue.lookup.{LookupRefusal.ENDED.value}",
        )
        self.ticket = ticket


@dataclass(frozen=True, slots=True)
class LookupResult:
    """The ticket a code opens, and the ticket whose code it was when a transfer was followed."""

    ticket: Ticket
    followed_from: Ticket | None


def lookup(db: Session, access: SiteAccess, raw: str, *, today: date) -> LookupResult:
    """The open ticket today that ``raw`` (scanned or typed) names at the caller's clinic.

    Raises:
        LookupNotACodeError: ``raw`` cannot be a code.
        LookupNotFoundError: No ticket this access reaches has it (another clinic's included).
        LookupExpiredError: The ticket was for an earlier day.
        LookupEndedError: The ticket, or the leg its transfer leads to, has ended.
    """
    code = read_code(raw)
    if code is None:
        raise LookupNotACodeError()
    ticket = db.execute(
        scoped_select(Ticket, access).where(Ticket.reference_code == code)
    ).scalar_one_or_none()
    if ticket is None:
        raise LookupNotFoundError(code)
    if ticket.service_day != today:
        raise LookupExpiredError(code, ticket.service_day)
    current = ticket
    for _ in range(MAX_LEGS):
        if current.status_enum is not TicketStatus.TRANSFERRED:
            break
        after = db.execute(
            scoped_select(Ticket, access).where(
                Ticket.transferred_from_id == current.id
            )
        ).scalar_one_or_none()
        if after is None:
            break
        current = after
    if current.status_enum in TICKET_TERMINAL_STATUSES:
        raise LookupEndedError(current)
    return LookupResult(
        ticket=current, followed_from=ticket if current is not ticket else None
    )


def lookup_view(
    db: Session, access: SiteAccess, raw: str, *, today: date
) -> TicketLookupOut:
    """:func:`lookup`, answered the way the desk reads it: the ticket, where it stands, and a sentence.

    Raises the same refusals as :func:`lookup`.
    """
    found = lookup(db, access, raw, today=today)
    ticket = found.ticket
    queue = db.execute(
        scoped_select(Queue, access).where(Queue.id == ticket.queue_id)
    ).scalar_one()
    ahead: int | None = None
    wait: WaitOut | None = None
    if ticket.status_enum is TicketStatus.WAITING:
        ahead = waiting_ahead(db, ticket)
        wait = WaitOut.of(estimates_for(db, [queue], {queue.id: ahead})[queue.id])
        standing = f"waiting in {queue.name}, {ahead} ahead"
    else:
        standing = f"{ticket.status_enum.value.replace('_', ' ')} in {queue.name}"
    moved = (
        f", moved on from {found.followed_from.number}" if found.followed_from else ""
    )
    return TicketLookupOut(
        ticket=TicketOut.of(ticket),
        queue_name=queue.name,
        room_label=queue.room_label,
        waiting_ahead=ahead,
        wait=wait,
        reference_spoken=spoken(ticket.reference_code),
        followed_from=found.followed_from.number if found.followed_from else None,
        message=f"{ticket.number} is {standing}{moved}.",
    )
