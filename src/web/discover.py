"""Clinic discovery pages: the first screen a patient sees (Issue 32).

Server-rendered on the patient layout, with htmx swaps, because the screen has to work on a cheap
Android phone on a weak connection. The page calls the discovery **service** directly, the same
:func:`~src.modules.discovery.service.find_nearby_sites` and
:func:`~src.modules.discovery.areas.search_areas` the JSON API, USSD and WhatsApp call, so the web
list cannot disagree with them about which clinics are open or how far away they are.

Three routes, each usable with and without JavaScript:

* ``GET /discover`` is the whole page. With no origin it offers the location prompt **and** the
  suburb search side by side, so declining the prompt, or a browser that cannot locate, lands on
  something that works rather than on an empty page. With ``lat``/``lon`` or ``area_id`` it also
  renders the first page of results.
* ``GET /discover/results`` is the results fragment the Public / Private / All toggle, the radius
  and the sort swap in. Asked by htmx it answers the fragment with ``HX-Push-Url`` set to the full
  page's address, so the address bar, a refresh and a shared link all show the same list. Asked by
  a browser without htmx it redirects to that address.
* ``GET /discover/areas`` is the suburb typeahead's suggestions, with the same fallback;
* ``GET /discover/clinics/{slug}`` is one clinic's detail page (Issue 35), and
  ``/discover/clinics/{slug}/live`` the figures on it htmx refreshes every 30 seconds.

**What the templates render is decided here**, as plain dataclasses (:class:`DiscoverPage`,
:class:`ResultsView`, :class:`ClinicCard`), so the words a patient reads ("Queue length not reported
yet", "about 2.1 km from the middle of Soweto", "Closed, opens tomorrow at 07:00") are tested as
data, never by reading HTML (``.cursor/rules/testing-strategy.mdc``).

**Positions are rounded before they reach a URL.** ``discover.js`` sends the browser's fix to three
decimal places (about 100 m), which is far finer than a clinic search needs and coarse enough that
an access log never holds where a patient is standing.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Final
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from src.commons.enums import DiscoverySort, MedicalAidScheme, SectorFilter, SiteSector
from src.commons.geo import CoordinateOutOfRangeError, Coordinates
from src.commons.time import APP_TIMEZONE, business_date
from src.core.config import get_settings
from src.database.session import get_db
from src.modules.discovery import areas, service
from src.modules.discovery import profile as profile_service
from src.modules.discovery.areas import AreaSummary
from src.modules.discovery.profile import ClinicProfile, ServiceOffered
from src.modules.discovery.service import (
    NearbyClinic,
    NearbyResult,
    OpenStatus,
)
from src.modules.queues.live import WaitRange
from src.modules.sites.hours import TimeSpan
from src.modules.sites.payment_profile import (
    CLINIC_REPORTED_NOTICE,
    SCHEME_LABELS,
    PaymentProfile,
)
from src.web.context import public_page_context
from src.web.routes import templates

router = APIRouter(prefix="/discover", include_in_schema=False)

DbSession = Annotated[Session, Depends(get_db)]

#: The header htmx sends with every request it makes.
HX_REQUEST: Final = "HX-Request"
#: The header that tells htmx which address to put in the bar after a swap.
HX_PUSH_URL: Final = "HX-Push-Url"

#: The radius choices the selector offers, in metres. 10 km is the default the empty state names.
RADIUS_CHOICES_M: Final = (2_000, 5_000, 10_000, 20_000, 50_000)
#: Decimal places a position keeps in a URL: about 110 m of latitude.
POSITION_DECIMALS: Final = 3


# --------------------------------------------------------------------------------------
# What a card shows, decided as data
# --------------------------------------------------------------------------------------


class DiscoverView(StrEnum):
    """How the results are shown (Issue 33). The same clinics either way: the map draws the list."""

    LIST = "list"
    MAP = "map"


#: What the view toggle calls each position.
VIEW_LABELS: Final[Mapping[DiscoverView, str]] = {
    DiscoverView.LIST: "List",
    DiscoverView.MAP: "Map",
}


class BadgeShape(StrEnum):
    """The shape drawn beside a sector's name, so the two badges differ without colour.

    Each value is a ``.sector-badge-<shape>`` class in ``discover.css`` and an icon in
    ``discover/_sector_badge.html``. A square for a public facility, a diamond for a private
    practice: the words differ, the shapes differ, and the border style differs, so a patient
    with a colour-vision deficiency, a greyscale screen or a screen reader tells them apart.
    """

    SQUARE = "square"
    DIAMOND = "diamond"


@dataclass(frozen=True, slots=True)
class SectorBadge:
    """How one sector is shown: its word, what it means, and its shape."""

    label: str
    description: str
    shape: BadgeShape


#: Every sector's badge. A sector added to the enum without one fails
#: ``tests/integration/discovery/test_discover_pages.py``.
SECTOR_BADGES: Final[Mapping[SiteSector, SectorBadge]] = {
    SiteSector.PUBLIC: SectorBadge(
        label="Public",
        description="A government clinic or community health centre",
        shape=BadgeShape.SQUARE,
    ),
    SiteSector.PRIVATE: SectorBadge(
        label="Private",
        description="A private practice; fees may apply",
        shape=BadgeShape.DIAMOND,
    ),
}

#: What the toggle calls each position.
SECTOR_FILTER_LABELS: Final[Mapping[SectorFilter, str]] = {
    SectorFilter.ALL: "All",
    SectorFilter.PUBLIC: "Public",
    SectorFilter.PRIVATE: "Private",
}

#: What the sort selector calls each order.
SORT_LABELS: Final[Mapping[DiscoverySort, str]] = {
    DiscoverySort.NEAREST: "Nearest",
    DiscoverySort.SHORTEST_QUEUE: "Shortest queue",
}

#: Shown for a length nobody has counted. Never "0": an unknown queue is not an empty one.
QUEUE_NOT_REPORTED: Final = "Queue length not reported yet"
#: Shown until the estimator (Issue 42) exists. Never an invented figure.
WAIT_NOT_AVAILABLE: Final = "Wait estimate not available yet"


def queue_label(waiting: int | None) -> str:
    """How many people are waiting, in words; "not reported yet" when nobody counted."""
    if waiting is None:
        return QUEUE_NOT_REPORTED
    if waiting == 0:
        return "No one waiting"
    return "1 person waiting" if waiting == 1 else f"{waiting} people waiting"


def counted_ago(as_of: datetime | None, moment: datetime) -> str | None:
    """How old a measured figure is, in seconds (Issue 36): counted 12 s ago. ``None`` when unknown."""
    if as_of is None:
        return None
    seconds = max(0, round((moment - as_of).total_seconds()))
    return f"counted {seconds} s ago"


def measured_queue_label(
    waiting: int | None, as_of: datetime | None, moment: datetime
) -> str:
    """The queue length with its age when it was measured; "not reported yet" when it was not."""
    label = queue_label(waiting)
    age = counted_ago(as_of, moment) if waiting is not None else None
    return f"{label}, {age}" if age else label


def wait_label(ranges: Sequence[WaitRange | None]) -> str:
    """The expected wait across a clinic's queues, always a range; "not available" until Issue 42.

    With a range for every queue, the label spans the shortest low to the longest high, because a
    patient does not yet know which queue they will join.
    """
    known = [wait for wait in ranges if wait is not None]
    if not known or len(known) != len(ranges):
        return WAIT_NOT_AVAILABLE
    low = min(wait.low_minutes for wait in known)
    high = max(wait.high_minutes for wait in known)
    return f"Wait about {low}–{high} min"


def travel_label(walking_minutes: int, driving_minutes: int) -> str:
    """The rough trip in words: about 24 min on foot, 5 min by car."""
    return f"About {walking_minutes} min on foot, {driving_minutes} min by car"


def opens_phrase(next_open_at: datetime, moment: datetime) -> str:
    """When a clinic opens next, relative to ``moment``: today at 14:00, tomorrow at 07:00, Mon 21 Sep at 07:30."""
    opens = next_open_at.astimezone(APP_TIMEZONE)
    today = business_date(moment)
    if opens.date() == today:
        return f"today at {opens:%H:%M}"
    if opens.date() == today + timedelta(days=1):
        return f"tomorrow at {opens:%H:%M}"
    return f"{opens:%a %d %b} at {opens:%H:%M}"


def open_label(status_: OpenStatus, moment: datetime) -> str:
    """Open or closed, and when it opens next, in Johannesburg wall-clock time.

    ``moment`` is when the search was evaluated, so "today" and "tomorrow" agree with the answer.
    """
    if status_.is_open:
        return "Open now"
    if status_.closure_reason:
        reason = f"Closed: {status_.closure_reason.rstrip('.')}."
        if status_.next_open_at is None:
            return reason
        return f"{reason} Opens {opens_phrase(status_.next_open_at, moment)}."
    if status_.next_open_at is None:
        return "Closed, no opening hours listed"
    return f"Closed, opens {opens_phrase(status_.next_open_at, moment)}"


@dataclass(frozen=True, slots=True)
class PaymentFilterView:
    """The payment and medical-aid filter under Private (Issue 37): what is ticked, and the notice."""

    cash: Choice
    card: Choice
    schemes: tuple[Choice, ...]
    notice: str


def payment_filter_view(payment: service.PaymentFilter | None) -> PaymentFilterView:
    """The filter's options, with the request's choices ticked."""
    chosen = payment or service.PaymentFilter()
    return PaymentFilterView(
        cash=Choice("true", "Cash", chosen.accepts_cash),
        card=Choice("true", "Card", chosen.accepts_card),
        schemes=tuple(
            Choice(scheme.value, label, scheme in chosen.schemes)
            for scheme, label in SCHEME_LABELS.items()
            if scheme is not MedicalAidScheme.OTHER
        ),
        notice=PAYMENT_FILTER_NOTICE,
    )


@dataclass(frozen=True, slots=True)
class PaymentLines:
    """A private clinic's reported payment information in words, with its notice (Issue 37)."""

    methods: str
    medical_aids: str
    copay_notice: str | None
    stale_label: str | None
    notice: str


