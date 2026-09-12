"""Test factories (Issue 8): one valid object per call, no arguments required, any field overridden.

    staff = StaffFactory.create(db)                          # a verified receptionist, persisted
    manager = StaffFactory.create(db, role=UserRole.CLINIC_MANAGER, site_id=site.slug)
    patient = PatientFactory.create(db)                      # +27 10 555 0001, persisted
    site = SiteFactory.build(sector=SiteSector.PRIVATE)
    tickets = TicketFactory.build_batch(5, queue=QueueFactory.build())

**Real or stub.** :class:`StaffFactory` persists: staff are ``User`` rows (Issue 15 keeps the
kernel's table), so ``create(session)`` writes one. The other factories build the agreed stubs in
``scripts/db/demo_dataset.py`` (``SiteStub``, ``QueueStub``, ``TicketStub``, ``PatientStub``),
because their tables arrive with Issues 23, 25 and 39. :class:`PatientFactory` persists too, since
Issue 17 brought the ``Patient`` model. When one of those lands, its author
changes the factory's ``model`` to the new class and adds ``create``; the field names already match
the specs, so callers do not change.

**Why not factory_boy.** A dozen lines of typed Python give the three things tests need (defaults,
per-factory sequences for unique fields, keyword overrides) with nothing global: the session is
passed in, so a factory works with the SQLite ``session_factory`` fixture and the PostgreSQL
``migrated_engine`` alike, and mypy sees the type each factory returns.
"""

import itertools
from collections.abc import Iterator
from datetime import timedelta
from typing import Any, ClassVar

from sqlalchemy.orm import Session

from scripts.db.demo_dataset import (
    CLINICS,
    QueueStub,
    SiteStub,
    TicketStub,
    queues_for,
)
from src.commons.enums import AssignmentScopeType, TicketSource, TicketStatus, UserRole
from src.commons.time import now_sast
from src.core.security import hash_password
from src.database.models import Patient, User, UserRoleAssignment

#: The password every factory-made staff account has, for tests that sign in. Development only.
FACTORY_STAFF_PASSWORD = "clinicq-factory-password"


