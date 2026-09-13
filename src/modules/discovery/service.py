"""The nearby-clinics search every channel calls (Issue 31).

**One implementation, four doors.** The web list (Issue 32), the map (Issue 33), the USSD menu
(Issue 73) and the WhatsApp bot (Issue 75) all answer "which clinics are near me?" by calling
:func:`find_nearby_sites`. It returns frozen dataclasses (plain data, never HTML and never a
Pydantic response model), so a channel adapter renders the answer its own way and never has a
reason to write a second search. A second search in an adapter is the failure this module exists to
prevent: the day the web page and the USSD menu disagree about which clinics are open is the day a
patient stops trusting both.

What a search does, in order:

1. **Caps the radius on the server** (:func:`clamp_radius`). A caller may ask for 5,000 km; the query
   runs with :data:`MAX_RADIUS_M`, and the answer says so, so a scraper cannot pull the whole
   country in one request and a legitimate client can tell it was capped.
2. **Narrows to clinics a patient may see**, through the one rule in
   :func:`~src.core.site_scope.publicly_visible_site_clauses`: live, active and ``verified``.
3. **Filters by radius with ``ST_DWithin`` on the geography column**, which the GiST index
   ``ix_clinicq_site_location_gist`` serves, and orders by ``ST_Distance``. :func:`nearby_statement`
   builds the statement, so a test can put the exact query the service runs under ``EXPLAIN``.
4. **Starts from a position or from an area** (Issue 34). An :class:`AreaOrigin` is resolved to the
   area's centroid, and every distance in the answer is then marked approximate
   (:class:`~src.commons.enums.DistanceBasis`) and labelled so by :mod:`.wording`.
5. **Joins what a patient decides on**: whether the clinic is open now and when it next opens
   (Issue 24's pure functions over :func:`~src.modules.sites.hours.published_schedules`), a rough
   travel time (:mod:`.travel`), and the live queue lengths
   (:func:`~src.modules.queues.live.published_live_queues`).

A page of twenty results costs a fixed number of queries whatever the directory's size: one for the
clinics, four for their schedules and two for their queues. That, and the index, is what keeps it
under the 200 ms budget with 500 clinics, which ``tests/integration/discovery/`` times.

**PostgreSQL only.** ``ST_DWithin`` is PostGIS, so the tests for this module are marked
``postgres`` and run against a real server.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Select, exists, func, select
from sqlalchemy.orm import Session

from src.commons.enums import (
    BoundedContext,
    DiscoverySort,
    DistanceBasis,
    MedicalAidScheme,
    SaProvince,
    SectorFilter,
    SiteSector,
)
from src.commons.geo import Coordinates, assert_within_operating_area
from src.commons.schemas import ModuleInfo
from src.commons.time import business_date, now_sast
from src.core.config import get_settings
from src.core.site_scope import publicly_visible_site_clauses
from src.database.models import SitePaymentMedicalAid, SitePaymentProfile
from src.database.models.site import Site
from src.database.types import point_ewkt
from src.modules.discovery.areas import AreaSummary, get_area
from src.modules.discovery.travel import TravelEstimate, estimate_travel
from src.modules.discovery.wording import distance_label
from src.modules.queue.snapshot import cached_waiting_counts
from src.modules.queues.live import (
    LiveQueue,
    WaitingCountReader,
    published_live_queues,
    total_waiting,
)
from src.modules.sites.hours import OpeningSchedule, open_state, published_schedules
from src.modules.sites.payment_profile import PaymentProfile, published_profiles
from src.modules.sites.service import within_radius_clause

#: The radius a search uses when the caller names none: the "within 10 km" of the empty state.
DEFAULT_RADIUS_M: Final = 10_000
#: The widest radius the server will search, whatever it is asked for. 50 km covers a metro and
#: its surrounding townships, which is as far as anyone travels to a clinic for a queue; beyond it
#: the only caller is one trying to copy the directory.
MAX_RADIUS_M: Final = 50_000
#: The narrowest radius worth running. Below it a GPS fix's own error is larger than the circle.
MIN_RADIUS_M: Final = 100

#: Results per page by default: a phone screen, not a console.
DEFAULT_PAGE_SIZE: Final = 20
#: The largest page a caller may ask for.
MAX_PAGE_SIZE: Final = 50
#: With ``open_now`` or the shortest-queue sort, how many of the nearest clinics are examined before
#: paginating. Opening hours are evaluated in Python (Issue 24's rules do not reduce to SQL) and queue
#: lengths come from a reader, so both run over a bounded candidate set rather than the whole radius;
#: 500 is the whole seeded directory in the performance test and far more than a 50 km radius holds
#: outside the three metros.
MAX_CANDIDATES: Final = 500


def get_module_info() -> ModuleInfo:
    """Return this module's metadata for its ``/info`` endpoint."""
    return ModuleInfo(
        context=BoundedContext.DISCOVERY,
        summary="Find verified clinics near a place: distance, travel time, open now, queue length.",
    )


