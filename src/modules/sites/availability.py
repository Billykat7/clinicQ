"""One gate: may a patient join a queue at this clinic right now? (Issue 24)

**Every channel asks the same function.** The web app, the USSD menu, the WhatsApp adapter and the
receptionist's own screen are four doors into one clinic, and a closure has to shut all four at the
same instant. The way to guarantee that is not four careful implementations; it is one, here, that
does not take the channel as an input at all.

:func:`join_gate` is therefore a function of the **clinic and the moment**, and nothing else. Its
:class:`JoinGate` answer carries a reason written for a patient, because every one of those four
doors has to say something when it refuses, and "this service is unavailable" is not it.

The join routes themselves arrive with Issue 40; this is the check they call, and the reason it
exists a milestone early is that queues (Issue 25) and onboarding (Issue 29) both need it too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.commons.enums import SiteStatus
from src.commons.time import now_sast
from src.database.models.site import Site
from src.modules.sites.hours import OpeningSchedule, OpenState, open_state


@dataclass(frozen=True, slots=True)
class JoinGate:
    """Whether a patient may take a ticket, and what to tell them when they may not.

    ``reason`` is written for the person at the door, in plain language, and is ``None`` exactly
    when ``allowed`` is true. ``next_open_at`` lets a channel add "opens 07:00 tomorrow" without
    asking a second question.
    """

    allowed: bool
    reason: str | None = None
    next_open_at: datetime | None = None


#: What a patient is told when a clinic's listing is not live. Deliberately the same sentence for
#: "never verified" and "suspended": which of the two it is, is the clinic's business and the
#: platform's, not a stranger's.
NOT_ACCEPTING = "This clinic is not accepting patients through ClinicQ at the moment."


def join_gate(
    site: Site, schedule: OpeningSchedule, moment: datetime | None = None
) -> JoinGate:
    """Decide whether a queue at ``site`` may be joined at ``moment``.

    The order is deliberate: a suspended clinic is refused before its opening hours are even
    considered, because a clinic the platform has switched off is shut whatever its schedule says.

    Args:
        site: The clinic.
        schedule: Its hours, holidays and closures (:func:`~src.modules.sites.hours.schedule_for`).
        moment: Aware datetime; ``None`` means now in Johannesburg.

    Returns:
        The decision, with a sentence a channel can show as it stands.
    """
    moment = moment or now_sast()
    if site.is_deleted or not site.is_active:
        return JoinGate(allowed=False, reason=NOT_ACCEPTING)
    if site.status_enum in {SiteStatus.SUSPENDED, SiteStatus.DRAFT}:
        return JoinGate(allowed=False, reason=NOT_ACCEPTING)

    state: OpenState = open_state(schedule, moment)
    if state.is_open:
        return JoinGate(allowed=True, next_open_at=moment)
    if state.closure_reason:
        return JoinGate(
            allowed=False,
            reason=f"{site.name} is closed: {state.closure_reason}",
            next_open_at=state.next_open_at,
        )
    return JoinGate(
        allowed=False,
        reason=f"{site.name} is closed at the moment.",
        next_open_at=state.next_open_at,
    )
