"""HTTP routes for discovery (Issues 31 and 34).

Thin adapters over the discovery services, and deliberately the **same kind** of adapter the USSD
and WhatsApp menus will be: each parses a request, calls the one service function, and renders the
plain data it gets back. Nothing about which clinics or which places match is decided here.

* ``GET /clinics/nearby`` searches from a position **or** from an area (``area_id``), never both;
* ``GET /clinics/areas`` is the place-name typeahead a patient without GPS uses;
* ``GET /clinics/areas/recent`` and ``PUT /clinics/areas/recent/{area_id}`` are a signed-in
  patient's recently used areas, behind the ``patient`` role's grant on ``patients.self``.

The search routes are public: a patient looking for a clinic has no account, and what they are shown
is what verified clinics publish about themselves and a public place-name dataset. Served under
``/api/v1/`` like every other kernel route (open decision 8). Rate limiting against directory
scraping arrives with Issue 38.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from src.api.rbac_deps import require_patient
from src.commons.enums import SaProvince, SectorFilter
from src.commons.geo import CoordinateOutOfRangeError, Coordinates
from src.database.models import Patient
from src.database.session import get_db
from src.modules.discovery import areas, service
from src.modules.discovery.schemas import AreaListOut, AreaOut, NearbyPageOut

router = APIRouter(prefix="/clinics", tags=["discovery"])

DbSession = Annotated[Session, Depends(get_db)]
#: A signed-in patient reading or changing their own recently used areas.
OwnRecordRead = Annotated[Patient, Depends(require_patient("patients.self", "read"))]
OwnRecordUpdate = Annotated[
    Patient, Depends(require_patient("patients.self", "update"))
]

#: Said when a search names no origin, or two.
ONE_ORIGIN = "Search from a position (lat and lon) or from an area (area_id), not both."


@router.get("/info", summary="Module metadata", operation_id="discoveryInfo")
def discovery_info() -> dict[str, str]:
    """Return discovery module metadata (unauthenticated, like every other ``/info``)."""
    info = service.get_module_info()
    return {"context": info.context.value, "summary": info.summary}


@router.get(
    "/nearby",
    response_model=NearbyPageOut,
    operation_id="discoveryNearby",
    summary="Verified clinics near a position or an area, nearest first",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No area has that id."},
    },
)
def clinics_nearby(
    db: DbSession,
    lat: Annotated[
        float | None, Query(ge=-90, le=90, description="Latitude, WGS 84.")
    ] = None,
    lon: Annotated[
        float | None, Query(ge=-180, le=180, description="Longitude, WGS 84.")
    ] = None,
    area_id: Annotated[
        str | None,
        Query(
            max_length=36,
            description=(
                "Search from this area's centroid instead of a position (Issue 34). Distances are "
                "then approximate, and every result says so."
            ),
        ),
    ] = None,
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
    """Search the directory. **No session**: see the module docstring."""
    has_position = lat is not None and lon is not None
    if has_position == (area_id is not None) or (lat is None) != (lon is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, ONE_ORIGIN)
    try:
        origin: service.SearchOrigin = (
            service.AreaOrigin(area_id=area_id)
            if area_id is not None
            else Coordinates(latitude=lat, longitude=lon)  # type: ignore[arg-type]
        )
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
    except areas.AreaNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such area.") from exc
    return NearbyPageOut.of(result)


@router.get(
    "/areas",
    response_model=AreaListOut,
    operation_id="discoveryAreaSearch",
    summary="Suggest the places a typed suburb, township or town name means",
)
def area_search(
    db: DbSession,
    q: Annotated[
        str,
        Query(
            max_length=100,
            description="What the patient typed. Misspellings and alternative names are tolerated.",
        ),
    ],
    limit: Annotated[int, Query(ge=1, le=areas.MAX_SUGGESTIONS)] = (
        areas.DEFAULT_SUGGESTIONS
    ),
    province: Annotated[SaProvince | None, Query()] = None,
) -> AreaListOut:
    """The typeahead. Fewer than two letters or digits answers an empty list, not an error."""
    return AreaListOut(
        items=[
            AreaOut.of(area)
            for area in areas.search_areas(db, q, limit=limit, province=province)
        ]
    )


@router.get(
    "/areas/recent",
    response_model=AreaListOut,
    operation_id="discoveryRecentAreas",
    summary="The signed-in patient's recently used areas, newest first",
)
def my_recent_areas(patient: OwnRecordRead, db: DbSession) -> AreaListOut:
    """Each item's ``id`` goes straight into ``/clinics/nearby?area_id=``: one interaction."""
    return AreaListOut(
        items=[AreaOut.of(area) for area in areas.recent_areas(db, patient.id)]
    )


@router.put(
    "/areas/recent/{area_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    operation_id="discoveryRememberArea",
    summary="Remember that the signed-in patient searched from this area",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No area has that id."}},
)
def remember_my_area(area_id: str, patient: OwnRecordUpdate, db: DbSession) -> Response:
    """Move ``area_id`` to the top of the patient's recent areas, keeping only the latest few."""
    try:
        areas.remember_area(db, patient.id, area_id)
    except areas.AreaNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such area.") from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
