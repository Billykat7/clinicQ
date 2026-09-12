"""Which clinics a patient may be shown (Issue 29; Issue 31 builds the API on it).

One function, and it exists a milestone early for one reason: **an unverified clinic must never
appear in a discovery search**, and the only way to prove that is to have a search to prove it
against. So :func:`search_sites` is the narrowing every patient-facing surface goes through —
discovery (Issue 31), the clinic detail page (Issue 35), the USSD and WhatsApp menus (M10) — and
the test for Issue 29's criterion runs against it rather than against a page.

The rule is a single ``where`` clause, and it is the same one everywhere:

* **status** must be one the platform has published (``verified``: see
  :data:`~src.commons.enums.SITE_PUBLICLY_VISIBLE_STATUSES`). A draft clinic, one waiting for a
  platform admin and a suspended one are all invisible here;
* the row must be **live and active** (not soft-deleted, not switched off).

A clinic that is not visible here is still reachable **by direct link** for the people who need to
check it: the clinic itself, and the platform admin deciding about it. That is
:func:`src.modules.sites.service.get_site` behind the site guard, and it is deliberately a different
question from this one.
"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from src.commons.enums import SiteSector
from src.commons.geo import Coordinates
from src.core.site_scope import publicly_visible_site_clauses
from src.database.models.site import Site
from src.database.types import point_ewkt

#: How many results a patient-facing search returns by default. A phone screen, not a console.
DEFAULT_LIMIT = 20


def publicly_visible() -> Select[tuple[Site]]:
    """The base query every patient-facing read of ``site`` starts from.

    The rule itself is :func:`~src.core.site_scope.publicly_visible_site_clauses`, written once
    beside the site guard. A surface that builds its own ``select(Site)`` and forgets one of those
    clauses is the bug Issue 29 exists to prevent, so anything patient-facing calls this rather
    than repeating it.
    """
    return select(Site).where(*publicly_visible_site_clauses())


def search_sites(
    db: Session,
    *,
    query: str | None = None,
    sector: SiteSector | None = None,
    near: Coordinates | None = None,
    within_metres: float | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Site]:
    """The clinics a patient may be shown, nearest first when a position is given.

    Args:
        db: The session.
        query: Case-insensitive substring of the name, suburb or city.
        sector: Restrict to public facilities or private practices.
        near: Where the patient is. Requires PostGIS, so it is PostgreSQL-only.
        within_metres: The radius around ``near``; ignored without it.
        limit: How many to return.

    Returns:
        The matching clinics. **Never** one that is not verified, whatever the other filters say.
    """
    statement = publicly_visible()
    if sector is not None:
        statement = statement.where(Site.sector == sector.value)
    if query:
        pattern = f"%{query.strip().lower()}%"
        statement = statement.where(
            func.lower(Site.name).like(pattern)
            | func.lower(Site.suburb).like(pattern)
            | func.lower(Site.city).like(pattern)
        )
    if near is not None:
        centre = func.ST_GeogFromText(point_ewkt(near))
        if within_metres is not None:
            statement = statement.where(
                func.ST_DWithin(Site.location, centre, within_metres)
            )
        statement = statement.order_by(func.ST_Distance(Site.location, centre))
    else:
        statement = statement.order_by(Site.name)
    return list(db.execute(statement.limit(limit)).scalars())
