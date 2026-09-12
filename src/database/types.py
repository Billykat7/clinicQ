"""Column types this project defines, and the one place ``POINT(lon lat)`` ordering is written.

:class:`PointGeography` stores a :class:`~src.commons.geo.Coordinates` as PostGIS
``geography(Point, 4326)`` and hands it back as the same value object, so no service ever holds a
raw WKB element or has to remember that PostGIS writes longitude first.

**Why a `TypeDecorator` rather than GeoAlchemy2's `Geography` directly.** Two reasons, both
practical:

* the **test suite runs on SQLite** (``tests/conftest.py``), which has no PostGIS and no
  SpatiaLite loaded, so a bare ``Geography`` column makes ``Base.metadata.create_all`` fail before
  a single test runs. ``load_dialect_impl`` renders the real geography type on PostgreSQL and plain
  text everywhere else, and the value round-trips identically on both;
* GeoAlchemy2 hangs DDL event listeners on tables whose columns *are* ``Geometry``/``Geography``
  instances (to emit ``AddGeometryColumn`` and manage its own spatial indexes). Wrapping the type
  keeps those listeners out of it, so the GiST index is the one this project declares, named by
  this project's naming convention, and visible in the migration rather than conjured at runtime.

The spatial index itself is **not** created by this type: it is declared on the model's
``__table_args__`` with ``postgresql_using="gist"`` and created by the migration, where a reviewer
can see it.
"""

from typing import Any

from geoalchemy2 import Geography
from geoalchemy2.shape import to_shape
from shapely import Point
from shapely import wkt as shapely_wkt
from sqlalchemy import Dialect, Text
from sqlalchemy.types import TypeDecorator, TypeEngine

from src.commons.geo import WGS84_SRID, Coordinates

#: The dialect that has PostGIS. Everything else stores the same value as EWKT text.
POSTGRESQL = "postgresql"


def point_ewkt(point: Coordinates) -> str:
    """Return ``SRID=4326;POINT(lon lat)`` for ``point``.

    The **only** place in the codebase that puts longitude before latitude: PostGIS takes a point
    as ``(x y)``, and x is longitude. Everything above this line says latitude first, the way a
    person reads a coordinate.
    """
    return f"SRID={WGS84_SRID};POINT({point.longitude} {point.latitude})"


class PointGeography(TypeDecorator[Coordinates]):
    """A WGS 84 point column: :class:`Coordinates` in Python, ``geography(Point, 4326)`` in PostGIS."""

    impl = Text
    cache_ok = True

    # GeoAlchemy2's DDL listeners unwrap a ``TypeDecorator`` (``_check_spatial_type`` calls
    # ``load_dialect_impl``), find the geography underneath and then read these two attributes
    # straight off the decorator — where ``TypeDecorator.__getattr__`` would proxy them to ``Text``
    # and raise. Declaring them here is the answer *and* the statement of intent: this type manages
    # no index of its own. The GiST index is the model's ``__table_args__`` and the migration's, so
    # it carries this project's naming convention and a reviewer can see it in the diff.
    spatial_index = False
    use_N_D_index = False  # noqa: N815 — GeoAlchemy2's own attribute name

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        """Render PostGIS geography on PostgreSQL, and plain text on any other dialect."""
        if dialect.name == POSTGRESQL:
            return dialect.type_descriptor(
                Geography(geometry_type="POINT", srid=WGS84_SRID, spatial_index=False)
            )
        return dialect.type_descriptor(Text())

    def process_bind_param(
        self, value: Coordinates | None, dialect: Dialect
    ) -> str | None:
        """Write a coordinate as EWKT, which both PostGIS and the text fallback accept."""
        if value is None:
            return None
        if not isinstance(value, Coordinates):
            raise TypeError(
                f"A location column takes src.commons.geo.Coordinates, not {type(value).__name__}."
            )
        return point_ewkt(value)

    def process_result_value(
        self, value: Any | None, dialect: Dialect
    ) -> Coordinates | None:
        """Read a coordinate back: PostGIS hands over WKB, the text fallback hands over EWKT."""
        if value is None:
            return None
        if isinstance(value, str):
            _, _, geometry = value.rpartition(";")  # drop any "SRID=4326;" prefix
            shape = shapely_wkt.loads(geometry)
        else:
            shape = to_shape(value)
        if not isinstance(shape, Point):
            raise ValueError(
                f"A location column holds a point; the database returned {shape.geom_type}."
            )
        return Coordinates(latitude=shape.y, longitude=shape.x)
