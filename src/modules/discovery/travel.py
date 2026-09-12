"""A rough travel time from a straight-line distance (Issue 31).

Discovery shows "about 12 min by car" next to each clinic. It is **rough on purpose**: a routing
engine would need a network call per result (to a host the Content-Security-Policy does not allow
the browser to reach, and a cost per search on the server), and a patient choosing between three
clinics needs to know which is ten minutes away and which is an hour, not the exact figure. The map
(Issue 33) hands off to the phone's own directions app for the real route.

The model is three numbers, each stated where it is defined so a reviewer can argue with it:

* a **detour factor** turning the straight line into a road distance. Road networks are longer than
  the crow flies; 1.3 is the middle of the circuity ratios commonly measured for urban networks
  (roughly 1.2 in a grid, 1.4 and more where rivers, rail and townships' few entry roads intervene);
* a **walking speed** of 4.5 km/h, a steady adult pace allowing for crossings;
* a **driving speed** of 25 km/h, an urban average including traffic lights, congestion and the
  stops a minibus taxi makes, which is how most patients who are not walking travel.

Minutes are rounded **up**, and never below one: "0 min" to a clinic across the road reads as a
mistake, and an estimate that errs short sends someone out the door late.
"""

import math
from dataclasses import dataclass
from typing import Final

#: Road distance over straight-line distance. See the module docstring.
ROAD_DETOUR_FACTOR: Final = 1.3
#: A steady adult walking pace, in km/h.
WALKING_KMH: Final = 4.5
#: An urban average by car or minibus taxi, stops included, in km/h.
DRIVING_KMH: Final = 25.0

_METRES_PER_KM: Final = 1000.0
_MINUTES_PER_HOUR: Final = 60.0


@dataclass(frozen=True, slots=True)
class TravelEstimate:
    """How long the trip is likely to take, in whole minutes, both ways a patient usually travels."""

    walking_minutes: int
    driving_minutes: int


def _minutes(road_metres: float, kmh: float) -> int:
    """Whole minutes to cover ``road_metres`` at ``kmh``, rounded up, at least one."""
    hours = road_metres / _METRES_PER_KM / kmh
    return max(1, math.ceil(hours * _MINUTES_PER_HOUR))


def estimate_travel(distance_m: float) -> TravelEstimate:
    """Estimate the trip to a clinic ``distance_m`` away in a straight line.

    Args:
        distance_m: Straight-line distance in metres, as ``ST_Distance`` reports it.

    Returns:
        Walking and driving minutes.

    Raises:
        ValueError: If the distance is negative or not a finite number.
    """
    if not math.isfinite(distance_m) or distance_m < 0:
        raise ValueError(f"A distance is a finite number of metres, not {distance_m}.")
    road = distance_m * ROAD_DETOUR_FACTOR
    return TravelEstimate(
        walking_minutes=_minutes(road, WALKING_KMH),
        driving_minutes=_minutes(road, DRIVING_KMH),
    )
