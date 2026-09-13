"""The discovery API's response shapes (Issue 31).

The service returns frozen dataclasses so every channel can reuse it; these models are the **web
API's** rendering of the same data, built by one ``of`` classmethod each so the mapping is written
once. Nothing here computes anything.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.commons.enums import (
    AreaKind,
    DiscoverySort,
    DistanceBasis,
    QueueKind,
    SaProvince,
    SectorFilter,
    ServiceCategory,
    SiteSector,
)
from src.modules.discovery.areas import AreaSummary
from src.modules.discovery.profile import ClinicProfile
from src.modules.discovery.service import NearbyClinic, NearbyResult, OpenStatus
from src.modules.queues.live import LiveQueue, WaitRange
from src.modules.sites.hours import TimeSpan
from src.modules.sites.schemas import SiteLocationOut


class AreaOut(BaseModel):
    """A place a search can start from (Issue 34)."""

    id: str
    name: str
    label: str = Field(
        description="The name with its municipality, to tell same-named places apart."
    )
    matched_name: str | None = Field(
        description="The alternative name the text matched, when it was not the place's own name."
    )
    kind: AreaKind
    municipality: str | None
    province: SaProvince
    centroid: SiteLocationOut

    @classmethod
    def of(cls, area: AreaSummary) -> AreaOut:
        """The API shape of one area."""
        return cls(
            id=area.area_id,
            name=area.name,
            label=area.label,
            matched_name=area.matched_name,
            kind=area.kind,
            municipality=area.municipality,
            province=area.province,
            centroid=SiteLocationOut.of(area.centroid),
        )


class AreaListOut(BaseModel):
    """Suggested or recently used areas, best or newest first."""

    items: list[AreaOut]


class WaitRangeOut(BaseModel):
    """An expected wait, always a range."""

    low_minutes: int
    high_minutes: int

    @classmethod
    def of(cls, wait: WaitRange | None) -> WaitRangeOut | None:
        """The API shape, or ``None`` when there is no estimate."""
        if wait is None:
            return None
        return cls(low_minutes=wait.low_minutes, high_minutes=wait.high_minutes)


class LiveQueueOut(BaseModel):
    """One of a clinic's queues, with its current length."""

    id: str
    name: str
    kind: QueueKind
    allows_remote_join: bool
    waiting: int | None = Field(
        description="People waiting now. `null` means not measured, which is not the same as 0."
    )
    wait_range: WaitRangeOut | None = Field(
        description="The expected wait as a range. `null` until the estimator (Issue 42) exists."
    )
    as_of: datetime | None = Field(
        description="When `waiting` was counted (the queue snapshot's time); `null` when not counted."
    )

    @classmethod
    def of(cls, queue: LiveQueue) -> LiveQueueOut:
        """The API shape of one live queue."""
        return cls(
            id=queue.queue_id,
            name=queue.name,
            kind=queue.kind,
            allows_remote_join=queue.allows_remote_join,
            waiting=queue.waiting,
            wait_range=WaitRangeOut.of(queue.wait_range),
            as_of=queue.as_of,
        )


class OpenStatusOut(BaseModel):
    """Open or closed when the search ran."""

    is_open: bool
    next_open_at: datetime | None
    closure_reason: str | None

    @classmethod
    def of(cls, status: OpenStatus) -> OpenStatusOut:
        """The API shape of an open/closed answer."""
        return cls(
            is_open=status.is_open,
            next_open_at=status.next_open_at,
            closure_reason=status.closure_reason,
        )


class TravelOut(BaseModel):
    """A rough travel time from the straight-line distance."""

    walking_minutes: int
    driving_minutes: int


class NearbyClinicOut(BaseModel):
    """One search result."""

    id: str
    slug: str
    name: str
    sector: SiteSector
    location: SiteLocationOut
    address_line: str
    suburb: str | None
    city: str
    province: SaProvince
    phone_e164: str | None
    distance_m: int = Field(
        description="Straight-line distance from the search origin."
    )
    distance_is_approximate: bool = Field(
        description="True when measured from an area's centroid rather than the patient's position."
    )
    distance_label: str = Field(
        description='The distance in words, e.g. "1.4 km" or "about 4.0 km from the middle of Soweto".'
    )
    travel: TravelOut
    open_status: OpenStatusOut
    total_waiting: int | None = Field(
        description="Everyone waiting across the clinic's queues; `null` when not measured."
    )
    queues: list[LiveQueueOut]

    @classmethod
    def of(cls, clinic: NearbyClinic) -> NearbyClinicOut:
        """The API shape of one result."""
        return cls(
            id=clinic.site_id,
            slug=clinic.slug,
            name=clinic.name,
            sector=clinic.sector,
            location=SiteLocationOut.of(clinic.location),
            address_line=clinic.address_line,
            suburb=clinic.suburb,
            city=clinic.city,
            province=clinic.province,
            phone_e164=clinic.phone_e164,
            distance_m=clinic.distance_m,
            distance_is_approximate=clinic.distance_basis.approximate,
            distance_label=clinic.distance_label,
            travel=TravelOut(
                walking_minutes=clinic.travel.walking_minutes,
                driving_minutes=clinic.travel.driving_minutes,
            ),
            open_status=OpenStatusOut.of(clinic.open_status),
            total_waiting=clinic.total_waiting,
            queues=[LiveQueueOut.of(queue) for queue in clinic.queues],
        )