class Factory[T]:
    """Build ``model`` objects from :meth:`defaults`, overridden by keyword.

    Each subclass has its own sequence, so ``n`` (1, 2, 3...) keeps unique fields unique across a
    test: two staff never share an email, two patients never share a number.
    """

    model: ClassVar[type[Any]]
    _sequence: ClassVar[Iterator[int]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Give every factory its own counter, starting at 1."""
        super().__init_subclass__(**kwargs)
        cls._sequence = itertools.count(1)

    @classmethod
    def defaults(cls, n: int) -> dict[str, Any]:
        """The fields of the ``n``-th object this factory makes. Subclasses override."""
        raise NotImplementedError

    @classmethod
    def build(cls, **overrides: Any) -> T:
        """One unsaved object: the defaults, with ``overrides`` applied on top."""
        fields = {**cls.defaults(next(cls._sequence)), **overrides}
        return cls.model(**fields)  # type: ignore[no-any-return]

    @classmethod
    def build_batch(cls, size: int, **overrides: Any) -> list[T]:
        """``size`` unsaved objects, each with the same overrides and its own sequence number."""
        return [cls.build(**overrides) for _ in range(size)]


class StaffFactory(Factory[User]):
    """A verified, active staff member with a ClinicQ role (a receptionist unless told otherwise)."""

    model = User

    @classmethod
    def defaults(cls, n: int) -> dict[str, Any]:
        """``staff<n>@clinicq.example``: reserved (RFC 2606), never delivered, and a valid sign-in."""
        return {
            "email": f"staff{n}@clinicq.example",
            "first_name": "Staff",
            "last_name": f"Member {n}",
            "password": hash_password(FACTORY_STAFF_PASSWORD),
            "role": UserRole.RECEPTIONIST.value,
            "is_verified": True,
            "is_active": True,
        }

    @classmethod
    def build(cls, **overrides: Any) -> User:
        """As :meth:`Factory.build`, accepting a ``UserRole`` member for ``role``."""
        if isinstance(overrides.get("role"), UserRole):
            overrides["role"] = overrides["role"].value
        return super().build(**overrides)

    @classmethod
    def create(
        cls, session: Session, *, site_id: str | None = None, **overrides: Any
    ) -> User:
        """Persist a staff member and the role assignment RBAC resolves them by.

        The unscoped assignment mirrors ``User.role``, as the kernel's baseline backfills for every
        user. With ``site_id``, a second assignment holds the role at that site
        (:attr:`~src.commons.enums.AssignmentScopeType.SITE`, Issue 15): a staff member's site is a
        scoped role assignment, never a column. Flushed, not committed: the test owns the
        transaction.
        """
        user = cls.build(**overrides)
        session.add(user)
        session.flush()
        session.add(UserRoleAssignment(user_id=user.id, role=user.role))
        if site_id is not None:
            session.add(
                UserRoleAssignment(
                    user_id=user.id,
                    role=user.role,
                    scope_type=AssignmentScopeType.SITE.value,
                    scope_id=site_id,
                )
            )
        session.flush()
        return user


class PatientFactory(Factory[Patient]):
    """A phone-first patient: Issue 17's ``Patient`` model. No password exists to set."""

    model = Patient

    @classmethod
    def defaults(cls, n: int) -> dict[str, Any]:
        """Numbers in ``+27 10 555 xxxx``: a Johannesburg landline range, which cannot take an SMS."""
        return {
            "phone_e164": f"+2710555{n:04d}",
            "display_name": f"Patient {n}",
        }

    @classmethod
    def create(cls, session: Session, **overrides: Any) -> Patient:
        """Persist a patient. Flushed, not committed: the test owns the transaction."""
        patient = cls.build(**overrides)
        session.add(patient)
        session.flush()
        return patient


class SiteFactory(Factory[SiteStub]):
    """A clinic: the demo dataset's real clinics in turn, with a unique slug from the second lap on."""

    model = SiteStub

    @classmethod
    def defaults(cls, n: int) -> dict[str, Any]:
        """The ``n``-th real clinic's fields (cycling), so coordinates are always plausible."""
        clinic = CLINICS[(n - 1) % len(CLINICS)]
        lap = (n - 1) // len(CLINICS)
        fields = {name: getattr(clinic, name) for name in clinic.__dataclass_fields__}
        if lap:
            fields["slug"] = f"{clinic.slug}-{lap + 1}"
        return fields


class QueueFactory(Factory[QueueStub]):
    """A queue at a site: a public health centre's general consultation line unless overridden."""

    model = QueueStub

    @classmethod
    def defaults(cls, n: int) -> dict[str, Any]:
        """The general-consultation queue of the first clinic, with a unique slug."""
        general = next(q for q in queues_for(CLINICS[0]) if q.slug == "general")
        fields = {name: getattr(general, name) for name in general.__dataclass_fields__}
        fields["slug"] = f"{general.slug}-{n}"
        return fields


class TicketFactory(Factory[TicketStub]):
    """A waiting walk-in ticket that joined a few minutes ago (a stub until Issue 39)."""

    model = TicketStub

    @classmethod
    def defaults(cls, n: int) -> dict[str, Any]:
        """Sequence ``n`` in the default queue, joined ``n`` minutes ago, still waiting."""
        return {
            "queue_slug": "general-1",
            "site_slug": CLINICS[0].slug,
            "sequence": n,
            "number": f"A{n:03d}",
            "source": TicketSource.WALK_IN,
            "status": TicketStatus.WAITING,
            "joined_at": now_sast() - timedelta(minutes=n),
        }

    @classmethod
    def build(cls, *, queue: QueueStub | None = None, **overrides: Any) -> TicketStub:
        """As :meth:`Factory.build`; with ``queue``, the ticket belongs to it and takes its prefix."""
        ticket = super().build(**overrides)
        if queue is None:
            return ticket
        fields = {name: getattr(ticket, name) for name in ticket.__dataclass_fields__}
        fields |= {
            "queue_slug": queue.slug,
            "site_slug": queue.site_slug,
            "number": overrides.get("number", f"{queue.prefix}{ticket.sequence:03d}"),
        }
        return TicketStub(**fields)

    @classmethod
    def build_batch(
        cls, size: int, *, queue: QueueStub | None = None, **overrides: Any
    ) -> list[TicketStub]:
        """``size`` tickets in one queue, in sequence."""
        return [cls.build(queue=queue, **overrides) for _ in range(size)]