@dataclass(frozen=True, slots=True)
class SearchRadius:
    """The radius a caller asked for, and the one the search actually used."""

    requested_m: int
    applied_m: int

    @property
    def capped(self) -> bool:
        """Whether the server narrowed the request to :data:`MAX_RADIUS_M`."""
        return self.applied_m < self.requested_m


def clamp_radius(requested_m: int | None) -> SearchRadius:
    """Bring a requested radius into ``[MIN_RADIUS_M, MAX_RADIUS_M]``.

    Capping rather than refusing is deliberate: a patient's app that asks for 100 km should still
    get the clinics within 50 km, and the answer reports that it was capped.
    """
    requested = DEFAULT_RADIUS_M if requested_m is None else requested_m
    return SearchRadius(
        requested_m=requested,
        applied_m=min(MAX_RADIUS_M, max(MIN_RADIUS_M, requested)),
    )


@dataclass(frozen=True, slots=True)
class AreaOrigin:
    """Search from the middle of a named place instead of from a position (Issue 34).

    What a patient without GPS searches from: the area they picked from
    :func:`~src.modules.discovery.areas.search_areas`. Distances from it are approximate.
    """

    area_id: str


#: Where a search starts: a position, or an area.
SearchOrigin = Coordinates | AreaOrigin


@dataclass(frozen=True, slots=True)
class OpenStatus:
    """Open or closed at the moment of the search, with what a patient needs next."""

    is_open: bool
    #: When it next opens; the moment of the search itself when it is open. ``None`` when it does
    #: not open again within Issue 24's horizon (closed until further notice, or no hours set).
    next_open_at: datetime | None
    #: The manager's reason, only when an ad-hoc closure is what keeps it shut.
    closure_reason: str | None


@dataclass(frozen=True, slots=True)
class NearbyClinic:
    """One search result: the clinic, how far it is, and what it is like right now."""

    site_id: str
    slug: str
    name: str
    sector: SiteSector
    location: Coordinates
    address_line: str
    suburb: str | None
    city: str
    province: SaProvince
    phone_e164: str | None
    #: Straight-line distance from the search origin, in whole metres.
    distance_m: int
    #: What the distance was measured from. From an area's centroid it is approximate.
    distance_basis: DistanceBasis
    #: The distance in words, approximate where it must be (:mod:`.wording`).
    distance_label: str
    travel: TravelEstimate
    open_status: OpenStatus
    #: Active queues in the clinic's own display order, each with its live length.
    queues: tuple[LiveQueue, ...]
    #: What a private clinic reports accepting (Issue 37); ``None`` for a public clinic, one that has
    #: declared nothing, or while the feature is off. Shown only with its notice.
    payment: PaymentProfile | None = None

    @property
    def total_waiting(self) -> int | None:
        """Everyone waiting across the clinic's queues, or ``None`` when that is not measured."""
        return total_waiting(self.queues)


@dataclass(frozen=True, slots=True)
class NearbyResult:
    """One page of a search, with everything needed to say what was searched."""

    #: The point distances were measured from: the position, or the area's centroid.
    origin: Coordinates
    #: The area searched from, when the search started from one.
    origin_area: AreaSummary | None
    distance_basis: DistanceBasis
    radius: SearchRadius
    sector: SectorFilter
    open_now: bool
    sort: DiscoverySort
    #: How many clinics matched in all, across every page.
    total: int
    limit: int
    offset: int
    clinics: tuple[NearbyClinic, ...]
    #: When the open/closed answers were evaluated (Johannesburg).
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class PaymentFilter:
    """What a patient filters private clinics by (Issue 37): cash, card, any of some schemes.

    Schemes combine with **or**: a patient on Bonitas who also ticks GEMS wants a clinic that takes
    either. Matching is against what clinics **report**, never an eligibility check.
    """

    accepts_cash: bool = False
    accepts_card: bool = False
    schemes: frozenset[MedicalAidScheme] = frozenset()

    @property
    def is_empty(self) -> bool:
        """Whether nothing is being filtered on."""
        return not (self.accepts_cash or self.accepts_card or self.schemes)


