"""The ClinicQ demo world (Issue 8): real clinics, their queues, and a day of ticket history.

Everything here is data and pure functions, shared by ``scripts/db/seed_dev_data.py`` and
``tests/factories.py``. Nothing touches a database.

**The clinics are real.** Discovery (Issue 31) and the map (Issue 33) will be judged against where
these places actually are, so every name, coordinate and operator below was read from OpenStreetMap
on 2026-09-11 (`© OpenStreetMap contributors <https://www.openstreetmap.org/copyright>`_, ODbL), not
typed from memory; each carries its OSM element. Eight public facilities (Gauteng and KwaZulu-Natal
Departments of Health, eThekwini Municipality) and three Medicross practices, seven in Gauteng and
four in KwaZulu-Natal, from inner-city Johannesburg and Soweto to Mamelodi, Durban and Imbali.
Coordinates are WGS 84 (SRID 4326), five decimals (about a metre). One operator is not tagged in
OSM and is marked so. **Opening hours and queues are demo values**, not the clinics' own.

**The records are stubs.** Sites, queues and tickets get their tables in Issues 23, 25 and 39.
Until then :class:`SiteStub`, :class:`QueueStub` and :class:`TicketStub` are the agreed contract:
the fields those specs name, typed with the Issue 4 enums, so a screen or an estimator can be built
against them now. Each issue swaps its stub for the model, keeping the field names.
"""

import heapq
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Final

from src.commons.enums import (
    SITE_DEFAULT_DISPLAY_MODE,
    DisplayMode,
    SaProvince,
    SiteSector,
    TicketSource,
    TicketStatus,
)
from src.commons.time import APP_TIMEZONE, business_day_bounds

#: The province vocabulary. One enum for the whole project since Issue 23 gave ``sites`` a real
#: ``province`` column; re-exported under the name this module has always used so the demo data,
#: the model and every report group by the same values.
Province = SaProvince


@dataclass(frozen=True, slots=True)
class SiteStub:
    """One clinic, in the shape of Issue 23's ``sites`` model (a stub until it exists)."""

    slug: str
    name: str
    sector: SiteSector
    latitude: float
    longitude: float
    suburb: str
    city: str
    province: Province
    operator: str
    #: Where the name, position and operator were read: ``"node/<id>"`` or ``"way/<id>"`` in OSM.
    osm: str
    opens: time = time(7, 0)
    closes: time = time(16, 0)
    # Every new site starts number-only (non-negotiable 4); the seed never changes it.
    display_mode: DisplayMode = SITE_DEFAULT_DISPLAY_MODE


@dataclass(frozen=True, slots=True)
class QueueStub:
    """One named line at a site, in the shape of Issue 25's ``queues`` model."""

    site_slug: str
    slug: str
    name: str
    prefix: str
    #: Average minutes one patient takes at the front of this queue.
    service_minutes: int
    #: How many rooms or counters serve it at once.
    rooms: int = 1


@dataclass(frozen=True, slots=True)
class TicketStub:
    """One patient's place in one queue on one day, in the shape of Issue 39's ``tickets`` model.

    ``joined_at``, ``called_at`` and ``completed_at`` are aware Africa/Johannesburg datetimes.
    ``patient_ref`` is an opaque demo reference, never a phone number or a name.
    """

    queue_slug: str
    site_slug: str
    sequence: int
    number: str
    source: TicketSource
    status: TicketStatus
    joined_at: datetime
    called_at: datetime | None = None
    completed_at: datetime | None = None
    patient_ref: str | None = None

    @property
    def wait_minutes(self) -> float | None:
        """Minutes from joining to being called, once the ticket has been called."""
        if self.called_at is None:
            return None
        return (self.called_at - self.joined_at).total_seconds() / 60


@dataclass(frozen=True, slots=True)
class PatientStub:
    """A phone-first patient, in the shape Issue 17's ``patients`` model will have.

    No account and no password: an E.164 phone number is the identity. The factory's numbers sit
    in a Johannesburg landline range (``+27 10 555 ...``), which cannot receive an SMS.
    """

    phone_e164: str
    display_name: str
    whatsapp_id: str | None = None
    consent_sms: bool = True
    consent_display_name: bool = False
    languages: tuple[str, ...] = field(default=("en",))


