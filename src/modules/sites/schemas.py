"""Request and response models for the sites API (Issue 23).

Separate ``In`` and ``Out`` shapes, like every other module: what an operator may set and what the
API returns are different sets of fields. Two decisions worth naming:

* **A coordinate crosses the wire as ``latitude`` / ``longitude``**, the order a person reads one,
  and is converted to :class:`~src.commons.geo.Coordinates` in exactly one place
  (:meth:`SiteLocationIn.to_coordinates`). PostGIS's ``POINT(lon lat)`` ordering never leaves
  :mod:`src.database.types`.
* **``status`` is not settable on create.** Every site starts at
  :data:`~src.commons.enums.SITE_DEFAULT_STATUS`, and moves through the verification workflow
  (Issue 29), so a client cannot post itself into ``verified``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.commons.enums import SaProvince, SiteSector, SiteStatus
from src.commons.geo import (
    CoordinateOutOfRangeError,
    Coordinates,
    assert_within_operating_area,
)

#: A slug is lowercase letters, digits and single hyphens: it appears in URLs and USSD menus.
SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
#: E.164, the one phone format this project stores.
PHONE_PATTERN = r"^\+[1-9]\d{6,14}$"


class SiteLocationIn(BaseModel):
    """A coordinate as a client sends it: latitude first, in degrees."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    def to_coordinates(self) -> Coordinates:
        """Return the validated value object, or raise for a point outside the operating country.

        Raises:
            ValueError: If the point is outside the operating area. Pydantic turns it into a 422
                carrying the message from :mod:`src.commons.geo`, which names the numbers read.
        """
        try:
            return assert_within_operating_area(
                Coordinates(latitude=self.latitude, longitude=self.longitude)
            )
        except CoordinateOutOfRangeError as exc:
            raise ValueError(str(exc)) from exc


class SiteLocationOut(BaseModel):
    """A coordinate as the API returns it."""

    latitude: float
    longitude: float

    @classmethod
    def of(cls, point: Coordinates) -> SiteLocationOut:
        """Build the response shape from the stored value object."""
        return cls(latitude=point.latitude, longitude=point.longitude)


class SiteIn(BaseModel):
    """The clinic profile an operator may set, on create and on update."""

    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=2, max_length=80, pattern=SLUG_PATTERN)
    sector: SiteSector
    location: SiteLocationIn
    address_line: str = Field(min_length=3, max_length=200)
    suburb: str | None = Field(default=None, max_length=120)
    city: str = Field(min_length=2, max_length=120)
    province: SaProvince
    postal_code: str | None = Field(default=None, max_length=10)
    phone_e164: str | None = Field(default=None, pattern=PHONE_PATTERN)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("location")
    @classmethod
    def _inside_the_operating_area(cls, value: SiteLocationIn) -> SiteLocationIn:
        """Refuse a location outside the operating country, with the reason (Issue 23)."""
        value.to_coordinates()
        return value


class SiteOut(BaseModel):
    """One clinic as the API returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    sector: SiteSector
    status: SiteStatus
    location: SiteLocationOut
    address_line: str
    suburb: str | None
    city: str
    province: SaProvince
    postal_code: str | None
    phone_e164: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    modified_at: datetime


class SiteListOut(BaseModel):
    """A page of clinics, plus the total the caller's scope and filters matched."""

    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    items: list[SiteOut]


class GeocodeIn(BaseModel):
    """An address to look up. Sent to ClinicQ, never from the browser to a geocoder."""

    address: str = Field(min_length=3, max_length=300)


class GeocodeCandidateOut(BaseModel):
    """One candidate the geocoder offered, for a person to accept or reject."""

    label: str
    location: SiteLocationOut


class GeocodeOut(BaseModel):
    """The candidates for one address, best first. Empty is a valid answer, not an error."""

    query: str
    candidates: list[GeocodeCandidateOut]
