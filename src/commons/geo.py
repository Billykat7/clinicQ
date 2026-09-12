"""Coordinates, and the box a ClinicQ coordinate has to fall inside (Issue 23).

A clinic's position is the one field discovery is built on (Issue 31), so it is worth being strict
about twice:

* **A coordinate is a value, not two loose floats.** :class:`Coordinates` is what the model, the
  schemas and the geocoder all pass around, so latitude and longitude can never be swapped by
  arriving in the wrong argument position — the mistake that puts a Johannesburg clinic in the
  Indian Ocean, and the reason ``POINT(lon lat)`` ordering is confined to
  :mod:`src.database.types`.
* **"Somewhere on Earth" is not good enough.** ``0, 0`` is in the Gulf of Guinea and is what an
  unset pair of floats looks like, so :func:`assert_within_operating_area` refuses anything outside
  the operating country's bounding box with a message naming what was wrong.

The box is South Africa's, generously rounded outwards from the country's extremes so that no real
address is refused: it exists to catch a swapped pair, a missing minus sign and an unset default,
not to trace a border.
"""

from dataclasses import dataclass
from typing import Final

#: The operating country. One place to change when ClinicQ opens somewhere else.
OPERATING_COUNTRY: Final = "South Africa"

#: South Africa's bounding box, rounded outwards: roughly Cape Agulhas to the Limpopo, and the
#: Atlantic coast to the Mozambique border. The Prince Edward Islands are deliberately outside it.
MIN_LATITUDE: Final = -35.5
MAX_LATITUDE: Final = -21.5
MIN_LONGITUDE: Final = 15.5
MAX_LONGITUDE: Final = 33.5

#: The SRID every coordinate in this project is expressed in: WGS 84, what a phone's GPS reports.
WGS84_SRID: Final = 4326


class CoordinateOutOfRangeError(ValueError):
    """A coordinate is not a point on Earth, or not a point in the operating country."""


@dataclass(frozen=True, slots=True)
class Coordinates:
    """One WGS 84 position: ``latitude`` north-positive, ``longitude`` east-positive.

    Validated on construction against the whole globe, so an impossible pair cannot exist even in
    memory. The narrower operating-area check is :func:`assert_within_operating_area`, kept
    separate because it is a business rule (where ClinicQ runs) rather than a fact about degrees.
    """

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        """Refuse a pair that is not a point on Earth at all."""
        if not -90.0 <= self.latitude <= 90.0:
            raise CoordinateOutOfRangeError(
                f"Latitude {self.latitude} is not between -90 and 90."
            )
        if not -180.0 <= self.longitude <= 180.0:
            raise CoordinateOutOfRangeError(
                f"Longitude {self.longitude} is not between -180 and 180."
            )

    def __str__(self) -> str:
        """``-26.19355, 28.04540``: latitude first, the order a person writes one."""
        return f"{self.latitude:.5f}, {self.longitude:.5f}"


def within_operating_area(point: Coordinates) -> bool:
    """Whether ``point`` falls inside the operating country's bounding box."""
    return (
        MIN_LATITUDE <= point.latitude <= MAX_LATITUDE
        and MIN_LONGITUDE <= point.longitude <= MAX_LONGITUDE
    )


def assert_within_operating_area(point: Coordinates) -> Coordinates:
    """Return ``point``, or raise :class:`CoordinateOutOfRangeError` naming what is wrong.

    The message says the country and the box rather than "invalid location", because the two
    mistakes this catches (a swapped pair, and an unset ``0, 0``) are both fixed by seeing the
    numbers that were actually read.

    Args:
        point: The candidate position.

    Returns:
        The same point, unchanged, so this reads well inline.

    Raises:
        CoordinateOutOfRangeError: If the point is outside the operating country.
    """
    if within_operating_area(point):
        return point
    raise CoordinateOutOfRangeError(
        f"{point} is outside {OPERATING_COUNTRY} "
        f"(latitude {MIN_LATITUDE}..{MAX_LATITUDE}, longitude {MIN_LONGITUDE}..{MAX_LONGITUDE}). "
        "Check that latitude and longitude are not swapped, and that the latitude is negative."
    )
