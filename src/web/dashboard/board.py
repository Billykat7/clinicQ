"""The front desk's queue cards: who is waiting, who is with staff, and whether anyone is stuck (Issue 49).

The screen reception keeps open all day answers one question per card at a glance, "is anyone stuck?",
so each card carries, for today:

* how many are **waiting** and how many are **with staff** (called, called again, or being seen);
* the **average wait today**, from the tickets already called (joined to called);
* the **longest current wait**, and whether it has passed the clinic's stuck threshold
  (``DASHBOARD_STUCK_WAIT_MINUTES``), which is what turns a card red without anyone reading a number;
* the tickets with staff, each with its status and how long since it was called.

Everything is read in **one query** for the clinic's day, through the site guard, and folded per queue
here, so ten queues and two hundred tickets cost one round trip however often the board refreshes.
The waiting line with its reorder controls is Issue 52's (:mod:`src.web.dashboard.reorder`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy.orm import Session

from src.commons.enums import TicketStatus
from src.commons.time import business_date, now_sast, stored_sast
from src.core.config import get_settings
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models import Ticket
from src.modules.queue.tickets import CALL_ORDER
from src.modules.queues.service import list_queues
from src.web.components import TICKET_STATUS_BADGES, StatusBadge

#: Statuses that mean "with a member of staff now": called to the room, called again, or being seen.
WITH_STAFF: Final[frozenset[TicketStatus]] = frozenset(
    {TicketStatus.CALLED, TicketStatus.RECALLED, TicketStatus.IN_PROGRESS}
)
_SECONDS_PER_MINUTE: Final = 60


@dataclass(frozen=True, slots=True)
class WithStaffTicket:
    """A ticket called to a room or being seen, and how long ago it was called."""

    number: str
    badge: StatusBadge
    minutes_since_called: int | None


@dataclass(frozen=True, slots=True)
class BoardCard:
    """One queue as the front desk's card shows it."""

    id: str
    name: str
    room_label: str | None
    is_active: bool
    waiting: int
    with_staff: int
    #: Mean minutes from joining to being called, over today's called tickets; ``None`` before the first call.
    average_wait_minutes: int | None
    #: The longest current wait among waiting tickets; ``None`` when nobody is waiting.
    longest_wait_minutes: int | None
    #: Whether the longest wait has passed the stuck threshold.
    stuck: bool
    with_staff_tickets: tuple[WithStaffTicket, ...]


@dataclass(frozen=True, slots=True)
class Board:
    """Every card of a clinic's front desk, and the moment it was read."""

    cards: tuple[BoardCard, ...]
    as_of: datetime
    stuck_after_minutes: int


def _minutes(later: datetime, earlier: datetime) -> int:
    """Whole minutes between two moments, never negative."""
    return max(0, int((later - earlier).total_seconds() // _SECONDS_PER_MINUTE))


def read_board(
    db: Session,
    access: SiteAccess,
    *,
    only: frozenset[str] | None = None,
    moment: datetime | None = None,
) -> Board:
    """The clinic's cards for today, in the clinic's queue order; ``only`` narrows to those queues.

    One query for the day's tickets, whatever their status, in call order; the counts, the average,
    the longest wait and the with-staff list are folded from it per queue.
    """
    now = moment or now_sast()
    threshold = get_settings().dashboard_stuck_wait_minutes
    queues = [
        queue
        for queue in list_queues(db, access).items
        if only is None or queue.id in only
    ]
    tickets = db.execute(
        scoped_select(Ticket, access)
        .where(Ticket.service_day == business_date(now))
        .order_by(Ticket.queue_id, *CALL_ORDER)
    ).scalars()
    by_queue: dict[str, list[Ticket]] = defaultdict(list)
    for ticket in tickets:
        by_queue[ticket.queue_id].append(ticket)

    cards = []
    for queue in queues:
        day = by_queue.get(queue.id, [])
        waiting = [t for t in day if t.status_enum is TicketStatus.WAITING]
        with_staff = [t for t in day if t.status_enum in WITH_STAFF]
        called = [t for t in day if t.called_at is not None]
        average = (
            round(
                sum(
                    (
                        stored_sast(t.called_at) - stored_sast(t.joined_at)
                    ).total_seconds()
                    for t in called
                    if t.called_at is not None
                )
                / len(called)
                / _SECONDS_PER_MINUTE
            )
            if called
            else None
        )
        longest = (
            max(_minutes(now, stored_sast(t.joined_at)) for t in waiting)
            if waiting
            else None
        )
        cards.append(
            BoardCard(
                id=queue.id,
                name=queue.name,
                room_label=queue.room_label,
                is_active=queue.is_active,
                waiting=len(waiting),
                with_staff=len(with_staff),
                average_wait_minutes=average,
                longest_wait_minutes=longest,
                stuck=longest is not None and longest >= threshold,
                with_staff_tickets=tuple(
                    WithStaffTicket(
                        number=t.number,
                        badge=TICKET_STATUS_BADGES[t.status_enum],
                        minutes_since_called=(
                            _minutes(now, stored_sast(t.called_at))
                            if t.called_at
                            else None
                        ),
                    )
                    for t in with_staff
                ),
            )
        )
    return Board(cards=tuple(cards), as_of=now, stuck_after_minutes=threshold)