#: The clinics, public first. Source: OpenStreetMap, retrieved 2026-09-11 (see the module docstring).
CLINICS: Final[tuple[SiteStub, ...]] = (
    SiteStub(
        slug="hillbrow-chc",
        name="Hillbrow Community Health Centre",
        sector=SiteSector.PUBLIC,
        latitude=-26.19355,
        longitude=28.04540,
        suburb="Hillbrow",
        city="Johannesburg",
        province=Province.GAUTENG,
        # Not tagged in OSM: the Gauteng Department of Health runs its community health centres.
        operator="Gauteng Department of Health",
        osm="way/58848750",
        opens=time(7, 0),
        closes=time(19, 0),
    ),
    SiteStub(
        slug="mandela-sisulu-clinic",
        name="Mandela Sisulu Clinic",
        sector=SiteSector.PUBLIC,
        latitude=-26.23523,
        longitude=27.90605,
        suburb="Orlando West, Soweto",
        city="Johannesburg",
        province=Province.GAUTENG,
        operator="Gauteng Department of Health",
        osm="way/1027130595",
    ),
    SiteStub(
        slug="mofolo-south-clinic",
        name="Mofolo South Clinic",
        sector=SiteSector.PUBLIC,
        latitude=-26.24745,
        longitude=27.88400,
        suburb="Mofolo, Soweto",
        city="Johannesburg",
        province=Province.GAUTENG,
        operator="Gauteng Department of Health",
        osm="way/1051870013",
    ),
    SiteStub(
        slug="laudium-chc",
        name="Laudium Community Health Centre",
        sector=SiteSector.PUBLIC,
        latitude=-25.78371,
        longitude=28.08720,
        suburb="Laudium",
        city="Pretoria",
        province=Province.GAUTENG,
        operator="Gauteng Department of Health",
        osm="way/725119081",
    ),
    SiteStub(
        slug="stanza-bopape-chc",
        name="Stanza Bopape Community Health Centre",
        sector=SiteSector.PUBLIC,
        latitude=-25.70588,
        longitude=28.39424,
        suburb="Mamelodi",
        city="Pretoria",
        province=Province.GAUTENG,
        operator="Gauteng Department of Health",
        osm="way/925253606",
    ),
    SiteStub(
        slug="red-hill-clinic",
        name="Red Hill Clinic",
        sector=SiteSector.PUBLIC,
        latitude=-29.77801,
        longitude=31.02011,
        suburb="Red Hill",
        city="Durban",
        province=Province.KWAZULU_NATAL,
        operator="eThekwini Municipality",
        osm="node/5265654403",
    ),
    SiteStub(
        slug="glen-earle-clinic",
        name="Glen Earle Clinic",
        sector=SiteSector.PUBLIC,
        latitude=-29.78023,
        longitude=30.97917,
        suburb="Newlands East",
        city="Durban",
        province=Province.KWAZULU_NATAL,
        operator="KwaZulu-Natal Department of Health",
        osm="node/7417427675",
    ),
    SiteStub(
        slug="imbalenhle-chc",
        name="Imbalenhle Community Health Centre",  # "Imbalenhle CHC" in OSM
        sector=SiteSector.PUBLIC,
        latitude=-29.65637,
        longitude=30.34211,
        suburb="Imbali",
        city="Pietermaritzburg",
        province=Province.KWAZULU_NATAL,
        operator="KwaZulu-Natal Department of Health",
        osm="way/792575028",
    ),
    SiteStub(
        slug="medicross-meldene",
        name="Medicross Meldene Medical and Dental Centre",
        sector=SiteSector.PRIVATE,
        latitude=-26.17691,
        longitude=28.00069,
        suburb="Melville",
        city="Johannesburg",
        province=Province.GAUTENG,
        operator="Medicross",
        osm="way/784658728",
        opens=time(8, 0),
        closes=time(17, 0),
    ),
    SiteStub(
        slug="medicross-randburg",
        name="Randburg Medicross",
        sector=SiteSector.PRIVATE,
        latitude=-26.11074,
        longitude=27.98970,
        suburb="Fontainebleau",
        city="Randburg",
        province=Province.GAUTENG,
        operator="Medicross",
        osm="way/639212536",
        opens=time(8, 0),
        closes=time(17, 0),
    ),
    SiteStub(
        slug="medicross-pinetown",
        name="Medicross Pinetown",
        sector=SiteSector.PRIVATE,
        latitude=-29.81556,
        longitude=30.86400,
        suburb="New Germany",
        city="Pinetown",
        province=Province.KWAZULU_NATAL,
        operator="Netcare Medicross",
        osm="way/791382917",
        opens=time(8, 0),
        closes=time(17, 0),
    ),
)

#: The lines a clinic runs, by sector: a public community health centre has four, a smaller public
#: clinic three, a private practice two.
#: ``(slug, name, ticket prefix, minutes per patient, rooms serving it)``.
_PUBLIC_CHC_QUEUES: Final = (
    ("triage", "Triage", "T", 4, 2),
    ("general", "General consultation", "A", 12, 4),
    ("chronic", "Chronic medication collection", "C", 3, 2),
    ("immunisation", "Immunisation", "I", 6, 1),
)
_PUBLIC_CLINIC_QUEUES: Final = _PUBLIC_CHC_QUEUES[:3]
_PRIVATE_QUEUES: Final = (
    ("reception", "Reception", "R", 3, 1),
    ("consultation", "Consultation", "D", 15, 2),
)


def queues_for(site: SiteStub) -> tuple[QueueStub, ...]:
    """The queues a site runs: four at a public health centre, three at a clinic, two private."""
    if site.sector is SiteSector.PRIVATE:
        specs = _PRIVATE_QUEUES
    elif "Community Health Centre" in site.name:
        specs = _PUBLIC_CHC_QUEUES
    else:
        specs = _PUBLIC_CLINIC_QUEUES
    return tuple(
        QueueStub(site.slug, slug, name, prefix, minutes, rooms)
        for slug, name, prefix, minutes, rooms in specs
    )