def payment_lines(profile: PaymentProfile | None) -> PaymentLines | None:
    """What a card or the detail page says about payment; ``None`` when there is nothing reported."""
    if profile is None:
        return None
    methods = [
        word
        for word, accepted in (
            ("cash", profile.accepts_cash),
            ("card", profile.accepts_card),
        )
        if accepted
    ]
    confirmed = profile.last_confirmed_at.astimezone(APP_TIMEZONE)
    return PaymentLines(
        methods=f"Takes {' and '.join(methods)}"
        if methods
        else "No cash or card listed",
        medical_aids=(
            "Medical aids: " + ", ".join(item.label for item in profile.schemes)
            if profile.schemes
            else "No medical aids listed"
        ),
        copay_notice=profile.copay_notice,
        stale_label=(
            f"Not confirmed by the clinic since {confirmed:%d %B %Y}"
            if profile.stale
            else None
        ),
        notice=profile.notice,
    )


@dataclass(frozen=True, slots=True)
class ClinicCard:
    """One clinic in the list, as the patient reads it."""

    slug: str
    name: str
    badge: SectorBadge
    address: str
    distance_label: str
    distance_is_approximate: bool
    travel_label: str
    is_open: bool
    open_label: str
    queue_label: str
    wait_label: str
    #: A private clinic's reported payment information, with its notice (Issue 37).
    payment: PaymentLines | None = None
    #: Where the pin goes on the map (Issue 33), read from the card itself so the map cannot show a
    #: clinic the list does not.
    latitude: float = 0.0
    longitude: float = 0.0
    #: Hand-off to the phone's own maps app, from the card and the map's preview.
    directions_href: str = ""


