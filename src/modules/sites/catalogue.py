"""A clinic's services catalogue: what it offers, and how long each thing takes (Issue 26).

Everything that touches the database for the catalogue. Two rules, and they are the same two the
queues module holds for the same reason:

* **Deactivating a service removes it from new joins, never from history.** A ticket issued last
  month says what it was for, and a report about a service a clinic has since stopped offering must
  still resolve it. So the base query filters ``is_deleted`` and **not** ``is_active``.
* **Every query is built by the site guard**, so another clinic's catalogue is unreachable rather
  than merely unasked-for.

:func:`seed_default_catalogue` is what a newly onboarded clinic starts with: the primary-care
services the pilot clinics actually run, with the minutes the demo dataset uses. A clinic edits,
renames or deactivates any of them; what matters is that it does not start with an empty list and
therefore no wait estimate at all on its first morning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from src.commons.enums import ServiceCategory
from src.core.site_scope import SiteAccess, scoped_select
from src.database.models.clinic_service import ClinicService
from src.database.models.queue import Queue
from src.modules.sites.schemas import (
    ClinicServiceIn,
    ClinicServiceListOut,
    ClinicServiceOut,
)


class ServiceNameTakenError(ValueError):
    """This clinic already has a service with that name or slug."""


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    """One seeded service: what it is called, what kind it is, and how long it takes."""

    slug: str
    name: str
    category: ServiceCategory
    expected_minutes: int
    description: str


#: The catalogue a newly onboarded clinic starts with. These are the primary-care services the
#: pilot clinics run, and the minutes are the demo dataset's own pace per patient: F's seed data,
#: kept next to the code that writes it rather than in a spreadsheet nobody can diff.
DEFAULT_CATALOGUE: Final[tuple[CatalogueEntry, ...]] = (
    CatalogueEntry(
        "consultation",
        "General consultation",
        ServiceCategory.CONSULTATION,
        15,
        "Seeing a nurse or a doctor about something new.",
    ),
    CatalogueEntry(
        "chronic-medication",
        "Chronic medication collection",
        ServiceCategory.CHRONIC,
        5,
        "Collecting repeat medicine for a long-term condition.",
    ),
    CatalogueEntry(
        "immunisation",
        "Immunisation",
        ServiceCategory.CHILD_HEALTH,
        6,
        "Routine childhood and adult vaccinations.",
    ),
    CatalogueEntry(
        "antenatal",
        "Antenatal care",
        ServiceCategory.MATERNAL,
        25,
        "Check-ups during pregnancy.",
    ),
    CatalogueEntry(
        "hiv-tb",
        "HIV and TB services",
        ServiceCategory.HIV_TB,
        20,
        "Testing, counselling, starting treatment and follow-up.",
    ),
    CatalogueEntry(
        "screening",
        "Health screening",
        ServiceCategory.SCREENING,
        10,
        "Blood pressure, glucose and other routine checks.",
    ),
)


def _live(access: SiteAccess) -> Select[tuple[ClinicService]]:
    """The base query every read starts from: this clinic's services that were not removed.

    ``is_active`` is **not** filtered here, for the same reason it is not in the queues module: a
    deactivated service leaves new joins and stays in history, and a base query that dropped it
    would quietly empty last month's report.
    """
    return scoped_select(ClinicService, access).where(
        ClinicService.is_deleted.is_(False)
    )


def service_out(service: ClinicService) -> ClinicServiceOut:
    """One service in its API shape, with the queues that handle it."""
    return ClinicServiceOut(
        id=service.id,
        site_id=service.site_id,
        name=service.name,
        slug=service.slug,
        category=service.category_enum,
        description=service.description,
        expected_minutes=service.expected_minutes,
        display_order=service.display_order,
        requires_appointment=service.requires_appointment,
        is_active=service.is_active,
        queue_ids=sorted(queue.id for queue in service.queues),
        created_at=service.created_at,
        modified_at=service.modified_at,
    )


def list_services(
    db: Session, access: SiteAccess, *, include_inactive: bool = True
) -> ClinicServiceListOut:
    """This clinic's catalogue, in the order the clinic put it in.

    ``include_inactive`` is ``True`` for a manager's screen, which has to show a deactivated
    service in order to bring it back, and ``False`` for anything a patient chooses from.
    """
    statement = _live(access)
    if not include_inactive:
        statement = statement.where(ClinicService.is_active.is_(True))
    rows = (
        db.execute(statement.order_by(ClinicService.display_order, ClinicService.name))
        .scalars()
        .all()
    )
    return ClinicServiceListOut(
        site_id=access.site_id,
        total=len(rows),
        items=[service_out(row) for row in rows],
    )


def get_service(
    db: Session, access: SiteAccess, service_id: str
) -> ClinicService | None:
    """One of this clinic's services by id, or ``None`` — including a deactivated one."""
    return db.execute(
        _live(access).where(ClinicService.id == service_id)
    ).scalar_one_or_none()


