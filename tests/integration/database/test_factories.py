"""The factories make valid objects in one call, with no arguments (Issue 8).

Staff are persisted (SQLite through the ``session_factory`` fixture: the factory takes any
session); the stubs are built. Each test checks what the next issue's author will rely on.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from scripts.db.demo_dataset import CLINICS, PatientStub, SiteStub, TicketStub
from src.commons.enums import AssignmentScopeType, SiteSector, TicketStatus, UserRole
from src.core.security import verify_password
from src.database.models import User, UserRoleAssignment
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
    """Any field can be overridden; ``site_id`` adds the scoped assignment beside the unscoped one."""
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
        assert scopes == {
            (None, None),
            (AssignmentScopeType.SITE.value, "hillbrow-chc"),
        }


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


def test_a_patient_is_phone_first_and_reachable_by_no_real_mobile() -> None:
    """E.164, in the Johannesburg landline range, with no name shown on a board by default."""
    patient = PatientFactory.build()
    assert isinstance(patient, PatientStub)
    assert patient.phone_e164.startswith("+2710555") and len(patient.phone_e164) == 12
    assert patient.consent_display_name is False


def test_sites_cycle_through_the_real_clinics_with_unique_slugs() -> None:
    """Every site is one of the real clinics; past the first lap the slug gains a suffix."""
    sites = SiteFactory.build_batch(len(CLINICS) + 2)
    assert all(isinstance(s, SiteStub) for s in sites)
    assert len({s.slug for s in sites}) == len(sites)
    assert {(s.latitude, s.longitude) for s in sites} <= {
        (c.latitude, c.longitude) for c in CLINICS
    }
    assert SiteFactory.build(sector=SiteSector.PRIVATE).sector is SiteSector.PRIVATE


def test_tickets_join_a_queue_in_sequence() -> None:
    """A batch in one queue takes its prefix and site, waits, and joined in order."""
    queue = QueueFactory.build()
    tickets = TicketFactory.build_batch(3, queue=queue)
    assert all(isinstance(t, TicketStub) for t in tickets)
    assert {t.queue_slug for t in tickets} == {queue.slug}
    assert all(t.number.startswith(queue.prefix) for t in tickets)
    assert {t.status for t in tickets} == {TicketStatus.WAITING}
    assert [t.sequence for t in tickets] == sorted(t.sequence for t in tickets)