def clinic_card(clinic: NearbyClinic, moment: datetime) -> ClinicCard:
    """The card for one search result."""
    address = clinic.address_line
    if clinic.suburb and clinic.suburb.casefold() not in address.casefold():
        address = f"{address}, {clinic.suburb}"
    return ClinicCard(
        slug=clinic.slug,
        name=clinic.name,
        badge=SECTOR_BADGES[clinic.sector],
        address=address,
        distance_label=clinic.distance_label,
        distance_is_approximate=clinic.distance_basis.approximate,
        travel_label=travel_label(
            clinic.travel.walking_minutes, clinic.travel.driving_minutes
        ),
        is_open=clinic.open_status.is_open,
        open_label=open_label(clinic.open_status, moment),
        queue_label=measured_queue_label(
            clinic.total_waiting,
            min((q.as_of for q in clinic.queues if q.as_of is not None), default=None),
            moment,
        ),
        wait_label=wait_label([queue.wait_range for queue in clinic.queues]),
        payment=payment_lines(clinic.payment),
        latitude=clinic.location.latitude,
        longitude=clinic.location.longitude,
        directions_href=directions_href(clinic.location),
    )


# --------------------------------------------------------------------------------------
# The page and the fragment, as data
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Choice:
    """One option of a toggle or selector."""

    value: str
    label: str
    selected: bool


@dataclass(frozen=True, slots=True)
class FilterGroup:
    """One radio group of the filter bar: its query parameter, its visible legend and its options."""

    name: str
    legend: str
    choices: tuple[Choice, ...]


@dataclass(frozen=True, slots=True)
class EmptyState:
    """What the list says when nothing matched, and the one thing to try next."""

    title: str
    body: str
    action_label: str | None = None
    action_href: str | None = None


@dataclass(frozen=True, slots=True)
class Origin:
    """Where the search starts from, as the URL carries it and the heading names it."""

    latitude: float | None = None
    longitude: float | None = None
    area: AreaSummary | None = None

    @property
    def is_set(self) -> bool:
        """Whether there is anywhere to search from yet."""
        return self.area is not None or self.latitude is not None

    @property
    def params(self) -> dict[str, str]:
        """The query parameters that name this origin."""
        if self.area is not None:
            return {"area_id": self.area.area_id}
        if self.latitude is not None and self.longitude is not None:
            return {"lat": f"{self.latitude}", "lon": f"{self.longitude}"}
        return {}

    @property
    def heading(self) -> str:
        """The results heading: clinics near Soweto, or near you."""
        return f"Clinics near {self.area.name}" if self.area else "Clinics near you"