def _name_is_free(
    db: Session,
    access: SiteAccess,
    payload: ClinicServiceIn,
    *,
    excluding: str | None = None,
) -> bool:
    """Whether this clinic may use that name and slug, ignoring the service being renamed."""
    statement = _live(access).where(
        (ClinicService.slug == payload.slug) | (ClinicService.name == payload.name)
    )
    if excluding is not None:
        statement = statement.where(ClinicService.id != excluding)
    return db.execute(statement).first() is None


def _queues_for(db: Session, access: SiteAccess, queue_ids: list[str]) -> list[Queue]:
    """The clinic's own queues among ``queue_ids``.

    Another clinic's queue id is **dropped**, not refused: the site guard has already decided what
    this caller may touch, and silently narrowing is what keeps a stale id in a browser's list from
    turning into a cross-tenant link.
    """
    if not queue_ids:
        return []
    return list(
        db.execute(scoped_select(Queue, access).where(Queue.id.in_(queue_ids)))
        .scalars()
        .all()
    )


def create_service(
    db: Session, access: SiteAccess, payload: ClinicServiceIn
) -> ClinicService:
    """Add a service to this clinic's catalogue. The caller commits.

    Raises:
        ServiceNameTakenError: If the clinic already uses that name or slug.
    """
    if not _name_is_free(db, access, payload):
        raise ServiceNameTakenError(f"This clinic already offers {payload.name!r}.")
    service = ClinicService(
        site_id=access.site_id,
        name=payload.name,
        slug=payload.slug,
        category=payload.category.value,
        description=payload.description,
        expected_minutes=payload.expected_minutes,
        display_order=payload.display_order,
        requires_appointment=payload.requires_appointment,
        is_active=payload.is_active,
    )
    service.queues = _queues_for(db, access, payload.queue_ids)
    db.add(service)
    db.flush()
    return service


def update_service(
    db: Session,
    access: SiteAccess,
    service: ClinicService,
    payload: ClinicServiceIn,
) -> ClinicService:
    """Replace a service's editable fields. The caller commits.

    Raises:
        ServiceNameTakenError: If the new name or slug belongs to another of this clinic's services.
    """
    if not _name_is_free(db, access, payload, excluding=service.id):
        raise ServiceNameTakenError(f"This clinic already offers {payload.name!r}.")
    service.name = payload.name
    service.slug = payload.slug
    service.category = payload.category.value
    service.description = payload.description
    service.expected_minutes = payload.expected_minutes
    service.display_order = payload.display_order
    service.requires_appointment = payload.requires_appointment
    service.is_active = payload.is_active
    service.queues = _queues_for(db, access, payload.queue_ids)
    db.flush()
    return service


def deactivate_service(db: Session, service: ClinicService) -> ClinicService:
    """Take a service out of new joins, keeping every ticket that named it. Caller commits."""
    service.is_active = False
    db.flush()
    return service


def seed_default_catalogue(db: Session, site_id: str) -> list[ClinicService]:
    """Give a newly onboarded clinic the catalogue in :data:`DEFAULT_CATALOGUE`. Caller commits.

    Idempotent: a clinic that already has any service is left exactly as it is, so re-running
    onboarding never duplicates an entry or resurrects one the clinic removed.
    """
    already = db.execute(
        select(ClinicService.id).where(ClinicService.site_id == site_id)
    ).first()
    if already is not None:
        return []
    created = [
        ClinicService(
            site_id=site_id,
            slug=entry.slug,
            name=entry.name,
            category=entry.category.value,
            description=entry.description,
            expected_minutes=entry.expected_minutes,
            display_order=position,
        )
        for position, entry in enumerate(DEFAULT_CATALOGUE)
    ]
    db.add_all(created)
    db.flush()
    return created


def expected_minutes_prior(
    db: Session, access: SiteAccess, service_id: str, *, fallback: int
) -> int:
    """The wait estimator's prior for one service (Issue 42 calls this until it has samples).

    Args:
        db: The session.
        access: The clinic.
        service_id: The service the patient is waiting for.
        fallback: What to use when the clinic has no such service — the queue's own
            ``expected_service_minutes``, which is the next-best number anyone has.

    Returns:
        The service's expected minutes, or ``fallback``.
    """
    service = get_service(db, access, service_id)
    return fallback if service is None else service.expected_minutes