#: Why a payment filter was refused outside Private. The filter does not exist under Public or All.
PAYMENT_FILTER_PRIVATE_ONLY: Final = "Payment and medical-aid filters apply only to private clinics. Choose Private to use them."
#: Why a payment filter was refused while the feature is switched off.
PAYMENT_FILTER_OFF: Final = "Filtering by payment and medical aid is not switched on."


class PaymentFilterRefusedError(ValueError):
    """A payment filter was sent where it does not apply. Answered as 422."""


def nearby_statement(
    origin: Coordinates,
    radius_m: int,
    sector: SectorFilter,
    payment: PaymentFilter | None = None,
) -> Select[Any]:
    """The search as one statement: visible clinics in the radius, nearest first.

    Selects the clinic, its distance in metres and the total match count (a window function, so the
    page and the total come back together). Pagination is applied by the caller. Exposed so the
    ``EXPLAIN`` test reads the plan of the query the service really runs, not a hand-copied one.
    """
    centre = func.ST_GeogFromText(point_ewkt(origin))
    distance = func.ST_Distance(Site.location, centre)
    statement = (
        select(Site, distance.label("distance_m"), func.count().over().label("total"))
        .where(*publicly_visible_site_clauses())
        .where(within_radius_clause(origin, radius_m))
        .order_by(distance, Site.id)
    )
    if sector.sector is not None:
        statement = statement.where(Site.sector == sector.sector.value)
    if payment is not None and not payment.is_empty:
        # Only a private clinic's reported profile can match; the caller has already refused a
        # payment filter outside Private, and the join repeats the rule rather than trusting it.
        statement = statement.join(
            SitePaymentProfile, SitePaymentProfile.site_id == Site.id
        ).where(Site.sector == SiteSector.PRIVATE.value)
        if payment.accepts_cash:
            statement = statement.where(SitePaymentProfile.accepts_cash.is_(True))
        if payment.accepts_card:
            statement = statement.where(SitePaymentProfile.accepts_card.is_(True))
        if payment.schemes:
            statement = statement.where(
                exists().where(
                    SitePaymentMedicalAid.site_id == Site.id,
                    SitePaymentMedicalAid.scheme.in_(
                        sorted(scheme.value for scheme in payment.schemes)
                    ),
                )
            )
    return statement


def _queue_order(queues: tuple[LiveQueue, ...]) -> tuple[bool, int]:
    """The shortest-queue sort key: measured lengths ascending, then every unmeasured one.

    ``sorted`` is stable and the candidates arrive nearest first, so equal keys stay in distance
    order. An unmeasured clinic is never ranked as if its queue were empty.
    """
    waiting = total_waiting(queues)
    return (waiting is None, waiting or 0)


def _open_status(schedule: OpeningSchedule | None, moment: datetime) -> OpenStatus:
    """Issue 24's answer for one clinic; a clinic with no schedule loaded is closed."""
    state = open_state(schedule or OpeningSchedule(), moment)
    return OpenStatus(
        is_open=state.is_open,
        next_open_at=state.next_open_at,
        closure_reason=state.closure_reason,
    )


