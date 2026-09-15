"""When a travelling patient should leave, as a pure rule of the wait and the trip (Issue 86)."""

from __future__ import annotations

from datetime import datetime, time

import pytest

from src.commons.enums import EstimateBasis, EstimateConfidence, TicketSource
from src.commons.time import APP_TIMEZONE
from src.modules.appointments.call_forward import (
    ARRIVAL_MARGIN_MINUTES,
    DEFAULT_TRAVEL_MINUTES,
    call_forward,
    stated_travel,
)
from src.modules.queue.estimate import WaitEstimate, WaitRange

MOMENT = datetime(2026, 10, 6, 9, 0, tzinfo=APP_TIMEZONE)


def _estimate(low: int, high: int) -> WaitEstimate:
    return WaitEstimate(
        wait=WaitRange(low, high),
        confidence=EstimateConfidence.LOW,
        basis=EstimateBasis.EXPECTED,
    )


@pytest.mark.parametrize(
    ("low", "due", "leave_at"),
    [
        (60, False, time(9, 20)),  # 60 - (30 + 10): leave at 09:20, not an hour early
        (41, False, time(9, 1)),
        (40, True, time(9, 0)),  # the trip and the margin exactly: leave now
        (25, True, time(9, 0)),  # already shorter than the trip: leave now, at once
    ],
)
def test_a_30_minute_trip_leaves_when_the_earliest_turn_is_the_trip_plus_the_margin(
    low: int, due: bool, leave_at: time
) -> None:
    """The alert is due once the low end of the wait is no more than 30 + 10 minutes."""
    plan = call_forward(_estimate(low, low + 15), 30, MOMENT)

    assert plan is not None
    assert ARRIVAL_MARGIN_MINUTES == 10
    assert (plan.due, plan.leave_at.timetz()) == (
        due,
        leave_at.replace(tzinfo=APP_TIMEZONE),
    )


def test_nobody_travelling_is_never_told_to_leave() -> None:
    """No trip, or a patient already at the clinic (0), gives no plan at all."""
    assert call_forward(_estimate(5, 10), None, MOMENT) is None
    assert call_forward(_estimate(5, 10), 0, MOMENT) is None


def test_the_stated_trip_is_the_answer_the_default_or_nothing() -> None:
    """Kept only where the clinic runs a virtual waiting room, never for a walk-in, and defaulted when unsaid."""
    assert stated_travel(enabled=True, source=TicketSource.WEB, requested=30) == 30
    assert stated_travel(enabled=True, source=TicketSource.WEB, requested=0) == 0
    assert (
        stated_travel(enabled=True, source=TicketSource.USSD, requested=None)
        == DEFAULT_TRAVEL_MINUTES
        == 15
    )
    assert stated_travel(enabled=False, source=TicketSource.WEB, requested=30) is None
    assert (
        stated_travel(enabled=True, source=TicketSource.WALK_IN, requested=30) is None
    )
