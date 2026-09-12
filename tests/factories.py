"""Test factories (Issue 8): one valid object per call, no arguments required, any field overridden.

    staff = StaffFactory.create(db)                          # a verified receptionist, persisted
    manager = StaffFactory.create(db, role=UserRole.CLINIC_MANAGER, site_id=site.slug)
    patient = PatientFactory.create(db)                      # +27 10 555 0001, persisted
    site = SiteFactory.create(db, sector=SiteSector.PRIVATE)   # a clinic, persisted
    queue = QueueFactory.create(db, site_id=site.id)           # a queue at that clinic
    tickets = TicketFactory.build_batch(5, queue=queue)

**Real or stub.** :class:`StaffFactory`, :class:`PatientFactory` and :class:`SiteFactory`
persist real models (Issues 15, 17, 23 and 25): ``create(session)`` writes a row.
:class:`TicketFactory` still builds the agreed stub in ``scripts/db/demo_dataset.py``
(``TicketStub``), because its table arrives with Issue 39. When it lands, its author changes the
factory's ``model`` to the new class and adds ``create``; the field names already match the spec, so
callers do not change.

**Why not factory_boy.** A dozen lines of typed Python give the three things tests need (defaults,
per-factory sequences for unique fields, keyword overrides) with nothing global: the session is
passed in, so a factory works with the SQLite ``session_factory`` fixture and the PostgreSQL
``migrated_engine`` alike, and mypy sees the type each factory returns.
"""

import itertools
from collections.abc import Iterator
from datetime import timedelta
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy.orm import Session

from scripts.db.demo_dataset import CLINICS, TicketStub, queues_for
from src.commons.enums import (
    AssignmentScopeType,
    QueueKind,
    TicketSource,
    TicketStatus,
    UserRole,
)
from src.commons.geo import Coordinates
from src.commons.time import now_sast
from src.core.security import hash_password
from src.database.models import Patient, Queue, Site, User, UserRoleAssignment

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

        With ``site_id`` the role is held **at that site** and nowhere else
        (:attr:`~src.commons.enums.AssignmentScopeType.SITE`, Issues 15 and 19): a staff member's
        clinic is a scoped role assignment, never a column, and an unscoped assignment beside it
        would make them a manager at every clinic — which is precisely what the site guard exists
        to prevent. Without ``site_id`` the assignment is unscoped, the kernel's shape for an
        account that belongs to no clinic (the platform admin, the kernel's own roles).

        Flushed, not committed: the test owns the transaction.
        """
        user = cls.build(**overrides)
        session.add(user)
        session.flush()
        session.add(
            UserRoleAssignment(
                user_id=user.id,
                role=user.role,
                scope_type=None if site_id is None else AssignmentScopeType.SITE.value,
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


class SiteFactory(Factory[Site]):
    """A clinic: the demo dataset's real clinics in turn, with a unique slug from the second lap on.

    Issue 23 swapped the stub for the model, keeping the field names the specs agreed on. The
    coordinate becomes a :class:`~src.commons.geo.Coordinates`, so a factory-made clinic is always
    somewhere a real one could be; ``status`` is the model's default (``draft``) unless a test says
    otherwise, and ``display_mode`` is not settable here at all — non-negotiable 4 owns it.
    """

    model = Site

    @classmethod
    def defaults(cls, n: int) -> dict[str, Any]:
        """The ``n``-th real clinic's columns (cycling), so coordinates are always plausible."""
        clinic = CLINICS[(n - 1) % len(CLINICS)]
        lap = (n - 1) // len(CLINICS)
        return {
            "slug": clinic.slug if not lap else f"{clinic.slug}-{lap + 1}",
            "name": clinic.name if not lap else f"{clinic.name} {lap + 1}",
            "sector": clinic.sector.value,
            "location": Coordinates(
                latitude=clinic.latitude, longitude=clinic.longitude
            ),
            "address_line": f"{clinic.name}, {clinic.suburb}",
            "suburb": clinic.suburb,
            "city": clinic.city,
            "province": clinic.province.value,
        }

    @classmethod
    def build(cls, **overrides: Any) -> Site:
        """As :meth:`Factory.build`, accepting enum members for ``sector``, ``province``, ``status``."""
        for field in ("sector", "province", "status"):
            value = overrides.get(field)
            if isinstance(value, StrEnum):
                overrides[field] = value.value
        return super().build(**overrides)

    @classmethod
    def create(cls, session: Session, **overrides: Any) -> Site:
        """Persist a clinic. Flushed, not committed: the test owns the transaction."""
        site = cls.build(**overrides)
        session.add(site)
        session.flush()
        return site


class QueueFactory(Factory[Queue]):
    """A queue at a site: a public health centre's general consultation line unless overridden.

    Issue 25 swapped the stub for the model. ``site_id`` has no sensible default — a queue does not
    exist outside a clinic — so pass one (``QueueFactory.create(db, site_id=site.id)``), which is
    also what stops a test accidentally building a queue that belongs nowhere.
    """

    model = Queue

    @classmethod
    def defaults(cls, n: int) -> dict[str, Any]:
        """The general-consultation queue of the first clinic, with a slug unique per call."""
        general = next(q for q in queues_for(CLINICS[0]) if q.slug == "general")
        return {
            "name": f"{general.name} {n}",
            "slug": f"{general.slug}-{n}",
            "kind": QueueKind.CONSULTATION.value,
            "ticket_prefix": general.prefix,
            "display_order": n,
            "expected_service_minutes": general.service_minutes,
        }

    @classmethod
    def build(cls, **overrides: Any) -> Queue:
        """As :meth:`Factory.build`, accepting a :class:`QueueKind` member for ``kind``."""
        if isinstance(overrides.get("kind"), StrEnum):
            overrides["kind"] = overrides["kind"].value
        return super().build(**overrides)

    @classmethod
    def create(cls, session: Session, *, site_id: str, **overrides: Any) -> Queue:
        """Persist a queue at ``site_id``. Flushed, not committed: the test owns the transaction."""
        queue = cls.build(site_id=site_id, **overrides)
        session.add(queue)
        session.flush()
        return queue


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
    def build(cls, *, queue: Queue | None = None, **overrides: Any) -> TicketStub:
        """As :meth:`Factory.build`; with ``queue``, the ticket belongs to it and takes its prefix.

        ``queue`` is Issue 25's model now, and the ticket is still a stub until Issue 39 — so this
        is where the two meet, and the field names the specs agreed on are what make it work.
        """
        ticket = super().build(**overrides)
        if queue is None:
            return ticket
        fields = {name: getattr(ticket, name) for name in ticket.__dataclass_fields__}
        fields |= {
            "queue_slug": queue.slug,
            "site_slug": queue.site_id,
            "number": overrides.get(
                "number", f"{queue.ticket_prefix}{ticket.sequence:03d}"
            ),
        }
        return TicketStub(**fields)

    @classmethod
    def build_batch(
        cls, size: int, *, queue: Queue | None = None, **overrides: Any
    ) -> list[TicketStub]:
        """``size`` tickets in one queue, in sequence."""
        return [cls.build(queue=queue, **overrides) for _ in range(size)]
