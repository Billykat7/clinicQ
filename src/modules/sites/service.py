"""Clinic persistence and the rules around it: everything that touches the database (Issue 23).

The router stays thin because the rules live here. Four of them:

* **A listing is narrowed by the caller's clinics, never by a request parameter.**
  :func:`list_sites` takes ``site_ids``; the router resolves it once with
  :func:`~src.core.site_scope.site_ids_in_scope`. ``None`` means a ``business``-tier grant (the
  platform admin's console); an **empty set** means no clinics, which is a real answer.
* **A slug is unique platform-wide**, and a clash is a 409 rather than an integrity error leaking
  out of the driver as a 500.
* **Deletes are soft.** A clinic's tickets, audit rows and reports outlive its listing.
* **The radius search lives here**, not in the router, because Issue 31 and the channel adapters
  will call it directly. :func:`sites_within_radius` is the query the GiST index exists for; it is
  PostGIS-only by construction and says so.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from src.commons.enums import (
    SITE_PUBLICLY_VISIBLE_STATUSES,
    BoundedContext,
    SiteSector,
    SiteStatus,
)
from src.commons.geo import WGS84_SRID, Coordinates
from src.commons.schemas import ModuleInfo
from src.database.models.site import Site
from src.database.types import point_ewkt
from src.modules.sites.schemas import SiteIn, SiteListOut, SiteLocationOut, SiteOut


class SlugAlreadyUsedError(ValueError):
    """Another clinic already holds this slug. Raised before the insert, answered as 409."""


def get_module_info() -> ModuleInfo:
    """Return this module's metadata for its ``/info`` endpoint."""
    return ModuleInfo(
        context=BoundedContext.SITES,
        summary="Clinics: profile, location, hours, queues, services, staff and display mode.",
    )


def site_out(site: Site) -> SiteOut:
    """One clinic in its API shape. The single place the stored point becomes two numbers."""
    return SiteOut(
        id=site.id,
        slug=site.slug,
        name=site.name,
        sector=SiteSector(site.sector),
        status=SiteStatus(site.status),
        location=SiteLocationOut.of(site.location),
        address_line=site.address_line,
        suburb=site.suburb,
        city=site.city,
        province=site.province_enum,
        postal_code=site.postal_code,
        phone_e164=site.phone_e164,
        notes=site.notes,
        is_active=site.is_active,
        created_at=site.created_at,
        modified_at=site.modified_at,
    )


def live_sites(site_ids: frozenset[str] | None) -> Select[tuple[Site]]:
    """The base query every read starts from: live clinics, narrowed to ``site_ids`` if given.

    ``None`` means "apply no narrowing" — a ``business``-tier caller. An **empty set** means no
    rows, which is a real answer and not the same thing; conflating the two is how a clinic
    manager ends up reading the whole platform.
    """
    statement = select(Site).where(Site.is_deleted.is_(False))
    if site_ids is not None:
        statement = statement.where(Site.id.in_(site_ids))
    return statement