@dataclass(frozen=True, slots=True)
class ResultsView:
    """Everything the results fragment renders."""

    origin: Origin
    sector: SectorFilter
    sort: DiscoverySort
    radius_m: int
    total: int
    cards: tuple[ClinicCard, ...]
    summary: str
    approximate_note: str | None
    empty: EmptyState | None
    more_href: str | None
    offset: int
    payment: service.PaymentFilter | None = None
    view: DiscoverView = DiscoverView.LIST

    def href(self, **changes: object) -> str:
        """The full page's address for this search, with ``changes`` applied."""
        return page_href(
            self.origin,
            self.sector,
            self.sort,
            self.radius_m,
            payment=self.payment,
            view=self.view,
            **changes,
        )

    @property
    def map_note(self) -> str | None:
        """Said on the map when the list has more clinics than it has loaded (Issue 33)."""
        shown = self.offset + len(self.cards)
        if self.total <= shown:
            return None
        return (
            f"The map shows the {shown} nearest of {self.total} clinics, the same ones as the "
            "list. Switch to the list to load more."
        )


@dataclass(frozen=True, slots=True)
class DiscoverPage:
    """Everything the whole page renders."""

    origin: Origin
    sector_choices: tuple[Choice, ...]
    radius_choices: tuple[Choice, ...]
    sort_choices: tuple[Choice, ...]
    query: str
    suggestions: tuple[AreaSummary, ...]
    results: ResultsView | None
    #: Shown instead of results when the origin could not be used.
    problem: str | None = None
    #: Whether to lead with the location prompt: only when there is nowhere to search from yet.
    offer_location: bool = field(default=True)
    #: The payment and medical-aid filter (Issue 37). ``None``, and so absent from the page, unless
    #: the feature is on **and** Private is selected.
    payment_filter: PaymentFilterView | None = None
    view_choices: tuple[Choice, ...] = ()
    view: DiscoverView = DiscoverView.LIST

    @property
    def filter_groups(self) -> tuple[FilterGroup, ...]:
        """The filter bar, in order: which sector, how far, and in what order."""
        return (
            FilterGroup(name="sector", legend="Show", choices=self.sector_choices),
            FilterGroup(name="radius_m", legend="Within", choices=self.radius_choices),
            FilterGroup(name="sort", legend="Sort by", choices=self.sort_choices),
            FilterGroup(name="view", legend="See them as", choices=self.view_choices),
        )

    @property
    def origin_label(self) -> str:
        """What the map calls the point the search started from."""
        return f"The middle of {self.origin.area.name}" if self.origin.area else "You"

    @property
    def origin_point(self) -> tuple[float, float] | None:
        """``(latitude, longitude)`` of the search origin, for the map's own marker."""
        if self.origin.area is not None:
            return (
                self.origin.area.centroid.latitude,
                self.origin.area.centroid.longitude,
            )
        if self.origin.latitude is not None and self.origin.longitude is not None:
            return self.origin.latitude, self.origin.longitude
        return None


def payment_params(payment: service.PaymentFilter | None) -> dict[str, object]:
    """The query parameters that carry a payment filter; nothing for an empty one."""
    if payment is None or payment.is_empty:
        return {}
    params: dict[str, object] = {}
    if payment.accepts_cash:
        params["accepts_cash"] = "true"
    if payment.accepts_card:
        params["accepts_card"] = "true"
    if payment.schemes:
        params["medical_aid"] = sorted(scheme.value for scheme in payment.schemes)
    return params


def page_href(
    origin: Origin,
    sector: SectorFilter,
    sort: DiscoverySort,
    radius_m: int,
    *,
    payment: service.PaymentFilter | None = None,
    view: DiscoverView = DiscoverView.LIST,
    **changes: object,
) -> str:
    """``/discover?...`` for a search, leaving out whatever is at its default.

    A payment filter is carried only under Private, the one sector it exists for.
    """
    params: dict[str, object] = {
        **origin.params,
        "sector": sector.value,
        "radius_m": radius_m,
        "sort": sort.value,
        **(payment_params(payment) if sector is SectorFilter.PRIVATE else {}),
        "view": view.value,
    }
    params.update(changes)
    defaults = {
        "sector": SectorFilter.ALL.value,
        "radius_m": service.DEFAULT_RADIUS_M,
        "sort": DiscoverySort.NEAREST.value,
        "offset": 0,
        "view": DiscoverView.LIST.value,
    }
    kept = {
        key: value
        for key, value in params.items()
        if value is not None and defaults.get(key) != value
    }
    return f"/discover?{urlencode(kept, doseq=True)}" if kept else "/discover"


def _radius_words(metres: int) -> str:
    """A radius in words: 10 km, or 500 m."""
    return f"{metres // 1000} km" if metres >= 1000 else f"{metres} m"


def _wider_radius(metres: int) -> int | None:
    """The next radius choice above ``metres``, or ``None`` at the widest."""
    return next((choice for choice in RADIUS_CHOICES_M if choice > metres), None)