#: How each ticket arrived, weighted to what the pilot expects: most walk in, remote joins grow.
_SOURCE_WEIGHTS: Final = (
    (TicketSource.WALK_IN, 55),
    (TicketSource.WEB, 20),
    (TicketSource.USSD, 15),
    (TicketSource.WHATSAPP, 10),
)

#: Share of tickets that end without being seen: cancelled by the patient, or called and absent.
_CANCEL_RATE: Final = 0.05
_NO_SHOW_RATE: Final = 0.07

#: Demand as a share of a queue's capacity, ``(first two hours, rest of the day)``. A public clinic
#: runs slightly over capacity while the queue that formed before the doors opened comes through; a
#: private practice, which books most of its patients, never does.
_LOAD: Final = {
    SiteSector.PUBLIC: (1.1, 0.45),
    SiteSector.PRIVATE: (0.8, 0.35),
}


def ticket_history(
    site: SiteStub, queue: QueueStub, day: date, now: datetime
) -> list[TicketStub]:
    """A believable day of tickets for one queue, up to ``now``.

    Deterministic: the same site, queue and day always give the same tickets, so a screenshot, a
    test and a demo agree. Arrivals are busiest in the first two hours after opening (the queue
    that forms before the doors open) and thin out after that. Each room serves one patient at a
    time at the queue's pace, so waits grow through the morning rush and fall back: the history a
    wait estimate (Issue 42) and a heatmap (Issue 90) need. Tickets called before ``now`` are done, a
    no-show or cancelled; the rest are waiting, called or being seen.

    Args:
        site: The clinic.
        queue: One of its queues (see :func:`queues_for`).
        day: The Johannesburg service day.
        now: An aware datetime; nothing is generated after it.

    Returns:
        The tickets in sequence order.
    """
    rng = random.Random(f"{site.slug}/{queue.slug}/{day.isoformat()}")
    start, _ = business_day_bounds(day)
    opens = start.replace(hour=site.opens.hour, minute=site.opens.minute)
    closes = start.replace(hour=site.closes.hour, minute=site.closes.minute)
    last_join = min(now, closes - timedelta(minutes=30))
    # Minutes between arrivals when demand equals capacity: one patient per room per service time.
    at_capacity = queue.service_minutes / queue.rooms

    tickets: list[TicketStub] = []
    rooms_free = [opens] * queue.rooms  # when each room is next free, as a heap
    last_call = (
        opens  # calls follow the sequence: one queue, arrival order (non-negotiable 1)
    )
    moment = opens - timedelta(minutes=45)  # people queue outside before opening
    sources, weights = zip(*_SOURCE_WEIGHTS, strict=True)
    while True:
        rush_load, quiet_load = _LOAD[site.sector]
        load = rush_load if moment < opens + timedelta(hours=2) else quiet_load
        moment += timedelta(minutes=rng.expovariate(load / at_capacity))
        if moment > last_join:
            break
        sequence = len(tickets) + 1
        source = rng.choices(sources, weights=weights)[0]
        # A short pause between patients, and a service time that varies around the queue's pace.
        called = max(moment, rooms_free[0], last_call) + timedelta(
            minutes=rng.uniform(0.2, 0.8)
        )
        minutes = rng.gauss(queue.service_minutes, queue.service_minutes / 4)
        service = timedelta(minutes=max(1.0, minutes))
        status, called_at, completed_at = _outcome(rng.random(), called, service, now)
        if status is not TicketStatus.CANCELLED:
            # The room is taken from the call onwards: for the service by anyone seen, not at all
            # by a no-show, and by a projected call for someone still waiting, so calls always
            # follow the sequence.
            seen = status in {
                TicketStatus.DONE,
                TicketStatus.IN_PROGRESS,
                TicketStatus.CALLED,
            }
            heapq.heapreplace(rooms_free, called + (service if seen else timedelta(0)))
            last_call = called
        tickets.append(
            TicketStub(
                queue_slug=queue.slug,
                site_slug=site.slug,
                sequence=sequence,
                number=f"{queue.prefix}{sequence:03d}",
                source=source,
                status=status,
                joined_at=moment.astimezone(APP_TIMEZONE),
                called_at=called_at,
                completed_at=completed_at,
                patient_ref=f"demo-{site.slug}-{queue.slug}-{sequence}",
            )
        )
    return tickets


def _outcome(
    roll: float, called: datetime, service: timedelta, now: datetime
) -> tuple[TicketStatus, datetime | None, datetime | None]:
    """Where a ticket stands at ``now``: its status, and when it was called and finished."""
    if roll < _CANCEL_RATE:
        return TicketStatus.CANCELLED, None, None
    if called > now:
        return TicketStatus.WAITING, None, None
    if roll < _CANCEL_RATE + _NO_SHOW_RATE:
        return TicketStatus.NO_SHOW, called, None
    if called + service > now:
        in_room = now - called > timedelta(minutes=1)
        return (
            (TicketStatus.IN_PROGRESS if in_room else TicketStatus.CALLED),
            called,
            None,
        )
    return TicketStatus.DONE, called, called + service
