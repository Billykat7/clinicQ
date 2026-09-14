"""Anonymous discovery analytics: what patients searched for, looked at and joined (Issue 38).

"How many people saw us and did not come?" is a clinic manager's first question, and a view and a
join recorded as separate events answer it (:func:`conversion_for_site`). What these events must
never do is identify a patient, so the rules are written into what is stored, not left to a policy:

* **The only link between events is a rotating session reference.** A browser gets a random
  discovery session cookie (:data:`~src.core.rate_limit_deps.DISCOVERY_SESSION_COOKIE`), and an event
  stores :func:`session_ref`: an HMAC of that cookie *and the service day* under a server key. Events
  from one visit on one day group together; the reference changes at midnight, cannot be reversed into
  the cookie, and cannot link Monday's visit to Tuesday's.
* **No precise location, phone number or account.** A search stores how it was made (from a position
  or an area), its radius, sector and result count, and nothing about where the patient was. The
  table has no column that could hold more (``src/database/models/discovery_event.py``).
* **A clinic may opt out** (``site.analytics_enabled``): no view or join of it is recorded. And the
  whole feature has a switch, ``DISCOVERY_ANALYTICS_ENABLED``.
* **Recording never breaks a patient's request.** A failure to write an event is logged and dropped;
  the search, the page or the join goes ahead.
* **A join's events are part of the join.** A search or a view is recorded and committed on its own.
  :func:`record_join_started` and :func:`record_join_completed` only add their event, in a savepoint,
  to the caller's transaction, which commits it with the ticket. An earlier version committed there
  too, which committed a join's ticket halfway through ``join_queue()``: its number lock was released
  before its queue snapshot was written, and a failed event would have rolled the ticket back. The
  07:30 rush test of Issue 47 found it.

The web pages and the API call :func:`record_search` and :func:`record_clinic_viewed`; the join flow
(Issue 40) calls :func:`record_join_started` and :func:`record_join_completed`; the M12 reports read
:func:`conversion_by_site`.
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.commons.enums import (
    DiscoveryChannel,
    DiscoveryEventKind,
    DistanceBasis,
    SectorFilter,
)
from src.commons.time import business_date, now_sast
from src.core.config import Settings, get_settings
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models import DiscoveryEvent, Site

logger = logging.getLogger(__name__)

#: Entropy of a new discovery session token, in bytes.
SESSION_TOKEN_BYTES: Final = 24
#: The hex characters of the HMAC kept: 128 bits, far more than a day's sessions need to stay apart.
SESSION_REF_HEX: Final = 32
#: Separates this HMAC's key from every other use of the application secret.
_KEY_CONTEXT: Final = b"clinicq:discovery-analytics:session-ref:v1"
#: The longest range one conversion report covers, in service days.
MAX_REPORT_DAYS: Final = 366
#: The range a conversion report covers when none is asked for, in service days.
DEFAULT_REPORT_DAYS: Final = 30
#: What a clinic manager reads next to the analytics switch.
ANALYTICS_EXPLANATION: Final = (
    "When this is on, ClinicQ counts how many times patients open this clinic's page and how many "
    "go on to join a queue. It never records who they are, their phone number or where they were. "
    "When it is off, nothing about this clinic is counted from then on."
)


def new_session_token() -> str:
    """A random, unguessable discovery session token for a browser cookie."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def _key(settings: Settings) -> bytes:
    """The HMAC key: derived from the application secret, never the secret itself."""
    return hmac.new(settings.jwt_secret.encode(), _KEY_CONTEXT, hashlib.sha256).digest()


def session_ref(
    token: str | None, day: date, settings: Settings | None = None
) -> str | None:
    """The stored reference for a session on a service day, or ``None`` without a session.

    The same token gives a different reference on another day, which is what makes the reference
    rotate without the browser having to change its cookie.
    """
    if not token:
        return None
    cfg = settings or get_settings()
    digest = hmac.new(_key(cfg), f"{day.isoformat()}:{token}".encode(), hashlib.sha256)
    return digest.hexdigest()[:SESSION_REF_HEX]


def _record(
    db: Session, event: DiscoveryEvent, *, commit: bool = True
) -> DiscoveryEvent | None:
    """Write one event in its own savepoint; a failure is logged, never raised.

    With ``commit`` the event is committed at once, for a read-only request (a search, a page view)
    whose caller commits nothing, and a failure rolls that session back. Without it the event stays
    in the caller's transaction, and a failure rolls back only the savepoint: the caller's own writes
    are never committed or undone here.
    """
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
        if commit:
            db.commit()
    except SQLAlchemyError:
        if commit:
            db.rollback()
        logger.warning(
            "A discovery event could not be recorded and was dropped.", exc_info=True
        )
        return None
    return event


def _enabled(settings: Settings) -> bool:
    return settings.discovery_analytics_enabled


def record_search(
    db: Session,
    *,
    channel: DiscoveryChannel,
    session_token: str | None,
    sector: SectorFilter,
    origin_basis: DistanceBasis,
    radius_m: int,
    result_count: int,
    settings: Settings | None = None,
    moment: datetime | None = None,
) -> DiscoveryEvent | None:
    """Record that a list of clinics was shown. Where the search was made from is not recorded."""
    cfg = settings or get_settings()
    if not _enabled(cfg):
        return None
    moment = moment or now_sast()
    day = business_date(moment)
    return _record(
        db,
        DiscoveryEvent(
            kind=DiscoveryEventKind.SEARCH_PERFORMED.value,
            channel=channel.value,
            occurred_at=moment,
            service_day=day,
            session_ref=session_ref(session_token, day, cfg),
            sector=sector.value,
            origin_basis=origin_basis.value,
            radius_m=radius_m,
            result_count=result_count,
        ),
    )