def find_nearby_sites(
    db: Session,
    origin: SearchOrigin,
    *,
    radius_m: int | None = None,
    sector: SectorFilter = SectorFilter.ALL,
    open_now: bool = False,
    sort: DiscoverySort = DiscoverySort.NEAREST,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    moment: datetime | None = None,
    reader: WaitingCountReader = cached_waiting_counts,
    payment: PaymentFilter | None = None,
    payments_enabled: bool | None = None,
) -> NearbyResult:
    """Find the verified clinics near ``origin``, nearest first.

    The one search every channel calls. See the module docstring for what it guarantees.

    Args:
        db: The session (PostgreSQL with PostGIS).
        origin: Where the patient is: a GPS fix, or an :class:`AreaOrigin` they picked, which is
            resolved to the area's centroid (Issue 34).
        radius_m: The radius asked for, in metres; capped to :data:`MAX_RADIUS_M`. ``None`` means
            :data:`DEFAULT_RADIUS_M`.
        sector: Public, private or all.
        open_now: Keep only the clinics open at ``moment``.
        sort: Nearest first (the default), or shortest queue first (Issue 32). A queue whose
            length is not measured sorts after every measured one, nearest first among them.
        limit: Page size, clamped to ``1..MAX_PAGE_SIZE``.
        offset: Results to skip.
        moment: When "open now" means; ``None`` is now in Johannesburg.
        reader: Where queue lengths come from: the queue snapshot (Issue 36) by default.
        payment: Filter private clinics by what they report accepting (Issue 37). Refused outside
            Private, and while the feature is off.
        payments_enabled: Whether payment information is part of this search; ``None`` reads
            ``PAYMENT_FILTER_ENABLED``. While off, no result carries a payment profile.

    Returns:
        The page, with the radius actually used and the total number of matches.

    Raises:
        CoordinateOutOfRangeError: If ``origin`` is outside the operating country.
        AreaNotFoundError: If ``origin`` names an area that does not exist.
        PaymentFilterRefusedError: If a payment filter is sent outside Private or while it is off.
    """
    enabled = (
        get_settings().payment_filter_enabled
        if payments_enabled is None
        else payments_enabled
    )
    if payment is not None and not payment.is_empty:
        if not enabled:
            raise PaymentFilterRefusedError(PAYMENT_FILTER_OFF)
        if sector is not SectorFilter.PRIVATE:
            raise PaymentFilterRefusedError(PAYMENT_FILTER_PRIVATE_ONLY)
    origin_area: AreaSummary | None = None
    match origin:
        case AreaOrigin(area_id=area_id):
            origin_area = get_area(db, area_id)
            point = origin_area.centroid
            basis = DistanceBasis.AREA_CENTROID
        case Coordinates():
            point = origin
            basis = DistanceBasis.POSITION
    assert_within_operating_area(point)
    moment = moment or now_sast()
    radius = clamp_radius(radius_m)
    limit = min(MAX_PAGE_SIZE, max(1, limit))
    offset = max(0, offset)
    statement = nearby_statement(point, radius.applied_m, sector, payment)

    rows: Sequence[Any]
    needs_candidates = open_now or sort is DiscoverySort.SHORTEST_QUEUE
    if needs_candidates:
        # Opening hours and queue lengths are not columns, so filtering or ordering by them runs
        # over a bounded set of the nearest candidates, then paginates. Each is read for every
        # candidate only when it decides the order or the filter, and for the page otherwise.
        rows = db.execute(statement.limit(MAX_CANDIDATES)).all()
        schedules = (
            published_schedules(
                db, [row.Site.id for row in rows], from_day=business_date(moment)
            )
            if open_now
            else {}
        )
        if open_now:
            rows = [
                row
                for row in rows
                if _open_status(schedules.get(row.Site.id), moment).is_open
            ]
        queues = (
            published_live_queues(db, [row.Site.id for row in rows], reader=reader)
            if sort is DiscoverySort.SHORTEST_QUEUE
            else {}
        )
        if sort is DiscoverySort.SHORTEST_QUEUE:
            rows = sorted(
                rows, key=lambda row: _queue_order(queues.get(row.Site.id, ()))
            )
        total = len(rows)
        rows = rows[offset : offset + limit]
        page_ids = [row.Site.id for row in rows]
        if not open_now:
            schedules = published_schedules(
                db, page_ids, from_day=business_date(moment)
            )
        if sort is not DiscoverySort.SHORTEST_QUEUE:
            queues = published_live_queues(db, page_ids, reader=reader)
    else:
        rows = db.execute(statement.limit(limit).offset(offset)).all()
        if rows:
            total = int(rows[0].total)
        else:
            # Past the last page the window function has no row to ride on; count directly.
            total = int(
                db.execute(
                    select(func.count()).select_from(statement.subquery())
                ).scalar_one()
            )
        schedules = published_schedules(
            db, [row.Site.id for row in rows], from_day=business_date(moment)
        )
        queues = published_live_queues(db, [row.Site.id for row in rows], reader=reader)

    payments = (
        published_profiles(db, [row.Site.id for row in rows], moment=moment)
        if enabled
        else {}
    )
    clinics = tuple(
        NearbyClinic(
            site_id=row.Site.id,
            slug=row.Site.slug,
            name=row.Site.name,
            sector=row.Site.sector_enum,
            location=row.Site.location,
            address_line=row.Site.address_line,
            suburb=row.Site.suburb,
            city=row.Site.city,
            province=row.Site.province_enum,
            phone_e164=row.Site.phone_e164,
            distance_m=round(float(row.distance_m)),
            distance_basis=basis,
            distance_label=distance_label(
                round(float(row.distance_m)),
                basis,
                origin_area.name if origin_area is not None else None,
            ),
            travel=estimate_travel(float(row.distance_m)),
            open_status=_open_status(schedules.get(row.Site.id), moment),
            queues=queues.get(row.Site.id, ()),
            payment=payments.get(row.Site.id),
        )
        for row in rows
    )
    return NearbyResult(
        origin=point,
        origin_area=origin_area,
        distance_basis=basis,
        radius=radius,
        sector=sector,
        open_now=open_now,
        sort=sort,
        total=total,
        limit=limit,
        offset=offset,
        clinics=clinics,
        evaluated_at=moment,
    )