def results_view(
    result: NearbyResult,
    origin: Origin,
    payment: service.PaymentFilter | None = None,
    view: DiscoverView = DiscoverView.LIST,
) -> ResultsView:
    """Turn one search result into what the list shows, including its empty state."""
    radius = result.radius.applied_m
    cards = tuple(clinic_card(clinic, result.evaluated_at) for clinic in result.clinics)
    sector_words = {
        SectorFilter.ALL: "clinics",
        SectorFilter.PUBLIC: "public clinics",
        SectorFilter.PRIVATE: "private clinics",
    }[result.sector]
    noun = sector_words if result.total != 1 else sector_words.removesuffix("s")
    summary = f"{result.total} {noun} within {_radius_words(radius)}"

    empty: EmptyState | None = None
    if not cards and result.offset == 0:
        wider = _wider_radius(radius)
        empty = EmptyState(
            title=f"No {sector_words} within {_radius_words(radius)}",
            body=(
                "Try a wider radius."
                if wider
                else "Try another suburb, or show public and private clinics together."
            ),
            action_label=f"Search within {_radius_words(wider)}" if wider else None,
            action_href=page_href(
                origin, result.sector, result.sort, wider, payment=payment, view=view
            )
            if wider
            else None,
        )

    next_offset = result.offset + len(cards)
    more_href = (
        page_href(
            origin,
            result.sector,
            result.sort,
            radius,
            payment=payment,
            view=view,
            offset=next_offset,
        )
        if next_offset < result.total
        else None
    )
    note = (
        f"Distances are approximate: they are measured from the middle of {origin.area.name}."
        if result.distance_basis.approximate and origin.area is not None
        else None
    )
    return ResultsView(
        origin=origin,
        sector=result.sector,
        sort=result.sort,
        radius_m=radius,
        total=result.total,
        cards=cards,
        summary=summary,
        approximate_note=note,
        empty=empty,
        more_href=more_href,
        offset=result.offset,
        payment=payment,
        view=view,
    )


def _choices[T: StrEnum](labels: Mapping[T, str], selected: T) -> tuple[Choice, ...]:
    """One :class:`Choice` per option, marking the selected one."""
    return tuple(
        Choice(value=value.value, label=label, selected=value is selected)
        for value, label in labels.items()
    )


def _round(value: float | None) -> float | None:
    """A coordinate rounded to :data:`POSITION_DECIMALS`, or ``None``."""
    return None if value is None else round(value, POSITION_DECIMALS)


def discover_page(
    db: Session,
    *,
    lat: float | None = None,
    lon: float | None = None,
    area_id: str | None = None,
    q: str = "",
    sector: SectorFilter = SectorFilter.ALL,
    radius_m: int = service.DEFAULT_RADIUS_M,
    sort: DiscoverySort = DiscoverySort.NEAREST,
    offset: int = 0,
    moment: datetime | None = None,
    payment: service.PaymentFilter | None = None,
    payments_enabled: bool = False,
    view: DiscoverView = DiscoverView.LIST,
) -> DiscoverPage:
    """Build the whole discovery page for one request.

    A position is rounded (:data:`POSITION_DECIMALS`) before anything else sees it. An origin that
    cannot be used (a coordinate outside the country, an area that no longer exists) becomes a
    ``problem`` sentence above the suburb search, never an error page: the patient can still search.
    """
    radius = service.clamp_radius(radius_m).applied_m
    # The filter exists only under Private with the feature on. Anywhere else a leftover checkbox
    # from the previous position of the toggle is dropped, not refused: the page simply has no
    # such filter there.
    applies = payments_enabled and sector is SectorFilter.PRIVATE
    payment = payment if applies else None
    problem: str | None = None
    origin = Origin()
    try:
        if area_id:
            origin = Origin(area=areas.get_area(db, area_id))
        elif lat is not None and lon is not None:
            point = Coordinates(
                latitude=_round(lat) or 0.0, longitude=_round(lon) or 0.0
            )
            origin = Origin(latitude=point.latitude, longitude=point.longitude)
    except areas.AreaNotFoundError:
        problem = "That area is no longer listed. Search for your suburb again."
    except CoordinateOutOfRangeError:
        problem = (
            "That location is outside South Africa. Search for your suburb instead."
        )

    results: ResultsView | None = None
    if origin.is_set:
        try:
            results = search_results(
                db,
                origin,
                sector=sector,
                radius_m=radius,
                sort=sort,
                offset=offset,
                moment=moment,
                payment=payment,
                payments_enabled=payments_enabled,
                view=view,
            )
        except CoordinateOutOfRangeError:
            problem = (
                "That location is outside South Africa. Search for your suburb instead."
            )
            origin = Origin()

    suggestions = tuple(areas.search_areas(db, q)) if q.strip() else ()
    return DiscoverPage(
        origin=origin,
        sector_choices=_choices(SECTOR_FILTER_LABELS, sector),
        radius_choices=tuple(
            Choice(
                value=str(metres),
                label=_radius_words(metres),
                selected=metres == radius,
            )
            for metres in RADIUS_CHOICES_M
        ),
        sort_choices=_choices(SORT_LABELS, sort),
        query=q,
        suggestions=suggestions,
        results=results,
        problem=problem,
        offer_location=not origin.is_set,
        payment_filter=payment_filter_view(payment) if applies else None,
        view_choices=_choices(VIEW_LABELS, view),
        view=view,
    )


