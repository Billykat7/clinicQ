"""What the reorder controls and the override trail show, read once on the server (Issue 52).

Issue 46 owns every rule of a priority override: a reason is required, a patient only moves forward,
never ahead of someone already called, and every override writes a ``queue_reorder`` row beside an
audit row. This module owns none of that. It reads what the dashboard needs to **offer** an override
and to **show** the ones already made:

* each queue's waiting line in call order (:data:`~src.modules.queue.tickets.CALL_ORDER`), the order
  the drag handles and the "Move forward" buttons act on;
* which of those tickets were moved forward today, for the staff-only priority badge;
* the queue's latest overrides, for the trail on its card;
* the whole clinic's overrides for a day, with counts per staff member, for the manager's view.

**Priority is staff-only by construction.** Everything here is read through the site guard for a
signed-in staff member, and only when their grant at the clinic lets them read
``queues.tickets.priority``: a caller without it is never handed a reason, a note or a badge to hide.
Nothing in this module is reachable from the waiting-room board, whose projection
(:class:`~src.modules.patients.consent.BoardEntry`) has no field for any of it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy.orm import Session

from src.commons.enums import PriorityReason, TicketSource, TicketStatus
from src.commons.time import business_date, stored_sast
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models import QueueReorder, Ticket
from src.modules.queue.priority import (
    COUNTS_ARE_NOT_RANKINGS,
    PRIORITY_REASON_LABELS,
    override_counts,
    reorder_trail,
)
from src.modules.queue.tickets import board_select

#: The resource whose grants decide whether a caller may see (``read``) and make (``update``) an
#: override. The same key the priority routes check, so the page and the API cannot disagree.
PRIORITY_RESOURCE: Final = "queues.tickets.priority"
#: How many of a queue's latest overrides its card shows; the manager's view has the rest.
TRAIL_ON_CARD: Final = 3

#: How each channel reads beside a ticket in the waiting line.
SOURCE_LABELS: Final[dict[TicketSource, str]] = {
    TicketSource.WEB: "Web",
    TicketSource.USSD: "USSD",
    TicketSource.WHATSAPP: "WhatsApp",
    TicketSource.WALK_IN: "Walk-in",
}


@dataclass(frozen=True, slots=True)
class ReasonChoice:
    """One reason the prompt offers: its wire value and its words."""

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class PriorityMark:
    """The staff-only badge on a ticket moved forward today: why, and by whom."""

    reason_label: str
    staff: str
    at: datetime


@dataclass(frozen=True, slots=True)
class LineTicket:
    """One waiting ticket in its queue's call order."""

    id: str
    number: str
    place: int
    name: str | None
    source_label: str
    joined_at: datetime
    priority: PriorityMark | None


@dataclass(frozen=True, slots=True)
class TrailEntry:
    """One override as the trail shows it: which ticket, who, why and the places before and after."""

    id: str
    ticket_number: str
    queue_id: str
    staff: str
    reason: PriorityReason
    reason_label: str
    note: str | None
    position_before: int
    position_after: int
    at: datetime


@dataclass(frozen=True, slots=True)
class QueueLine:
    """A queue's waiting line and, for a caller who may read them, its latest overrides."""

    queue_id: str
    waiting: tuple[LineTicket, ...]
    trail: tuple[TrailEntry, ...]


@dataclass(frozen=True, slots=True)
class StaffCount:
    """How many overrides one staff member made on the day. Listed by name, never ranked."""

    staff: str
    overrides: int


@dataclass(frozen=True, slots=True)
class OverrideDay:
    """The manager's view of one day: every override, newest first, and the counts by name."""

    day: date
    entries: tuple[TrailEntry, ...]
    counts: tuple[StaffCount, ...]
    counts_note: str


@dataclass(frozen=True, slots=True)
class OverrideFilters:
    """The manager's filters over a day's overrides: a queue, a reason, a staff member."""

    queue: str | None = None
    reason: PriorityReason | None = None
    staff: str | None = None

    @property
    def active(self) -> bool:
        """Whether any filter narrows the list."""
        return any((self.queue, self.reason, self.staff))

    def apply(self, entries: Sequence[TrailEntry]) -> tuple[TrailEntry, ...]:
        """The entries every set filter admits, in their original (newest first) order."""
        return tuple(
            entry
            for entry in entries
            if (self.queue is None or entry.queue_id == self.queue)
            and (self.reason is None or entry.reason is self.reason)
            and (self.staff is None or entry.staff == self.staff)
        )


