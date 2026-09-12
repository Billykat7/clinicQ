"""HTTP routes for discovery (Issue 31).

A thin adapter over :func:`~src.modules.discovery.service.find_nearby_sites`, and deliberately the
**same kind** of adapter the USSD and WhatsApp menus will be: it parses a request, calls the one
search, and renders the plain data it gets back. Nothing about which clinics match is decided here.

The routes are public: a patient looking for a clinic has no account, and what they are shown is
what verified clinics publish about themselves. Served under ``/api/v1/clinics`` like every other
kernel route (open decision 8 in ``docs/GITHUB/ISSUES/README.md``: the spec's ``/api/clinics`` means
this). Rate limiting against directory scraping arrives with Issue 38.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from src.commons.enums import SectorFilter
from src.commons.geo import CoordinateOutOfRangeError, Coordinates
from src.database.session import get_db
from src.modules.discovery import service
from src.modules.discovery.schemas import NearbyPageOut

router = APIRouter(prefix="/clinics", tags=["discovery"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/info", summary="Module metadata", operation_id="discoveryInfo")
def discovery_info() -> dict[str, str]:
    """Return discovery module metadata (unauthenticated, like every other ``/info``)."""
    info = service.get_module_info()
    return {"context": info.context.value, "summary": info.summary}


@router.get(
    "/nearby",
    response_model=NearbyPageOut,
    operation_id="discoveryNearby",
    summary="Verified clinics near a coordinate, nearest first",
)
def clinics_nearby(
    db: DbSession,
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitude, WGS 84.")],
    lon: Annotated[float, Query(ge=-180, le=180, description="Longitude, WGS 84.")],
    radius_m: Annotated[
        int,
        Query(
            ge=1,
            description=(
                "Search radius in metres. Larger values are capped by the server at "
                f"{service.MAX_RADIUS_M}; the response's `radius` says what was used."
            ),
        ),
    ] = service.DEFAULT_RADIUS_M,
    sector: Annotated[SectorFilter, Query()] = SectorFilter.ALL,
    open_now: Annotated[
        bool, Query(description="Only clinics open at the moment of the search.")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = (
        service.DEFAULT_PAGE_SIZE
    ),
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NearbyPageOut:
    """Search the directory from a coordinate. **No session**: see the module docstring."""
    try:
        origin = Coordinates(latitude=lat, longitude=lon)
        result = service.find_nearby_sites(
            db,
            origin,
            radius_m=radius_m,
            sector=sector,
            open_now=open_now,
            limit=limit,
            offset=offset,
        )
    except CoordinateOutOfRangeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return NearbyPageOut.of(result)