def search_results(
    db: Session,
    origin: Origin,
    *,
    sector: SectorFilter,
    radius_m: int,
    sort: DiscoverySort,
    offset: int = 0,
    moment: datetime | None = None,
    payment: service.PaymentFilter | None = None,
    payments_enabled: bool = False,
    view: DiscoverView = DiscoverView.LIST,
) -> ResultsView:
    """Run the one discovery search for ``origin`` and shape its answer for the list."""
    search_from: service.SearchOrigin = (
        service.AreaOrigin(area_id=origin.area.area_id)
        if origin.area is not None
        else Coordinates(
            latitude=origin.latitude or 0.0, longitude=origin.longitude or 0.0
        )
    )
    result = service.find_nearby_sites(
        db,
        search_from,
        radius_m=radius_m,
        sector=sector,
        sort=sort,
        offset=offset,
        moment=moment,
        payment=payment,
        payments_enabled=payments_enabled,
    )
    return results_view(result, origin, payment, view)


# --------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------

Latitude = Annotated[float | None, Query(ge=-90, le=90)]
Longitude = Annotated[float | None, Query(ge=-180, le=180)]
AreaId = Annotated[str | None, Query(max_length=36)]
RadiusM = Annotated[int, Query(ge=1)]
Offset = Annotated[int, Query(ge=0, le=1000)]
MedicalAids = Annotated[list[MedicalAidScheme] | None, Query()]


def _payment(
    accepts_cash: bool, accepts_card: bool, medical_aid: list[MedicalAidScheme] | None
) -> service.PaymentFilter:
    """The payment filter a request's query parameters describe."""
    return service.PaymentFilter(
        accepts_cash=accepts_cash,
        accepts_card=accepts_card,
        schemes=frozenset(medical_aid or ()),
    )


def _is_htmx(request: Request) -> bool:
    """Whether htmx made this request (it sends ``HX-Request: true``)."""
    return request.headers.get(HX_REQUEST, "").lower() == "true"


def _render(request: Request, template: str, **context: object) -> HTMLResponse:
    """Render a discovery template with the public page context."""
    return templates.TemplateResponse(
        request,
        template,
        public_page_context(request, page_title="Find a clinic", **context),
    )


@router.get("", response_class=HTMLResponse)
def discover(
    request: Request,
    db: DbSession,
    lat: Latitude = None,
    lon: Longitude = None,
    area_id: AreaId = None,
    q: Annotated[str, Query(max_length=100)] = "",
    sector: SectorFilter = SectorFilter.ALL,
    radius_m: RadiusM = service.DEFAULT_RADIUS_M,
    sort: DiscoverySort = DiscoverySort.NEAREST,
    offset: Offset = 0,
    accepts_cash: bool = False,
    accepts_card: bool = False,
    medical_aid: MedicalAids = None,
    view: DiscoverView = DiscoverView.LIST,
) -> HTMLResponse:
    """The discovery page. Public: a patient looking for a clinic has no account."""
    page = discover_page(
        db,
        lat=lat,
        lon=lon,
        area_id=area_id,
        q=q,
        sector=sector,
        radius_m=radius_m,
        sort=sort,
        offset=offset,
        payment=_payment(accepts_cash, accepts_card, medical_aid),
        payments_enabled=get_settings().payment_filter_enabled,
        view=view,
    )
    return _render(request, "discover/list.html", page=page)


@router.get("/results", response_class=HTMLResponse)
def discover_results(
    request: Request,
    db: DbSession,
    lat: Latitude = None,
    lon: Longitude = None,
    area_id: AreaId = None,
    sector: SectorFilter = SectorFilter.ALL,
    radius_m: RadiusM = service.DEFAULT_RADIUS_M,
    sort: DiscoverySort = DiscoverySort.NEAREST,
    offset: Offset = 0,
    accepts_cash: bool = False,
    accepts_card: bool = False,
    medical_aid: MedicalAids = None,
    view: DiscoverView = DiscoverView.LIST,
) -> Response:
    """The results fragment an htmx swap asks for; a plain browser is sent to the full page.

    The fragment also carries the payment filter's slot, swapped out of band (Issue 37), so moving
    the toggle away from Private removes the filter from the page rather than hiding it.
    """
    page = discover_page(
        db,
        lat=lat,
        lon=lon,
        area_id=area_id,
        sector=sector,
        radius_m=radius_m,
        sort=sort,
        offset=offset,
        payment=_payment(accepts_cash, accepts_card, medical_aid),
        payments_enabled=get_settings().payment_filter_enabled,
        view=view,
    )
    address = page_href(
        page.origin,
        sector,
        sort,
        page.results.radius_m if page.results else radius_m,
        payment=page.results.payment if page.results else None,
        view=view,
    )
    if not _is_htmx(request):
        return RedirectResponse(address, status_code=status.HTTP_303_SEE_OTHER)
    template = "discover/_more.html" if offset else "discover/_results_swap.html"
    response = _render(request, template, page=page, view=page.results)
    if page.results is None:
        # Nothing to search from: tell htmx not to swap a half-empty list over a good one.
        response.status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif not offset:
        response.headers[HX_PUSH_URL] = address
    return response


