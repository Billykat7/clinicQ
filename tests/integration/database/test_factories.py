"""The factories make valid objects in one call, with no arguments (Issue 8).

Staff, patients and clinics are persisted (SQLite through the ``session_factory`` fixture: a
factory takes any session); the queue and ticket stubs are built until Issues 25 and 39 land. Each
test checks what the next issue's author will rely on.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from scripts.db.demo_dataset import CLINICS
from src.commons.enums import (
    SITE_DEFAULT_STATUS,
    AssignmentScopeType,
    QueueKind,
    SiteSector,
    TicketStatus,
    UserRole,
)
from src.commons.geo import Coordinates
from src.commons.phone import normalize_phone
from src.core.security import verify_password
from src.database.models import Patient, Queue, Site, Ticket, User, UserRoleAssignment
from tests.factories import (
    FACTORY_STAFF_PASSWORD,
    PatientFactory,
    QueueFactory,
    SiteFactory,
    StaffFactory,
    TicketFactory,
)


def test_a_staff_member_is_persisted_with_no_arguments(
    session_factory: sessionmaker[Session],
) -> None:
    """One call: a verified receptionist whose password works and whose role RBAC can resolve."""
    with session_factory() as db:
        staff = StaffFactory.create(db)
        db.commit()

        stored = db.get(User, staff.id)
        assert stored is not None
        assert stored.role == UserRole.RECEPTIONIST
        assert stored.is_verified and stored.is_active
        assert stored.email.endswith("@clinicq.example")
        assert verify_password(FACTORY_STAFF_PASSWORD, stored.password or "")
        roles = db.execute(
            select(UserRoleAssignment.role, UserRoleAssignment.scope_type).where(
                UserRoleAssignment.user_id == staff.id
            )
        ).all()
        assert roles == [(UserRole.RECEPTIONIST.value, None)]


def test_overrides_and_a_site_scope_apply(
    session_factory: sessionmaker[Session],
) -> None:
    """Any field can be overridden; ``site_id`` holds the role at that clinic and nowhere else."""
    with session_factory() as db:
        manager = StaffFactory.create(
            db,
            role=UserRole.CLINIC_MANAGER,
            first_name="Thandi",
            site_id="hillbrow-chc",
        )
        assert (
            manager.role == UserRole.CLINIC_MANAGER and manager.first_name == "Thandi"
        )
        scopes = set(
            db.execute(
                select(
                    UserRoleAssignment.scope_type, UserRoleAssignment.scope_id
                ).where(UserRoleAssignment.user_id == manager.id)
            ).all()
        )
        # One assignment, scoped to the clinic: an unscoped one beside it would make them a
        # manager at every clinic, which is what the site guard exists to prevent (Issue 19).
        assert scopes == {(AssignmentScopeType.SITE.value, "hillbrow-chc")}


def test_unique_fields_stay_unique_across_many_calls(
    session_factory: sessionmaker[Session],
) -> None:
    """Twenty staff and twenty patients: no email or number repeats, and the unique index agrees."""
    with session_factory() as db:
        for _ in range(20):
            StaffFactory.create(db)
        db.commit()
        assert db.scalar(select(func.count(func.distinct(User.email)))) == db.scalar(
            select(func.count(User.id))
        )
    numbers = [p.phone_e164 for p in PatientFactory.build_batch(20)]
    assert len(set(numbers)) == 20


def test_a_patient_is_phone_first_and_reachable_by_no_real_mobile(
    session_factory: sessionmaker[Session],
) -> None:
    """E.164 in the Johannesburg landline range, persisted as Issue 17's model, already normalised."""
    with session_factory() as db:
        patient = PatientFactory.create(db)
        assert isinstance(patient, Patient)
        assert (
            patient.phone_e164.startswith("+2710555") and len(patient.phone_e164) == 12
        )
        assert normalize_phone(patient.phone_e164) == patient.phone_e164


def test_sites_cycle_through_the_real_clinics_with_unique_slugs() -> None:
    """Every site is one of the real clinics; past the first lap the slug gains a suffix.

    Issue 23 swapped :class:`SiteStub` for the ``Site`` model, so this now checks the columns other
    modules will read: a unique slug, a coordinate that is one of the real clinics', and the
    lifecycle status every code path starts a clinic at.
    """
    sites = SiteFactory.build_batch(len(CLINICS) + 2)
    assert all(isinstance(s, Site) for s in sites)
    assert len({s.slug for s in sites}) == len(sites)
    assert {(s.location.latitude, s.location.longitude) for s in sites} <= {
        (c.latitude, c.longitude) for c in CLINICS
    }
    assert (
        SiteFactory.build(sector=SiteSector.PRIVATE).sector_enum is SiteSector.PRIVATE
    )


def test_a_site_persists_with_its_coordinate_and_the_default_status(
    session_factory: sessionmaker[Session],
) -> None:
    """``create`` writes a row: the point round-trips as a value object, and the status is ``draft``.

    The status matters to every other module: a clinic an operator types in is not visible to
    patients until the verification workflow (Issue 29) moves it, so the column's default is the
    closed one and no factory may quietly open it.
    """
    with session_factory() as db:
        site = SiteFactory.create(db)
        db.commit()
        db.refresh(site)
        clinic = next(c for c in CLINICS if site.slug.startswith(c.slug))
        assert isinstance(site.location, Coordinates)
        assert (site.location.latitude, site.location.longitude) == (
            clinic.latitude,
            clinic.longitude,
        )
        assert site.status == SITE_DEFAULT_STATUS.value


def test_tickets_join_a_queue_in_sequence(
    session_factory: sessionmaker[Session],
) -> None:
    """Created tickets take their queue's prefix and clinic, wait, and are numbered 1, 2, 3.

    Issue 39 made the ticket real: the numbers come from the database counter, as a join's do, so a
    factory-made queue day looks exactly like a real one.
    """
    with session_factory() as db:
        site = SiteFactory.create(db)
        queue = QueueFactory.create(db, site_id=site.id)
        tickets = [TicketFactory.create(db, queue=queue) for _ in range(3)]
        db.commit()

        assert all(isinstance(t, Ticket) for t in tickets)
        assert {t.queue_id for t in tickets} == {queue.id}
        assert {t.site_id for t in tickets} == {site.id}
        assert [t.number for t in tickets] == [
            f"{queue.ticket_prefix}001",
            f"{queue.ticket_prefix}002",
            f"{queue.ticket_prefix}003",
        ]
        assert {t.status for t in tickets} == {TicketStatus.WAITING.value}
        assert [t.sequence for t in tickets] == [1, 2, 3]


def test_a_queue_is_persisted_at_the_clinic_it_was_asked_for(
    session_factory: sessionmaker[Session],
) -> None:
    """``site_id`` has no default, because a queue does not exist outside a clinic (Issue 25)."""
    with session_factory() as db:
        site = SiteFactory.create(db)
        site_id = site.id
        queue = QueueFactory.create(db, site_id=site_id, kind=QueueKind.PHARMACY)
        db.commit()
        db.refresh(queue)

        assert isinstance(queue, Queue)
        assert queue.site_id == site_id
        assert queue.kind_enum is QueueKind.PHARMACY
        assert queue.is_active and queue.allows_remote_join
