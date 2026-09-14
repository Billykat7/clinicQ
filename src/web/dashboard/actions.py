"""What a member of staff can do next with a patient who is with them (Issue 50).

*Call next* is pressed hundreds of times a day by someone talking to a patient at the same time, so the
buttons around it must be right without anyone thinking about them. This module decides **which**
buttons a ticket offers, and it decides nothing itself:

* a status change is offered only when the lifecycle's own table allows it
  (:func:`~src.modules.queue.lifecycle.is_legal`, Issue 41);
* *Undo call* only while the lifecycle says the call can still be undone
  (:func:`~src.modules.queue.lifecycle.undo_window_ends`);
* a confirmation only for the one action that is hard to take back and easy to hit by mistake: marking a
  **no-show**, which ends the patient's ticket while they may be in the corridor. *Done* ends a ticket
  too, but it is the room's everyday last step; asking every time would teach people to click through
  the question, which is worse than not asking.

Whether the signed-in person may press them is the page's grant (``queues.tickets`` to move a ticket,
``queues.call`` to call and undo), and the API checks both again. A button the person may not press is
shown disabled, never hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from src.commons.enums import TicketStatus
from src.database.models import Ticket
from src.modules.queue.lifecycle import is_legal, undo_window_ends
from src.web.components import TICKET_STATUS_BADGES


class ActionKind(StrEnum):
    """The buttons beside a patient with staff."""

    START = "start"
    RECALL = "recall"
    NO_SHOW = "no_show"
    DONE = "done"
    UNDO_CALL = "undo_call"


@dataclass(frozen=True, slots=True)
class TicketAction:
    """One button: what it says, where it goes, and what the card shows while the server answers."""

    kind: ActionKind
    label: str
    #: The status the button asks for; ``None`` for *Undo call*, which has its own route.
    to: TicketStatus | None
    #: The badge the card shows at once, before the server has answered (rolled back on a refusal).
    pending_label: str
    #: The question asked first, or ``None`` to act at once.
    confirm: str | None
    #: Which grant the button needs: ``call`` (``queues.call``) or ``move`` (``queues.tickets``).
    needs: str
    #: The one button drawn as the obvious next step.
    primary: bool = False


#: Status changes the panel offers, in the order they are drawn, with the button's words.
_MOVES: Final[tuple[tuple[ActionKind, TicketStatus, str], ...]] = (
    (ActionKind.START, TicketStatus.IN_PROGRESS, "Start"),
    (ActionKind.DONE, TicketStatus.DONE, "Done"),
    (ActionKind.RECALL, TicketStatus.RECALLED, "Recall"),
    (ActionKind.NO_SHOW, TicketStatus.NO_SHOW, "No-show"),
)


def ticket_actions(ticket: Ticket, *, moment: datetime) -> tuple[TicketAction, ...]:
    """The buttons ``ticket`` offers at ``moment``, from the lifecycle's table and its undo window."""
    current = ticket.status_enum
    actions = [
        TicketAction(
            kind=kind,
            label=label,
            to=to,
            pending_label=TICKET_STATUS_BADGES[to].label,
            confirm=(
                f"Mark {ticket.number} as a no-show? Their ticket ends, and they would have to "
                "join again."
                if kind is ActionKind.NO_SHOW
                else None
            ),
            needs="move",
            primary=kind in {ActionKind.START, ActionKind.DONE},
        )
        for kind, to, label in _MOVES
        if is_legal(current, to)
    ]
    ends = undo_window_ends(ticket)
    if ends is not None and moment < ends:
        actions.append(
            TicketAction(
                kind=ActionKind.UNDO_CALL,
                label="Undo call",
                to=None,
                pending_label=TICKET_STATUS_BADGES[TicketStatus.WAITING].label,
                confirm=None,
                needs="call",
            )
        )
    return tuple(actions)


@dataclass(frozen=True, slots=True)
class Elapsed:
    """How long a patient has been at the current step, and the words for that step.

    ``since`` goes into the page so the time keeps counting between refreshes; ``minutes`` is what the
    server saw when it drew the card.
    """

    label: str
    since: datetime
    minutes: int


def elapsed(
    ticket: Ticket, step_started: datetime | None, *, moment: datetime
) -> Elapsed | None:
    """How long ``ticket`` has been at its step at ``moment``; ``None`` before it was called.

    ``step_started`` is the step's start, already read back as Johannesburg time by the caller: when it
    started being seen, was called again, or was called.
    """
    if step_started is None:
        return None
    return Elapsed(
        label=TICKET_STATUS_BADGES[ticket.status_enum].label,
        since=step_started,
        minutes=max(0, int((moment - step_started).total_seconds() // 60)),
    )
