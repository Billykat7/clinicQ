"""The PostGIS half of Issue 23, against a real PostgreSQL 18 + PostGIS server.

Two things SQLite cannot prove, and the reason the column and its index land in M4 rather than M5:

* ``ST_DWithin`` on the ``geography`` column returns the clinics inside a radius, in metres on the
  spheroid, with the distance the discovery API (Issue 31) will report;
* **the GiST index is used**, asserted from the query plan rather than from the fact that an index
  exists. ``EXPLAIN`` is read, not eyeballed.

Each test starts from a database that did not exist a moment ago and runs the real migrations, so
what is proven is what ``alembic upgrade head`` produces.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from scripts.db.demo_dataset import CLINICS
from src.commons.enums import SiteStatus
from src.commons.geo import Coordinates
from src.modules.sites.service import sites_within_radius, within_radius_clause
from tests.factories import SiteFactory

pytestmark = pytest.mark.postgres

#: The Johannesburg city centre, a few hundred metres from Hillbrow CHC.
_JOHANNESBURG = Coordinates(latitude=-26.20500, longitude=28.04000)
#: The name the model and migration give the spatial index; the plan has to mention it.
_GIST_INDEX = "ix_clinicq_site_location_gist"


@pytest.fixture
def seeded(migrated_engine: Engine) -> Engine:
    """Every demo clinic, verified, in a freshly migrated database."""
    with Session(migrated_engine) as db:
        for clinic in CLINICS:
            SiteFactory.create(
                db,
                slug=clinic.slug,
                name=clinic.name,
                sector=clinic.sector,
                location=Coordinates(
                    latitude=clinic.latitude, longitude=clinic.longitude
                ),
                address_line=f"{clinic.name}, {clinic.suburb}",
                suburb=clinic.suburb,
                city=clinic.city,
                province=clinic.province,
                status=SiteStatus.VERIFIED,
            )
        db.commit()
    return migrated_engine


def test_a_site_persists_with_a_real_coordinate_and_comes_back_from_st_dwithin(
    seeded: Engine,
) -> None:
    """The acceptance criterion: a stored clinic is found by a radius query, with its distance."""
    with Session(seeded) as db:
        near = sites_within_radius(db, _JOHANNESBURG, metres=2_000)

    slugs = [site.slug for site, _ in near]
    assert "hillbrow-chc" in slugs
    # Nearest first, and the distance is metres: Hillbrow CHC is about a kilometre from the centre.
    assert near[0][1] < 2_000
    assert near == sorted(near, key=lambda pair: pair[1])
    # Soweto is 20 km away and is not in a 2 km radius.
    assert "mandela-sisulu-clinic" not in slugs


def test_the_radius_is_metres_on_the_spheroid_not_degrees(seeded: Engine) -> None:
    """A geography column measures in metres, so widening the radius adds the clinics it should."""
    with Session(seeded) as db:
        close = {site.slug for site, _ in sites_within_radius(db, _JOHANNESBURG, 2_000)}
        wide = {site.slug for site, _ in sites_within_radius(db, _JOHANNESBURG, 30_000)}
    assert close < wide
    assert "mandela-sisulu-clinic" in wide  # Orlando West, about 20 km out


def test_a_clinic_that_is_not_verified_is_not_returned_to_a_patient(
    seeded: Engine,
) -> None:
    """Discovery's rule, held in the query rather than in the caller (Issue 29 moves the status)."""
    with Session(seeded) as db:
        site = SiteFactory.create(
            db,
            slug="not-yet-checked",
            name="Not Yet Checked",
            location=Coordinates(latitude=-26.20400, longitude=28.04100),
            status=SiteStatus.PENDING_VERIFICATION,
        )
        db.commit()
        public = {s.slug for s, _ in sites_within_radius(db, _JOHANNESBURG, 2_000)}
        operator = {
            s.slug
            for s, _ in sites_within_radius(
                db, _JOHANNESBURG, 2_000, publicly_visible_only=False
            )
        }
    assert site.slug not in public
    assert site.slug in operator


def test_the_gist_index_exists_on_the_location_column(seeded: Engine) -> None:
    """It is in the migration, so it is there before a single clinic is: read it from the catalog."""
    with seeded.connect() as conn:
        definition = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": _GIST_INDEX},
        ).scalar_one()
    assert "USING gist" in definition
    assert "location" in definition


def test_the_radius_query_plan_actually_uses_the_gist_index(seeded: Engine) -> None:
    """The criterion that matters: ``EXPLAIN`` names the index, rather than a person assuming it.

    ``enable_seqscan`` is turned off for this transaction on purpose. With a few dozen demo clinics a
    sequential scan is genuinely the cheaper plan, and the planner is right to choose it — what has
    to be proven here is that the index is **usable** for this predicate, which is exactly what a
    plan produced with the sequential scan priced out shows. Both settings are ``SET LOCAL``, so
    they end with the transaction and no other test sees them.
    """
    statement = (
        "EXPLAIN (FORMAT TEXT) SELECT id FROM clinicq.site WHERE "
        "ST_DWithin(location, ST_GeogFromText(:centre), :metres)"
    )
    with seeded.begin() as conn:
        conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                text(statement),
                {
                    "centre": (
                        f"SRID=4326;POINT({_JOHANNESBURG.longitude} "
                        f"{_JOHANNESBURG.latitude})"
                    ),
                    "metres": 2_000,
                },
            )
        )

    assert _GIST_INDEX in plan, plan
    assert "Index Scan" in plan or "Bitmap Index Scan" in plan, plan
    assert "Seq Scan" not in plan, plan


def test_the_same_predicate_is_what_the_service_builds(seeded: Engine) -> None:
    """The plan above is not a hand-written query that has drifted from the service's.

    ``within_radius_clause`` is compiled to SQL and compared with what ``EXPLAIN`` was given, so a
    change to the service either keeps this test honest or fails it.
    """
    compiled = str(
        within_radius_clause(_JOHANNESBURG, 2_000).compile(
            seeded, compile_kwargs={"literal_binds": True}
        )
    )
    assert "ST_DWithin" in compiled
    assert "ST_GeogFromText" in compiled
    assert "site.location" in compiled