def _record_about_site(
    db: Session,
    kind: DiscoveryEventKind,
    site_id: str,
    *,
    channel: DiscoveryChannel,
    session_token: str | None,
    settings: Settings | None,
    moment: datetime | None,
    commit: bool = True,
) -> DiscoveryEvent | None:
    """Record an event about one clinic, unless analytics are off or the clinic has opted out."""
    cfg = settings or get_settings()
    if not _enabled(cfg):
        return None
    opted_in = db.execute(
        select(Site.analytics_enabled).where(Site.id == site_id)
    ).scalar()
    if not opted_in:
        return None
    moment = moment or now_sast()
    day = business_date(moment)
    return _record(
        db,
        DiscoveryEvent(
            kind=kind.value,
            channel=channel.value,
            occurred_at=moment,
            service_day=day,
            session_ref=session_ref(session_token, day, cfg),
            site_id=site_id,
        ),
        commit=commit,
    )


def record_clinic_viewed(
    db: Session,
    site_id: str,
    *,
    channel: DiscoveryChannel,
    session_token: str | None,
    settings: Settings | None = None,
    moment: datetime | None = None,
) -> DiscoveryEvent | None:
    """Record that one clinic's detail was opened."""
    return _record_about_site(
        db,
        DiscoveryEventKind.CLINIC_VIEWED,
        site_id,
        channel=channel,
        session_token=session_token,
        settings=settings,
        moment=moment,
    )


def record_join_started(
    db: Session,
    site_id: str,
    *,
    channel: DiscoveryChannel,
    session_token: str | None,
    settings: Settings | None = None,
    moment: datetime | None = None,
) -> DiscoveryEvent | None:
    """Record that a patient began joining a queue at a clinic, in the caller's transaction.

    The caller commits: the event is part of the join flow's own write (Issue 40).
    """
    return _record_about_site(
        db,
        DiscoveryEventKind.JOIN_STARTED,
        site_id,
        channel=channel,
        session_token=session_token,
        settings=settings,
        moment=moment,
        commit=False,
    )


def record_join_completed(
    db: Session,
    site_id: str,
    *,
    channel: DiscoveryChannel,
    session_token: str | None,
    settings: Settings | None = None,
    moment: datetime | None = None,
) -> DiscoveryEvent | None:
    """Record that a patient got a ticket at a clinic, in the caller's transaction.

    Called by ``join_queue()`` (Issue 40), which commits the event with the ticket, so a join that
    fails after this point records no completed join either.
    """
    return _record_about_site(
        db,
        DiscoveryEventKind.JOIN_COMPLETED,
        site_id,
        channel=channel,
        session_token=session_token,
        settings=settings,
        moment=moment,
        commit=False,
    )


@dataclass(frozen=True, slots=True)
class SiteConversion:
    """One clinic's views and joins over a range of service days."""

    site_id: str
    start: date
    end: date
    views: int
    joins_started: int
    joins_completed: int

    @property
    def conversion_rate(self) -> float | None:
        """Joins completed per view, or ``None`` with no views (a rate of nothing is not zero)."""
        return None if self.views == 0 else self.joins_completed / self.views


_COUNTED: Final = (
    DiscoveryEventKind.CLINIC_VIEWED,
    DiscoveryEventKind.JOIN_STARTED,
    DiscoveryEventKind.JOIN_COMPLETED,
)


def _conversion(
    site_id: str, start: date, end: date, counts: dict[str, int]
) -> SiteConversion:
    return SiteConversion(
        site_id=site_id,
        start=start,
        end=end,
        views=counts.get(DiscoveryEventKind.CLINIC_VIEWED.value, 0),
        joins_started=counts.get(DiscoveryEventKind.JOIN_STARTED.value, 0),
        joins_completed=counts.get(DiscoveryEventKind.JOIN_COMPLETED.value, 0),
    )


def conversion_for_site(
    db: Session, access: SiteAccess, *, start: date, end: date
) -> SiteConversion:
    """One clinic's view-to-join numbers for ``start``..``end`` inclusive, through the site guard."""
    events = (
        scoped_select(DiscoveryEvent, access)
        .where(
            DiscoveryEvent.service_day >= start,
            DiscoveryEvent.service_day <= end,
            DiscoveryEvent.kind.in_([kind.value for kind in _COUNTED]),
        )
        .subquery()
    )
    rows = db.execute(select(events.c.kind, func.count()).group_by(events.c.kind)).all()
    counts: dict[str, int] = {kind: int(count) for kind, count in rows}
    return _conversion(access.site_id, start, end, counts)


def conversion_by_site(db: Session, *, start: date, end: date) -> list[SiteConversion]:
    """Every clinic's view-to-join numbers for a range: the M12 report set's source (Issue 89).

    Platform-wide by design (a report across clinics has no single clinic to scope by), so it is
    for platform and district reporting, never for a clinic-facing route.
    """
    rows = db.execute(
        select(DiscoveryEvent.site_id, DiscoveryEvent.kind, func.count())
        .where(
            DiscoveryEvent.site_id.is_not(None),
            DiscoveryEvent.service_day >= start,
            DiscoveryEvent.service_day <= end,
            DiscoveryEvent.kind.in_([kind.value for kind in _COUNTED]),
        )
        .group_by(DiscoveryEvent.site_id, DiscoveryEvent.kind)
    ).all()
    by_site: dict[str, dict[str, int]] = {}
    for site_id, kind, count in rows:
        by_site.setdefault(site_id, {})[kind] = int(count)
    return [
        _conversion(site_id, start, end, counts)
        for site_id, counts in sorted(by_site.items())
    ]