class SearchRadiusOut(BaseModel):
    """The radius asked for and the one used."""

    requested_m: int
    applied_m: int
    capped: bool = Field(
        description="True when the server narrowed the request to its maximum."
    )


class NearbyPageOut(BaseModel):
    """One page of nearby clinics, nearest first."""

    origin: SiteLocationOut
    origin_area: AreaOut | None = Field(
        description="The area searched from, when the search started from one."
    )
    distance_basis: DistanceBasis
    radius: SearchRadiusOut
    sector: SectorFilter
    open_now: bool
    sort: DiscoverySort
    total: int
    limit: int
    offset: int
    evaluated_at: datetime
    items: list[NearbyClinicOut]

    @classmethod
    def of(cls, result: NearbyResult) -> NearbyPageOut:
        """The API shape of a search result."""
        return cls(
            origin=SiteLocationOut.of(result.origin),
            origin_area=AreaOut.of(result.origin_area) if result.origin_area else None,
            distance_basis=result.distance_basis,
            radius=SearchRadiusOut(
                requested_m=result.radius.requested_m,
                applied_m=result.radius.applied_m,
                capped=result.radius.capped,
            ),
            sector=result.sector,
            open_now=result.open_now,
            sort=result.sort,
            total=result.total,
            limit=result.limit,
            offset=result.offset,
            evaluated_at=result.evaluated_at,
            items=[NearbyClinicOut.of(clinic) for clinic in result.clinics],
        )


class SpanOut(BaseModel):
    """A stretch of wall-clock time in Africa/Johannesburg; ``closes_at`` at or before
    ``opens_at`` crosses midnight."""

    opens_at: str = Field(examples=["07:00"])
    closes_at: str = Field(examples=["16:00"])


class DayHoursOut(BaseModel):
    """One weekday's regular hours. An empty list is a day the clinic does not open."""

    weekday: int = Field(ge=0, le=6, description="Monday is 0.")
    name: str
    spans: list[SpanOut]


class ServiceOfferedOut(BaseModel):
    """One thing the clinic offers."""

    name: str
    category: ServiceCategory
    description: str | None
    requires_appointment: bool


class JoinAvailabilityOut(BaseModel):
    """Whether a patient may join a queue here now; ``reason`` is set exactly when they may not."""

    allowed: bool
    reason: str | None
    next_open_at: datetime | None
    remote_queue_count: int


class ClinicProfileOut(BaseModel):
    """Everything a patient needs to decide on one clinic (Issue 35)."""

    id: str
    slug: str
    name: str
    sector: SiteSector
    location: SiteLocationOut
    address_line: str
    suburb: str | None
    city: str
    province: SaProvince
    phone_e164: str | None
    open_status: OpenStatusOut
    today: list[SpanOut]
    week: list[DayHoursOut]
    total_waiting: int | None = Field(
        description="Everyone waiting across the clinic's queues; `null` when not measured."
    )
    queues: list[LiveQueueOut]
    services: list[ServiceOfferedOut]
    join: JoinAvailabilityOut
    evaluated_at: datetime

    @classmethod
    def of(cls, profile: ClinicProfile) -> ClinicProfileOut:
        """The API shape of a clinic profile."""

        def spans(items: tuple[TimeSpan, ...]) -> list[SpanOut]:
            return [
                SpanOut(
                    opens_at=f"{s.opens_at:%H:%M}", closes_at=f"{s.closes_at:%H:%M}"
                )
                for s in items
            ]

        return cls(
            id=profile.site_id,
            slug=profile.slug,
            name=profile.name,
            sector=profile.sector,
            location=SiteLocationOut.of(profile.location),
            address_line=profile.address_line,
            suburb=profile.suburb,
            city=profile.city,
            province=profile.province,
            phone_e164=profile.phone_e164,
            open_status=OpenStatusOut.of(profile.open_status),
            today=spans(profile.today),
            week=[
                DayHoursOut(weekday=day.weekday, name=day.name, spans=spans(day.spans))
                for day in profile.week
            ],
            total_waiting=profile.total_waiting,
            queues=[LiveQueueOut.of(queue) for queue in profile.queues],
            services=[
                ServiceOfferedOut(
                    name=service.name,
                    category=service.category,
                    description=service.description,
                    requires_appointment=service.requires_appointment,
                )
                for service in profile.services
            ],
            join=JoinAvailabilityOut(
                allowed=profile.join.allowed,
                reason=profile.join.reason,
                next_open_at=profile.join.next_open_at,
                remote_queue_count=profile.join.remote_queue_count,
            ),
            evaluated_at=profile.evaluated_at,
        )
