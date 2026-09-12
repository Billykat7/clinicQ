"""The two pieces of discovery arithmetic worth pinning on their own (Issue 31).

Both are pure and both are promises a patient reads: the travel time next to each clinic, and the
radius a search actually used. Everything else about the search is proven against PostGIS in
``tests/integration/discovery/``.
"""

import math

import pytest

from src.modules.discovery.service import (
    DEFAULT_RADIUS_M,
    MAX_RADIUS_M,
    MIN_RADIUS_M,
    clamp_radius,
)
from src.modules.discovery.travel import TravelEstimate, estimate_travel


@pytest.mark.parametrize(
    ("distance_m", "expected"),
    [
        # 1 km in a straight line is 1.3 km by road: 17.3 min on foot, 3.1 min by car, rounded up.
        (1_000, TravelEstimate(walking_minutes=18, driving_minutes=4)),
        # 10 km: 13 km by road, 173.3 min walking and 31.2 min driving.
        (10_000, TravelEstimate(walking_minutes=174, driving_minutes=32)),
    ],
)
def test_travel_time_is_the_road_distance_at_a_stated_speed_rounded_up(
    distance_m: float, expected: TravelEstimate
) -> None:
    """The three numbers in the module docstring, applied."""
    assert estimate_travel(distance_m) == expected


def test_a_clinic_across_the_road_is_never_zero_minutes_away() -> None:
    """ "0 min" reads as a mistake; the floor is one minute each way."""
    assert estimate_travel(0) == TravelEstimate(walking_minutes=1, driving_minutes=1)


@pytest.mark.parametrize("bad", [-1.0, math.inf, math.nan])
def test_a_distance_that_is_not_a_distance_is_refused(bad: float) -> None:
    """A negative or non-finite figure is a bug upstream, not a trip to estimate."""
    with pytest.raises(ValueError, match="finite number of metres"):
        estimate_travel(bad)


@pytest.mark.parametrize(
    ("requested", "applied", "capped"),
    [
        (None, DEFAULT_RADIUS_M, False),
        (5_000, 5_000, False),
        (MAX_RADIUS_M, MAX_RADIUS_M, False),
        (5_000_000, MAX_RADIUS_M, True),
        (1, MIN_RADIUS_M, False),
    ],
)
def test_the_radius_is_clamped_and_says_when_it_was_capped(
    requested: int | None, applied: int, capped: bool
) -> None:
    """Asking for the whole country searches 50 km; asking for 1 m searches a GPS fix's error."""
    radius = clamp_radius(requested)
    assert radius.applied_m == applied
    assert radius.capped is capped