def reason_choices() -> tuple[ReasonChoice, ...]:
    """The reasons in the enum's order, with their words, for the reason prompt."""
    return tuple(
        ReasonChoice(value=reason.value, label=PRIORITY_REASON_LABELS[reason])
        for reason in PriorityReason
    )


def _ticket_numbers(
    db: Session, access: SiteAccess, ticket_ids: Iterable[str]
) -> dict[str, str]:
    """``{ticket id: number}`` for tickets at this clinic, through the site guard."""
    ids = sorted(set(ticket_ids))
    if not ids:
        return {}
    rows = db.execute(scoped_select(Ticket, access).where(Ticket.id.in_(ids))).scalars()
    return {row.id: row.number for row in rows}


def _entries(
    db: Session, access: SiteAccess, rows: Sequence[QueueReorder]
) -> tuple[TrailEntry, ...]:
    """Trail rows as entries, each with its ticket's number (read through the site guard)."""
    numbers = _ticket_numbers(db, access, (row.ticket_id for row in rows))
    entries = []
    for row in rows:
        reason = PriorityReason(row.reason_code)
        entries.append(
            TrailEntry(
                id=row.id,
                ticket_number=numbers.get(row.ticket_id, "—"),
                queue_id=row.queue_id,
                staff=row.staff,
                reason=reason,
                reason_label=PRIORITY_REASON_LABELS[reason],
                note=row.note,
                position_before=row.position_before,
                position_after=row.position_after,
                at=stored_sast(row.created_at),
            )
        )
    return tuple(entries)


def queue_lines(
    db: Session,
    access: SiteAccess,
    queue_ids: Sequence[str],
    *,
    include_priority: bool,
) -> dict[str, QueueLine]:
    """Each queue's waiting line today in call order, with badges and a trail when allowed.

    Args:
        db: The session.
        access: The clinic, from the site guard.
        queue_ids: The queues to read, in the order the page shows them.
        include_priority: Whether the caller's grant at this clinic reads
            ``queues.tickets.priority``. When ``False``, no override is read at all, so the page has
            no badge or trail to leave out.

    Returns:
        ``{queue id: QueueLine}`` for every id in ``queue_ids``.
    """
    today = business_date()
    trail_rows = reorder_trail(db, access, today) if include_priority else ()
    entries = _entries(db, access, trail_rows)
    # The trail is newest first, so the first entry seen for a ticket is its latest override.
    marks: dict[str, PriorityMark] = {}
    for row, entry in zip(trail_rows, entries, strict=True):
        marks.setdefault(
            row.ticket_id,
            PriorityMark(
                reason_label=entry.reason_label, staff=entry.staff, at=entry.at
            ),
        )
    lines: dict[str, QueueLine] = {}
    for queue_id in queue_ids:
        waiting = db.execute(
            board_select(access, queue_id, today, statuses=(TicketStatus.WAITING,))
        ).scalars()
        lines[queue_id] = QueueLine(
            queue_id=queue_id,
            waiting=tuple(
                LineTicket(
                    id=ticket.id,
                    number=ticket.number,
                    place=place,
                    name=ticket.walk_in_name,
                    source_label=SOURCE_LABELS[ticket.source_enum],
                    joined_at=stored_sast(ticket.joined_at),
                    priority=marks.get(ticket.id),
                )
                for place, ticket in enumerate(waiting, start=1)
            ),
            trail=tuple(entry for entry in entries if entry.queue_id == queue_id)[
                :TRAIL_ON_CARD
            ],
        )
    return lines


def override_day(db: Session, access: SiteAccess, day: date) -> OverrideDay:
    """Every override at this clinic on ``day``, newest first, and the counts per staff member."""
    rows = reorder_trail(db, access, day)
    return OverrideDay(
        day=day,
        entries=_entries(db, access, rows),
        counts=tuple(
            StaffCount(staff=count.staff, overrides=count.overrides)
            for count in override_counts(db, access, day, day)
        ),
        counts_note=COUNTS_ARE_NOT_RANKINGS,
    )
