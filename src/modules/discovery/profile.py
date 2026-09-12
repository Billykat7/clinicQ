"""Everything a patient needs to decide on one clinic, as plain data (Issue 35).

The clinic detail page answers four questions before a patient commits to a trip: is it open, how
long is the wait, what does it treat, and how do I get there. :func:`clinic_profile` gathers the
answers in one call, and like the search (:mod:`.service`) it returns frozen dataclasses rather than
HTML, so the web page, the USSD "clinic info" screen and the WhatsApp reply show the same facts.

Where each answer comes from, so none is invented here:

* **open or closed, and when it opens** — Issue 24's pure :func:`~src.modules.sites.hours.open_state`
  over the clinic's schedule, which is the same ``is_open_now`` / ``next_open_at`` every surface
  reads;
* **how many are waiting** — :func:`~src.modules.queues.live.published_live_queues`, per queue, which
  says "not measured" until tickets exist (Issue 39);
* **the wait** — a :class:`~src.modules.queues.live.WaitRange` from the estimator (Issue 42) once it
  exists, and ``None`` until then. Never a single number, and never a figure this module made up;
* **whether a patient may join** — :func:`~src.modules.sites.availability.join_gate`, the one gate
  every channel asks, plus whether any queue takes remote joins. The answer always carries a
  reason when it is no, because a join button that silently disappears leaves a patient guessing.

Only a publicly visible clinic has a profile: a draft, pending or suspended one answers ``None``,
exactly as it is absent from the search.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy.orm import Session

from src.commons.enums import SaProvince, ServiceCategory, SiteSector
from src.commons.geo import Coordinates
from src.commons.time import business_date, now_sast
from src.core.site_scope import published_select
from src.database.models import ClinicService, Site
from src.modules.discovery.service import OpenStatus
from src.modules.queues.live import (
    LiveQueue,
    WaitingCountReader,
    published_live_queues,
    read_waiting_counts,
    total_waiting,
)
from src.modules.sites.availability import join_gate
from src.modules.sites.discovery import publicly_visible
from src.modules.sites.hours import (
    OpeningSchedule,
    TimeSpan,
    open_state,
    published_schedules,
)

#: Monday first, as ``date.weekday()`` counts.
WEEKDAY_NAMES: Final = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

#: When every queue at an open clinic is walk-ins only, a phone cannot join any of them.
WALK_IN_ONLY: Final = "Every queue here takes walk-in patients only. Please join at the clinic's front desk."
#: A listed clinic that has not set up a queue yet.
NO_QUEUES: Final = "This clinic has not set up its queues on ClinicQ yet."


@dataclass(frozen=True, slots=True)
class DayHours:
    """One weekday's regular opening hours. No spans means closed that day."""

    weekday: int
    name: str
    spans: tuple[TimeSpan, ...]


@dataclass(frozen=True, slots=True)
class ServiceOffered:
    """One thing the clinic offers, as a patient reads it."""

    name: str
    category: ServiceCategory
    description: str | None
    requires_appointment: bool


@dataclass(frozen=True, slots=True)
class JoinAvailability:
    """Whether a patient may join a queue here right now, and why not when they may not.

    ``reason`` is ``None`` exactly when ``allowed`` is true. ``next_open_at`` lets a surface add
    "opens tomorrow at 07:00" without asking again.
    """

    allowed: bool
    reason: str | None
    next_open_at: datetime | None
    remote_queue_count: int


@dataclass(frozen=True, slots=True)
class ClinicProfile:
    """One clinic's detail page, as data."""

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
    open_status: OpenStatus
    #: Today's regular spans (a public-holiday rule already applied). A closure is in
    #: ``open_status.closure_reason``, not removed from here, so the page can say both.
    today: tuple[TimeSpan, ...]
    week: tuple[DayHours, ...]
    queues: tuple[LiveQueue, ...]
    services: tuple[ServiceOffered, ...]
    join: JoinAvailability
    evaluated_at: datetime

    @property
    def total_waiting(self) -> int | None:
        """Everyone waiting across the clinic's queues, or ``None`` when that is not measured."""
        return total_waiting(self.queues)


def _services(db: Session, site_id: str) -> tuple[ServiceOffered, ...]:
    """The clinic's active services, in its own order."""
    rows: Sequence[ClinicService] = (
        db.execute(
            published_select(ClinicService, [site_id])
            .where(
                ClinicService.is_active.is_(True), ClinicService.is_deleted.is_(False)
            )
            .order_by(ClinicService.display_order, ClinicService.name)
        )
        .scalars()
        .all()
    )
    return tuple(
        ServiceOffered(
            name=row.name,
            category=row.category_enum,
            description=row.description,
            requires_appointment=row.requires_appointment,
        )
        for row in rows
    )


def _join(
    site: Site,
    schedule: OpeningSchedule,
    queues: tuple[LiveQueue, ...],
    moment: datetime,
) -> JoinAvailability:
    """The gate's answer, narrowed by whether any queue takes a remote join.

    The clinic-level gate is asked first, because a closed clinic is closed whatever its queues
    allow; only an open clinic is then asked whether a phone can join any of its lines.
    """
    gate = join_gate(site, schedule, moment)
    remote = sum(1 for queue in queues if queue.allows_remote_join)
    if not gate.allowed:
        return JoinAvailability(False, gate.reason, gate.next_open_at, remote)
    if not queues:
        return JoinAvailability(False, NO_QUEUES, None, 0)
    if not remote:
        return JoinAvailability(False, WALK_IN_ONLY, None, 0)
    return JoinAvailability(True, None, gate.next_open_at, remote)


def clinic_profile(
    db: Session,
    slug: str,
    *,
    moment: datetime | None = None,
    reader: WaitingCountReader = read_waiting_counts,
) -> ClinicProfile | None:
    """One publicly visible clinic's profile, or ``None``.

    ``None`` covers "no such clinic" and "not visible to patients" alike, so the page cannot be used
    to learn that an unverified clinic exists.

    Args:
        db: The session.
        slug: The clinic's public handle.
        moment: When "open now" means; ``None`` is now in Johannesburg.
        reader: Where queue lengths come from (the direct read until Issue 36).
    """
    site = db.execute(publicly_visible().where(Site.slug == slug)).scalar_one_or_none()
    if site is None:
        return None
    moment = moment or now_sast()
    today = business_date(moment)
    schedule = published_schedules(db, [site.id], from_day=today).get(
        site.id, OpeningSchedule()
    )
    state = open_state(schedule, moment)
    queues = published_live_queues(db, [site.id], reader=reader).get(site.id, ())
    return ClinicProfile(
        site_id=site.id,
        slug=site.slug,
        name=site.name,
        sector=site.sector_enum,
        location=site.location,
        address_line=site.address_line,
        suburb=site.suburb,
        city=site.city,
        province=site.province_enum,
        phone_e164=site.phone_e164,
        open_status=OpenStatus(
            is_open=state.is_open,
            next_open_at=state.next_open_at,
            closure_reason=state.closure_reason,
        ),
        today=schedule.spans_on(today),
        week=tuple(
            DayHours(weekday=day, name=name, spans=tuple(schedule.weekly.get(day, ())))
            for day, name in enumerate(WEEKDAY_NAMES)
        ),
        queues=queues,
        services=_services(db, site.id),
        join=_join(site, schedule, queues, moment),
        evaluated_at=moment,
    )