def list_sites(
    db: Session,
    *,
    site_ids: frozenset[str] | None = None,
    sector: SiteSector | None = None,
    status: SiteStatus | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> SiteListOut:
    """Return a page of clinics the caller may see, by name.

    Args:
        db: The session.
        site_ids: The caller's clinics, or ``None`` for a business-tier grant.
        sector: Restrict to public or private.
        status: Restrict to one lifecycle status.
        query: Case-insensitive substring of the name or the suburb.
        limit: Page size.
        offset: Rows to skip.

    Returns:
        The page, with the total the filters matched.
    """
    base = live_sites(site_ids)
    if sector is not None:
        base = base.where(Site.sector == sector.value)
    if status is not None:
        base = base.where(Site.status == status.value)
    if query:
        pattern = f"%{query.strip().lower()}%"
        base = base.where(
            func.lower(Site.name).like(pattern) | func.lower(Site.suburb).like(pattern)
        )
    total = int(
        db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    )
    rows = (
        db.execute(base.order_by(Site.name, Site.id).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return SiteListOut(
        total=total,
        limit=limit,
        offset=offset,
        items=[site_out(row) for row in rows],
    )


def get_site(
    db: Session, site_id: str, *, site_ids: frozenset[str] | None = None
) -> Site | None:
    """Return one live clinic the caller's scope admits, or ``None``.

    ``None`` covers both "no such clinic" and "not your clinic" deliberately: the router turns it
    into the same 404, so a status code cannot be used to learn that an id exists (non-negotiable 3).
    """
    return db.execute(
        live_sites(site_ids).where(Site.id == site_id)
    ).scalar_one_or_none()


def get_site_by_slug(db: Session, slug: str) -> Site | None:
    """Return one live clinic by its public slug, whatever the caller's scope.

    Used by the surfaces that address a clinic by name rather than by id — a seed run, a channel
    menu, the public detail page. Authorization is the caller's job; this is a lookup.
    """
    return db.execute(live_sites(None).where(Site.slug == slug)).scalar_one_or_none()


def slug_is_free(db: Session, slug: str, *, excluding: str | None = None) -> bool:
    """Whether ``slug`` is available, ignoring the clinic being renamed."""
    statement = select(Site.id).where(Site.slug == slug)
    if excluding is not None:
        statement = statement.where(Site.id != excluding)
    return db.execute(statement).first() is None


def create_site(db: Session, payload: SiteIn) -> Site:
    """Insert a clinic at :data:`~src.commons.enums.SITE_DEFAULT_STATUS`. The caller commits.

    The status is not taken from the payload: a clinic becomes visible through the verification
    workflow (Issue 29), never by asking to be.

    It gets the default queue set and services catalogue, **exactly as a clinic that signs itself
    up does** (:func:`~src.modules.sites.onboarding.submit_registration`). Until Issue 223 the two
    paths differed: a clinic an operator typed in started with no rooms and no services, so its
    setup checklist opened on an empty clinic while a self-registered one opened on a working
    one — a difference nobody chose, and one the person setting the clinic up pays for. Both seeds
    are idempotent, so this is safe wherever it is called from.

    Raises:
        SlugAlreadyUsedError: If another clinic already holds the slug.
    """
    if not slug_is_free(db, payload.slug):
        raise SlugAlreadyUsedError(f"The slug {payload.slug!r} is already in use.")
    site = Site(
        slug=payload.slug,
        name=payload.name,
        sector=payload.sector.value,
        location=payload.location.to_coordinates(),
        address_line=payload.address_line,
        suburb=payload.suburb,
        city=payload.city,
        province=payload.province.value,
        postal_code=payload.postal_code,
        phone_e164=payload.phone_e164,
        notes=payload.notes,
    )
    db.add(site)
    db.flush()
    # Imported here rather than at module load: queues' service imports this one.
    from src.modules.queues.service import create_default_queues
    from src.modules.sites.catalogue import seed_default_catalogue

    create_default_queues(db, site.id)
    seed_default_catalogue(db, site.id)
    return site


def update_site(db: Session, site: Site, payload: SiteIn) -> Site:
    """Apply a profile update in place. The caller commits.

    ``status`` is untouched here for the same reason it is not settable on create.

    Raises:
        SlugAlreadyUsedError: If the new slug belongs to another clinic.
    """
    if payload.slug != site.slug and not slug_is_free(
        db, payload.slug, excluding=site.id
    ):
        raise SlugAlreadyUsedError(f"The slug {payload.slug!r} is already in use.")
    site.slug = payload.slug
    site.name = payload.name
    site.sector = payload.sector.value
    site.location = payload.location.to_coordinates()
    site.address_line = payload.address_line
    site.suburb = payload.suburb
    site.city = payload.city
    site.province = payload.province.value
    site.postal_code = payload.postal_code
    site.phone_e164 = payload.phone_e164
    site.notes = payload.notes
    db.flush()
    return site


def delete_site(db: Session, site: Site) -> None:
    """Soft-delete a clinic: the row stays, reads stop returning it. The caller commits."""
    site.is_deleted = True
    site.is_active = False
    db.flush()


# --------------------------------------------------------------------------------------
# The radius search the GiST index exists for (Issue 23, consumed by Issue 31)
# --------------------------------------------------------------------------------------


def within_radius_clause(centre: Coordinates, metres: float) -> Any:
    """``ST_DWithin(site.location, <centre>, <metres>)`` as a SQLAlchemy expression.

    Both operands are ``geography``, so the distance is metres on the spheroid and the planner can
    use ``ix_clinicq_site_location_gist``. Exposed separately from :func:`sites_within_radius` so a
    test can put it under ``EXPLAIN`` and read the plan rather than trusting the query.
    """
    centre_literal = func.ST_GeogFromText(
        f"SRID={WGS84_SRID};POINT({centre.longitude} {centre.latitude})"
    )
    return func.ST_DWithin(Site.location, centre_literal, metres)


def sites_within_radius(
    db: Session,
    centre: Coordinates,
    metres: float,
    *,
    publicly_visible_only: bool = True,
    limit: int = 50,
) -> list[tuple[Site, float]]:
    """Return live clinics within ``metres`` of ``centre``, nearest first, with their distance.

    **PostgreSQL only**: it calls PostGIS. The SQLite test database has no spatial functions, so
    the tests covering this are marked ``postgres`` and run against a real server.

    Args:
        db: The session.
        centre: Where the patient is.
        metres: The radius, in metres on the spheroid.
        publicly_visible_only: Keep only the statuses a patient may see
            (:data:`~src.commons.enums.SITE_PUBLICLY_VISIBLE_STATUSES`). An operator's map passes
            ``False`` to see draft and suspended clinics too.
        limit: How many to return.

    Returns:
        ``(site, distance in metres)`` pairs, nearest first.
    """
    centre_literal = func.ST_GeogFromText(point_ewkt(centre))
    distance = func.ST_Distance(Site.location, centre_literal)
    statement = (
        select(Site, distance)
        .where(Site.is_deleted.is_(False), Site.is_active.is_(True))
        .where(within_radius_clause(centre, metres))
        .order_by(distance)
        .limit(limit)
    )
    if publicly_visible_only:
        statement = statement.where(
            Site.status.in_([s.value for s in SITE_PUBLICLY_VISIBLE_STATUSES])
        )
    return [(site, float(metres_away)) for site, metres_away in db.execute(statement)]
