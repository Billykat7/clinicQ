"""Areas: the suburbs, townships and towns a patient without GPS types instead (Issue 34).

GPS is unavailable more often than a smartphone-first design assumes: a declined permission, a phone
indoors, an old handset, and every USSD session, which has no location at all. So discovery can
start from a **place name** as well as a position, and these tables are the places.

* :class:`Area` is one place: its name, what kind of place it is, the municipality and province it
  is in, and its **centroid**, which is what a search measures from. It is reference data, seeded by
  migration ``0014`` from OpenStreetMap (the source and licence are in that migration's docstring)
  and never edited by the application.
* :class:`AreaName` is every name a place is searched by: its own, and its alternatives ("Tembisa"
  for Thembisa, "Joburg" for Johannesburg). Each row carries a ``search_key``, the name folded to
  lowercase letters and digits (:func:`src.modules.discovery.areas.search_key`), with a trigram
  index over it so a misspelling still finds the place.
* :class:`PatientRecentArea` is the handful of areas one patient last searched from, so choosing one
  again is a single tap or a single digit.

The trigram index is PostgreSQL's (``pg_trgm``), created by the migration. The SQLite test database
builds these tables without it, and the fuzzy search is tested against PostgreSQL.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.commons.enums import AreaKind, DbSchema, SaProvince
from src.commons.geo import Coordinates
from src.commons.ids import new_id
from src.database.models.base import Base
from src.database.models.mixins import TimestampMixin
from src.database.types import PointGeography

SCHEMA = DbSchema.CLINICQ.value


class Area(Base, TimestampMixin):
    """One named place a search can start from: a suburb, township, town or city."""

    __tablename__ = "area"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    osm_ref: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    """Where the place was read from: ``node/<id>`` in OpenStreetMap. The dataset's natural key."""
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    """The name the place goes by, as OpenStreetMap spells it."""
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    """:class:`~src.commons.enums.AreaKind`."""
    municipality: Mapped[str | None] = mapped_column(String(160), nullable=True)
    """The local or metropolitan municipality, which tells apart the four Riversides."""
    province: Mapped[str] = mapped_column(String(32), nullable=False)
    """:class:`~src.commons.enums.SaProvince`."""
    centroid: Mapped[Coordinates] = mapped_column(PointGeography, nullable=False)
    """The place's point in OpenStreetMap: what a search from this area measures from."""

    @property
    def kind_enum(self) -> AreaKind:
        """``kind`` as its enum member."""
        return AreaKind(self.kind)

    @property
    def province_enum(self) -> SaProvince:
        """``province`` as its enum member."""
        return SaProvince(self.province)

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"Area(id={self.id!r}, name={self.name!r})"


class AreaName(Base, TimestampMixin):
    """One name an area is found by: its own, or an alternative spelling or name."""

    __tablename__ = "area_name"
    __table_args__ = (
        UniqueConstraint(
            "area_id", "search_key", name="uq_area_name_area_id_search_key"
        ),
        # The typeahead: trigram similarity and prefix LIKE both use a GIN trigram index.
        Index(
            "ix_clinicq_area_name_search_key_trgm",
            "search_key",
            postgresql_using="gin",
            postgresql_ops={"search_key": "gin_trgm_ops"},
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    area_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.area.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    """The name as written: "Tembisa", "Joburg"."""
    search_key: Mapped[str] = mapped_column(String(160), nullable=False)
    """The name folded for matching: lowercase ASCII letters and digits only."""
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    """True for the area's own name, false for an alternative."""


class PatientRecentArea(Base):
    """An area one patient searched from, and when they last did (Issue 34).

    Suburb-level, never a position: it records "Soweto", not where in Soweto. Kept to a handful per
    patient by :func:`src.modules.discovery.areas.remember_area`, and deleted with the patient.
    """

    __tablename__ = "patient_recent_area"
    __table_args__ = (
        UniqueConstraint(
            "patient_id", "area_id", name="uq_patient_recent_area_patient_id"
        ),
        Index("ix_clinicq_patient_recent_area_patient_used", "patient_id", "used_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{SCHEMA}.patient.id", ondelete="CASCADE"),
        nullable=False,
    )
    area_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.area.id", ondelete="CASCADE"), nullable=False
    )
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """When the patient last searched from this area (Africa/Johannesburg)."""
