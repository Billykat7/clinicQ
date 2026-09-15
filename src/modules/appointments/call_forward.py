"""When a patient waiting away from the clinic should leave: a pure rule of the wait and the trip (Issue 86).

The virtual waiting room lets a patient who joined by phone wait at home or nearby. The only inputs are
the wait estimate the patient already sees (Issue 42, read for one ticket by
:func:`src.modules.queue.waits.ticket_wait`) and the trip they said they need. No routing or traffic
service is asked: the patient knows their own taxi better than a map does.

**The rule.** An estimate is a range, and a turn can come as early as its low end. A patient who leaves
when ``low_minutes`` is no more than their trip plus :data:`ARRIVAL_MARGIN_MINUTES` arrives at least
that margin before the earliest likely turn, and not an hour early, because until then the rule says
"not yet" and shows when to leave::

    lead      = travel_minutes + ARRIVAL_MARGIN_MINUTES
    due       = estimate.low_minutes <= lead
    leave_at  = moment + max(estimate.low_minutes - lead, 0) minutes

Checked by the call-forward sweep every ``VIRTUAL_WAITING_SWEEP_SECONDS`` (30 by default), well inside the margin.

**Travel time is optional.** A patient who says nothing is given :data:`DEFAULT_TRAVEL_MINUTES`, a short
taxi or a walk; one who says they are already at the clinic (``0``) is never told to leave, and hears
"you are next" and "please come in now" like everyone else. A walk-in is at the clinic by definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from src.commons.enums import TicketSource, TicketStatus
from src.modules.queue.estimate import WaitEstimate

#: The trip assumed when a patient at a virtual-waiting clinic does not say: a short taxi ride or a walk.
DEFAULT_TRAVEL_MINUTES: Final = 15
#: How long before the earliest likely turn the patient should arrive.
ARRIVAL_MARGIN_MINUTES: Final = 10
#: What the switch does, in the words a clinic manager is shown beside it.
VIRTUAL_WAITING_EXPLANATION: Final = (
    "Patients who join by phone are asked how long their trip to the clinic takes (15 minutes if they "
    "do not say) and may wait at home or nearby. When the expected wait comes down to their trip plus "
    f"{ARRIVAL_MARGIN_MINUTES} minutes, they are told once that it is time to leave, and can tap "
    '"On my way", which the front desk sees. Nobody loses their place for not answering. While off, '
    "nobody is asked for a trip and nobody is told to leave."
)
#: The statuses a patient may still be on their way in: waiting, or called before they arrived.
ON_THE_WAY_STATUSES: Final = frozenset(
    {TicketStatus.WAITING, TicketStatus.CALLED, TicketStatus.RECALLED}
)
#: The trips the join page offers, in minutes; ``0`` is "I am at the clinic already".
TRAVEL_CHOICES: Final = (0, 5, 10, 15, 20, 30, 45, 60, 90, 120)


@dataclass(frozen=True, slots=True)
class CallForward:
    """When to leave, and whether that is now."""

    travel_minutes: int
    #: The moment to set off: the moment asked about when it is already due.
    leave_at: datetime
    #: Whether the patient should leave now.
    due: bool


def call_forward(
    estimate: WaitEstimate, travel_minutes: int | None, moment: datetime
) -> CallForward | None:
    """When a patient with this estimate and this trip should leave; ``None`` when they are not travelling.

    Args:
        estimate: The ticket's wait estimate, exactly as its page shows it.
        travel_minutes: The stated trip; ``None`` or ``0`` means nobody is travelling.
        moment: When the estimate was read (aware).
    """
    if not travel_minutes:
        return None
    lead = travel_minutes + ARRIVAL_MARGIN_MINUTES
    low = estimate.wait.low_minutes
    return CallForward(
        travel_minutes=travel_minutes,
        leave_at=moment + timedelta(minutes=max(low - lead, 0)),
        due=low <= lead,
    )


def stated_travel(
    *, enabled: bool, source: TicketSource, requested: int | None
) -> int | None:
    """The trip to store on a new ticket: the patient's answer, the default, or nothing.

    Nothing when the clinic does not run a virtual waiting room (an answer sent anyway is not kept) and
    nothing for a walk-in, who is standing at the desk.
    """
    if not enabled or source is TicketSource.WALK_IN:
        return None
    return DEFAULT_TRAVEL_MINUTES if requested is None else requested