@router.get("/areas", response_class=HTMLResponse)
def discover_areas(
    request: Request,
    db: DbSession,
    q: Annotated[str, Query(max_length=100)] = "",
) -> Response:
    """The suburb typeahead's suggestions; a plain browser gets the full page with them on it."""
    if not _is_htmx(request):
        return RedirectResponse(
            f"/discover?{urlencode({'q': q})}" if q else "/discover",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    suggestions = tuple(areas.search_areas(db, q)) if q.strip() else ()
    return _render(
        request, "discover/_area_suggestions.html", suggestions=suggestions, query=q
    )


# --------------------------------------------------------------------------------------
# The clinic detail page (Issue 35)
# --------------------------------------------------------------------------------------

#: How often the live figures refresh while the page is open. Well inside the milestone's rule that
#: a shown queue length is never more than 30 seconds stale.
LIVE_REFRESH_SECONDS: Final = 30

#: The label on the join action, enabled or not. The words never change, only whether it works.
JOIN_LABEL: Final = "Join the queue"
#: Why the action is disabled while joining from a phone is not switched on (Issue 40 switches it).
JOIN_NOT_SWITCHED_ON: Final = (
    "Joining from your phone is not switched on yet. You can join at the clinic's front desk "
    "while it is open."
)
#: What a private clinic's payment section says until it has listed anything (Issue 37 fills it).
PAYMENT_NOT_LISTED: Final = (
    "This clinic has not listed the payment methods or medical aids it accepts. Ask the clinic "
    "before you travel."
)
#: Shown above the filter, so the patient reads it before choosing (Issue 37).
PAYMENT_FILTER_NOTICE: Final = (
    "Payment methods and medical aids are reported by each clinic, not checked by ClinicQ. "
    "Please confirm with the clinic before you travel."
)


@dataclass(frozen=True, slots=True)
class JoinButton:
    """The join action: always rendered, enabled only when joining works, and never without a reason.

    ``href`` is where an enabled action goes: the join flow Issue 40 serves at that address.
    """

    label: str
    enabled: bool
    reason: str | None
    href: str | None


@dataclass(frozen=True, slots=True)
class QueueRow:
    """One of the clinic's queues, as the per-queue breakdown shows it."""

    name: str
    waiting_label: str
    wait_label: str
    walk_in_only: bool


@dataclass(frozen=True, slots=True)
class HoursRow:
    """One weekday in the opening-hours list."""

    day: str
    hours: str
    is_today: bool


@dataclass(frozen=True, slots=True)
class LiveView:
    """The part of the detail page htmx refreshes: open state, the join action and the queues."""

    slug: str
    is_open: bool
    open_label: str
    today_label: str
    join: JoinButton
    total_label: str
    queues: tuple[QueueRow, ...]
    updated_label: str
    refresh_seconds: int
    live_href: str


@dataclass(frozen=True, slots=True)
class DetailView:
    """Everything the clinic detail page renders."""

    slug: str
    name: str
    badge: SectorBadge
    address: str
    area_line: str
    phone_label: str | None
    tel_href: str | None
    directions_href: str
    live: LiveView
    week: tuple[HoursRow, ...]
    services: tuple[ServiceOffered, ...]
    #: The payment section's sentence when nothing is reported: ``None`` for a public clinic (which
    #: never carries payment information), or while the feature is off.
    payment_note: str | None
    #: What a private clinic reports accepting, with the notice. ``None`` when nothing is reported.
    payment: PaymentLines | None
    reported_notice: str


def spans_label(spans: Sequence[TimeSpan]) -> str:
    """A day's spans in words (07:00–12:30, 13:30–16:00), or Closed when there are none."""
    if not spans:
        return "Closed"
    if any(span.opens_at == span.closes_at for span in spans):
        # Issue 24: equal times are the whole twenty-four hours, not a span of no length.
        return "Open all day"
    return ", ".join(f"{span.opens_at:%H:%M}–{span.closes_at:%H:%M}" for span in spans)


def phone_label(e164: str) -> str:
    """A South African E.164 number grouped for reading aloud: +27 10 555 0100."""
    if e164.startswith("+27") and len(e164) == 12:
        national = e164[3:]
        return f"+27 {national[:2]} {national[2:5]} {national[5:]}"
    return e164


def directions_href(location: Coordinates) -> str:
    """Hand-off to the phone's own maps application, with the clinic as the destination.

    Google's documented cross-platform directions URL opens the Maps app on Android and iOS when it
    is installed and the website otherwise; no route is computed by ClinicQ (Issue 33 reuses this).
    """
    destination = f"{location.latitude},{location.longitude}"
    return f"https://www.google.com/maps/dir/?api=1&{urlencode({'destination': destination})}"


def join_button(profile: ClinicProfile, *, join_enabled: bool) -> JoinButton:
    """The join action for this clinic now: disabled with the service's reason, or the flag's."""
    availability = profile.join
    if not availability.allowed:
        reason = (availability.reason or "Joining is not possible right now.").rstrip(
            "."
        ) + "."
        if availability.next_open_at is not None:
            reason = f"{reason} Opens {opens_phrase(availability.next_open_at, profile.evaluated_at)}."
        return JoinButton(JOIN_LABEL, enabled=False, reason=reason, href=None)
    if not join_enabled:
        return JoinButton(
            JOIN_LABEL, enabled=False, reason=JOIN_NOT_SWITCHED_ON, href=None
        )
    return JoinButton(
        JOIN_LABEL,
        enabled=True,
        reason=None,
        href=f"/discover/clinics/{profile.slug}/join",
    )


def live_view(profile: ClinicProfile, *, join_enabled: bool) -> LiveView:
    """The refreshable half of the page."""
    rows = tuple(
        QueueRow(
            name=queue.name,
            waiting_label=measured_queue_label(
                queue.waiting, queue.as_of, profile.evaluated_at
            ),
            wait_label=wait_label([queue.wait_range]),
            walk_in_only=not queue.allows_remote_join,
        )
        for queue in profile.queues
    )
    today = spans_label(profile.today)
    return LiveView(
        slug=profile.slug,
        is_open=profile.open_status.is_open,
        open_label=open_label(profile.open_status, profile.evaluated_at),
        today_label=f"Today: {today[0].lower()}{today[1:]}"
        if not today[0].isdigit()
        else f"Today: {today}",
        join=join_button(profile, join_enabled=join_enabled),
        total_label=queue_label(profile.total_waiting),
        queues=rows,
        updated_label=f"Updated at {profile.evaluated_at.astimezone(APP_TIMEZONE):%H:%M:%S}",
        refresh_seconds=LIVE_REFRESH_SECONDS,
        live_href=f"/discover/clinics/{profile.slug}/live",
    )


def detail_view(profile: ClinicProfile, *, join_enabled: bool) -> DetailView:
    """The whole detail page for one clinic profile."""
    today = business_date(profile.evaluated_at).weekday()
    area = ", ".join(part for part in (profile.suburb, profile.city) if part)
    return DetailView(
        slug=profile.slug,
        name=profile.name,
        badge=SECTOR_BADGES[profile.sector],
        address=profile.address_line,
        area_line=f"{area}, {profile.province.value}",
        phone_label=phone_label(profile.phone_e164) if profile.phone_e164 else None,
        tel_href=f"tel:{profile.phone_e164}" if profile.phone_e164 else None,
        directions_href=directions_href(profile.location),
        live=live_view(profile, join_enabled=join_enabled),
        week=tuple(
            HoursRow(
                day=day.name,
                hours=spans_label(day.spans),
                is_today=day.weekday == today,
            )
            for day in profile.week
        ),
        services=profile.services,
        payment_note=PAYMENT_NOT_LISTED
        if profile.payments_enabled
        and profile.sector is SiteSector.PRIVATE
        and profile.payment is None
        else None,
        payment=payment_lines(profile.payment),
        reported_notice=CLINIC_REPORTED_NOTICE,
    )


Slug = Annotated[str, Path(max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


def _join_enabled() -> bool:
    """The feature flag, read per request so a test's settings override applies."""
    return get_settings().patient_join_enabled


@router.get("/clinics/{slug}", response_class=HTMLResponse)
def clinic_detail(request: Request, slug: Slug, db: DbSession) -> HTMLResponse:
    """One clinic's detail page. A clinic a patient may not see is the same 404 as a missing one."""
    found = profile_service.clinic_profile(
        db, slug, payments_enabled=get_settings().payment_filter_enabled
    )
    if found is None:
        response = _render(request, "discover/not_found.html")
        response.status_code = status.HTTP_404_NOT_FOUND
        return response
    view = detail_view(found, join_enabled=_join_enabled())
    return templates.TemplateResponse(
        request,
        "discover/detail.html",
        public_page_context(request, page_title=found.name, view=view),
    )


@router.get("/clinics/{slug}/live", response_class=HTMLResponse)
def clinic_live(request: Request, slug: Slug, db: DbSession) -> Response:
    """The live figures htmx polls every :data:`LIVE_REFRESH_SECONDS`; a plain browser gets the page."""
    if not _is_htmx(request):
        return RedirectResponse(
            f"/discover/clinics/{slug}", status_code=status.HTTP_303_SEE_OTHER
        )
    found = profile_service.clinic_profile(db, slug)
    if found is None:
        # htmx does not swap a 404, so the last figures stay rather than the section vanishing.
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return _render(
        request,
        "discover/_queue_summary.html",
        live=live_view(found, join_enabled=_join_enabled()),
    )
