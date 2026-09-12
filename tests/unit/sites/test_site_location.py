"""A clinic's coordinate is a value, validated once, and never silently swapped (Issue 23).

Pure logic, so unit tests: the bounding box, the value object, and the one place latitude and
longitude are written in PostGIS's order.
"""

from __future__ import annotations

import pytest

from scripts.db.demo_dataset import CLINICS
from src.commons.geo import (
    MAX_LATITUDE,
    CoordinateOutOfRangeError,
    Coordinates,
    assert_within_operating_area,
    within_operating_area,
)
from src.database.types import point_ewkt


def test_a_coordinate_that_is_not_a_point_on_earth_cannot_be_built() -> None:
    """Degrees are checked on construction, so an impossible pair never reaches a column."""
    with pytest.raises(CoordinateOutOfRangeError, match="Latitude"):
        Coordinates(latitude=-91.0, longitude=28.0)
    with pytest.raises(CoordinateOutOfRangeError, match="Longitude"):
        Coordinates(latitude=-26.0, longitude=181.0)


@pytest.mark.parametrize("clinic", CLINICS, ids=lambda c: c.slug)
def test_every_real_clinic_falls_inside_the_operating_area(clinic: object) -> None:
    """The box is generous enough for the whole demo dataset: it rejects mistakes, not addresses."""
    point = Coordinates(latitude=clinic.latitude, longitude=clinic.longitude)  # type: ignore[attr-defined]
    assert within_operating_area(point)


def test_null_island_is_refused_with_a_message_that_says_what_to_check() -> None:
    """``0, 0`` is what an unset pair of floats looks like, and it is in the Gulf of Guinea."""
    with pytest.raises(CoordinateOutOfRangeError) as refusal:
        assert_within_operating_area(Coordinates(latitude=0.0, longitude=0.0))
    message = str(refusal.value)
    assert "South Africa" in message
    assert "swapped" in message and "negative" in message
    assert "0.00000, 0.00000" in message


def test_a_swapped_pair_is_refused_rather_than_stored_in_the_sea() -> None:
    """Hillbrow with its numbers the wrong way round is off the coast of Somalia, and is refused."""
    hillbrow = CLINICS[0]
    swapped = Coordinates(latitude=hillbrow.longitude, longitude=hillbrow.latitude)
    assert not within_operating_area(swapped)
    with pytest.raises(CoordinateOutOfRangeError):
        assert_within_operating_area(swapped)


def test_a_positive_latitude_inside_the_longitude_range_is_still_refused() -> None:
    """A dropped minus sign puts a Johannesburg clinic in Ukraine; the box catches that too."""
    with pytest.raises(CoordinateOutOfRangeError):
        assert_within_operating_area(Coordinates(latitude=26.19355, longitude=28.04540))
    assert MAX_LATITUDE < 0, "the operating area is entirely south of the equator"


def test_the_only_place_longitude_comes_first_is_the_ewkt_writer() -> None:
    """PostGIS takes ``POINT(x y)`` and x is longitude; everything above that reads latitude first."""
    point = Coordinates(latitude=-26.19355, longitude=28.04540)
    assert point_ewkt(point) == "SRID=4326;POINT(28.0454 -26.19355)"
    assert str(point) == "-26.19355, 28.04540"
