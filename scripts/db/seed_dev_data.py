#!/usr/bin/env python3
"""Seed the ClinicQ demo world into a development database (Issue 8).

Run after ``make migrate-up`` (and ``make seed-rbac`` for the kernel's roles)::

    make seed-dev-data
    python -m scripts.db.seed_dev_data              # the same
    python -m scripts.db.seed_dev_data --dry-run    # say what would change, write nothing

**It refuses to touch anything but a local development database**, before it opens a connection:
``ENVIRONMENT`` must be ``development``, the ``DATABASE_URL`` host must be this machine or the compose
service (``localhost``, ``127.0.0.1``, ``::1``, ``db``), and a database whose name contains
``prod`` is refused even there. There is no override flag: seeding demo accounts with a published
password into a real database is never the intent.

**It is idempotent.** Everything is matched by a natural key (a staff account by its email), so a
second run creates nothing: it updates what differs from the dataset and leaves the rest.

**What it seeds today.** The demo **clinics** (Issue 23: Gauteng, KwaZulu-Natal and Cape Town), verified so they behave like
listed ones, with their **weekly opening hours** and the country's **public holidays** for this year
and next (Issue 24); and one staff account per ClinicQ staff role, printed with its development
password, each (except the platform admin) assigned to the first demo clinic so the site-scoped
routes work locally. Their **queues** land too (Issue 25): the dataset's own
lines per clinic, so a public health centre gets four and a private practice two; and their
**services catalogue** (Issue 26), the primary-care services with the minutes each usually takes. The day of ticket
history (``scripts/db/demo_dataset.py``) is built and summarised, and is written when its table
lands with Issue 39, which adds its step to :func:`seed`.
"""

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Final

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from scripts.db.demo_dataset import CLINICS, queues_for, ticket_history
from src.commons.enums import (
    AppEnvironment,
    AssignmentScopeType,
    QueueKind,
    SiteStatus,
    TicketStatus,
    UserRole,
)
from src.commons.geo import Coordinates
from src.commons.time import APP_TIMEZONE, business_date
from src.core.config import Settings, get_settings
from src.core.security import hash_password, verify_password
from src.database.models import (
    Queue,
    Site,
    SiteOpeningHours,
    User,
    UserRoleAssignment,
)

#: Where a development database may live: this machine, or the compose stack's service name.
LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1", "db"})

#: The published development password of every seeded account. Development only: the guard above
#: is what keeps it out of any other database. ``SEED_STAFF_PASSWORD`` replaces it.
DEFAULT_STAFF_PASSWORD: Final = "clinicq-dev-only"


@dataclass(frozen=True, slots=True)
class DemoStaff:
    """One seeded staff account."""

    role: UserRole
    email: str
    first_name: str
    last_name: str


#: The clinic the demo staff work at: the first demo clinic, looked up by slug so the ``scope_id``
#: of their site-scoped role assignment (Issue 19) is the ``site`` row's real id. Without one, a
#: seeded account reaches no site route at all, which is the guard working, not a bug.
DEMO_SITE_SLUG: Final = CLINICS[0].slug

#: One account per staff role. ``.example`` is reserved by RFC 2606 and never delegated, so mail
#: to it cannot be delivered. Not ``.test``: that one is reserved too, but the email validator the
#: sign-in form uses refuses it as a special-use name, so a ``.test`` account could never sign in
#: (found by Issue 15).
DEMO_STAFF: Final[tuple[DemoStaff, ...]] = (
    DemoStaff(
        UserRole.PLATFORM_ADMIN, "platform-admin@clinicq.example", "Platform", "Admin"
    ),
    DemoStaff(UserRole.CLINIC_MANAGER, "manager@clinicq.example", "Clinic", "Manager"),
    DemoStaff(UserRole.RECEPTIONIST, "reception@clinicq.example", "Front", "Desk"),
    DemoStaff(UserRole.NURSE_DOCTOR, "nurse@clinicq.example", "Sister", "Nurse"),
)


class SeedRefusedError(RuntimeError):
    """The target is not a local development database; nothing was touched."""


def refuse_unless_local_development(settings: Settings) -> None:
    """Raise :class:`SeedRefusedError` unless ``settings`` point at a local development database.

    Checked before any connection is opened, from the settings alone.
    """
    url = make_url(settings.database_url)
    target = f"{url.host or '(no host)'}/{url.database or ''}"
    if settings.environment is not AppEnvironment.DEVELOPMENT:
        raise SeedRefusedError(
            f"ENVIRONMENT is {settings.environment.value}; demo data is seeded in development only."
        )
    if (url.host or "") not in LOCAL_HOSTS:
        raise SeedRefusedError(
            f"DATABASE_URL points at {target}, which is not a local development database "
            f"({', '.join(sorted(LOCAL_HOSTS))})."
        )
    if "prod" in (url.database or "").lower():
        raise SeedRefusedError(
            f"DATABASE_URL names database {url.database!r}, which looks like production."
        )


@dataclass(slots=True)
class SeedReport:
    """What one run did, per kind of record."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0


def seed_staff(session: Session, *, password: str, site_id: str) -> SeedReport:
    """Create or update one account per staff role; never a duplicate.

    An existing account (matched by email) keeps its password unless it no longer matches
    ``password``, and is brought back to the dataset's role, names and verified, active state.
    Its role assignment, the one RBAC resolves it by, is added if missing.

    Args:
        session: The session; the caller commits.
        password: The development password every seeded account gets.
        site_id: The ``site`` row the clinic staff hold their role at (Issue 23).
    """
    report = SeedReport()
    for staff in DEMO_STAFF:
        user = session.execute(
            select(User).where(User.email == staff.email)
        ).scalar_one_or_none()
        if user is None:
            user = User(
                email=staff.email,
                first_name=staff.first_name,
                last_name=staff.last_name,
                password=hash_password(password),
                role=staff.role.value,
                is_verified=True,
                is_active=True,
            )
            session.add(user)
            session.flush()
            report.created += 1
        else:
            wanted = {
                "first_name": staff.first_name,
                "last_name": staff.last_name,
                "role": staff.role.value,
                "is_verified": True,
                "is_active": True,
                "is_deleted": False,
            }
            changed = {k: v for k, v in wanted.items() if getattr(user, k) != v}
            if not user.password or not verify_password(password, user.password):
                changed["password"] = hash_password(password)
            for name, value in changed.items():
                setattr(user, name, value)
            if changed:
                report.updated += 1
            else:
                report.unchanged += 1
        # The assignment RBAC resolves them by. Clinic staff hold their role **at the demo clinic**
        # and nowhere else (Issue 19); the platform admin holds theirs unscoped, reaching a clinic
        # only through the audited cross-site hatch.
        at_site = staff.role is not UserRole.PLATFORM_ADMIN
        scope_type = AssignmentScopeType.SITE.value if at_site else None
        scope_id = site_id if at_site else None
        has_assignment = session.execute(
            select(UserRoleAssignment.id).where(
                UserRoleAssignment.user_id == user.id,
                UserRoleAssignment.role == staff.role.value,
                UserRoleAssignment.scope_type.is_(scope_type)
                if scope_type is None
                else UserRoleAssignment.scope_type == scope_type,
            )
        ).first()
        if has_assignment is None:
            session.add(
                UserRoleAssignment(
                    user_id=user.id,
                    role=staff.role.value,
                    scope_type=scope_type,
                    scope_id=scope_id,
                )
            )
    session.flush()
    return report


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    """The demo clinics, queues and tickets, counted for the report."""

    clinics: int
    queues: int
    tickets: int
    done: int
    busiest_queue: str
    busiest_done: int


def summarise_dataset(day: date, now: datetime) -> DatasetSummary:
    """Build the whole demo dataset for ``day`` up to ``now`` and count it."""
    queues = tickets = done = 0
    busiest = ("", 0)
    for clinic in CLINICS:
        for queue in queues_for(clinic):
            queues += 1
            history = ticket_history(clinic, queue, day, now)
            finished = sum(1 for t in history if t.status is TicketStatus.DONE)
            tickets += len(history)
            done += finished
            if finished > busiest[1]:
                busiest = (f"{clinic.name}: {queue.name}", finished)
    return DatasetSummary(len(CLINICS), queues, tickets, done, *busiest)


def seed_sites(session: Session) -> tuple[SeedReport, dict[str, str]]:
    """Create or update the demo clinics; return the report and ``{slug: site id}`` (Issue 23).

    Matched by slug, so a second run updates rather than duplicates. Every clinic is seeded
    ``verified``: locally these stand in for listed clinics, and discovery only ever returns that
    status. What the seed never touches is ``display_mode`` — non-negotiable 4 says a site is
    created ``number_only`` on every code path, including this one, so the column's default is left
    to do its job (and ``tests/unit/sites/test_display_defaults.py`` proves it, Issue 27).
    """
    report = SeedReport()
    ids: dict[str, str] = {}
    for clinic in CLINICS:
        wanted: dict[str, object] = {
            "name": clinic.name,
            "sector": clinic.sector.value,
            "status": SiteStatus.VERIFIED.value,
            "location": Coordinates(
                latitude=clinic.latitude, longitude=clinic.longitude
            ),
            "address_line": f"{clinic.name}, {clinic.suburb}",
            "suburb": clinic.suburb,
            "city": clinic.city,
            "province": clinic.province.value,
            "is_active": True,
            "is_deleted": False,
        }
        site = session.execute(
            select(Site).where(Site.slug == clinic.slug)
        ).scalar_one_or_none()
        if site is None:
            site = Site(slug=clinic.slug, **wanted)
            session.add(site)
            session.flush()
            report.created += 1
        else:
            changed = {
                name: value
                for name, value in wanted.items()
                if getattr(site, name) != value
            }
            for name, value in changed.items():
                setattr(site, name, value)
            if changed:
                report.updated += 1
            else:
                report.unchanged += 1
        ids[clinic.slug] = site.id
    session.flush()
    return report, ids


#: The demo clinics' ordinary week: weekdays only, one span a day, from the dataset's own opening
#: and closing times. Saturdays and Sundays are left with no rows, which is what "closed" looks
#: like (:mod:`src.modules.sites.hours`).
DEMO_WEEKDAYS: Final = range(5)


def seed_opening_hours(session: Session, site_ids: dict[str, str]) -> SeedReport:
    """Give every demo clinic its weekly hours (Issue 24); return the report. Caller commits.

    Idempotent by clinic: a clinic that already has any opening-hours row is left exactly as it is,
    because a manager may have edited it and a seed run must not overwrite a real decision.
    """
    report = SeedReport()
    for clinic in CLINICS:
        site_id = site_ids[clinic.slug]
        already = session.execute(
            select(SiteOpeningHours.id).where(SiteOpeningHours.site_id == site_id)
        ).first()
        if already is not None:
            report.unchanged += 1
            continue
        for weekday in DEMO_WEEKDAYS:
            session.add(
                SiteOpeningHours(
                    site_id=site_id,
                    weekday=weekday,
                    opens_at=clinic.opens,
                    closes_at=clinic.closes,
                )
            )
        report.created += 1
    session.flush()
    return report


def seed_queues(session: Session, site_ids: dict[str, str]) -> SeedReport:
    """Give every demo clinic the queues the dataset says it runs (Issue 25). Caller commits.

    Idempotent by clinic, like the opening hours: a clinic that already has any queue is left
    exactly as it is, because a manager may have renamed, reordered or deactivated one and a seed
    run must not undo that.
    """
    report = SeedReport()
    for clinic in CLINICS:
        site_id = site_ids[clinic.slug]
        already = session.execute(
            select(Queue.id).where(Queue.site_id == site_id)
        ).first()
        if already is not None:
            report.unchanged += 1
            continue
        for position, queue in enumerate(queues_for(clinic)):
            session.add(
                Queue(
                    site_id=site_id,
                    slug=queue.slug,
                    name=queue.name,
                    kind=_queue_kind(queue.slug).value,
                    ticket_prefix=queue.prefix,
                    display_order=position,
                    expected_service_minutes=queue.service_minutes,
                    # The pharmacy and chronic-medication windows are walk-in only: you cannot
                    # collect medicine from a phone, so a remote ticket for one would put somebody
                    # in a line they cannot reach the front of.
                    allows_remote_join=_queue_kind(queue.slug)
                    is not QueueKind.PHARMACY,
                )
            )
        report.created += 1
    session.flush()
    return report


def _queue_kind(slug: str) -> QueueKind:
    """The kind of line a demo queue is, from the dataset's own slug."""
    if slug == "triage":
        return QueueKind.TRIAGE
    if slug in {"pharmacy", "chronic"}:
        return QueueKind.PHARMACY
    if slug in {"general", "consultation"}:
        return QueueKind.CONSULTATION
    return QueueKind.OTHER


def seed_services(session: Session, site_ids: dict[str, str]) -> SeedReport:
    """Give every demo clinic the default services catalogue (Issue 26). Caller commits.

    Idempotent by clinic, like the queues and the hours: a clinic that already has any service is
    left alone, because a manager may have edited the catalogue and a seed run must not undo that.
    """
    from src.modules.sites.catalogue import seed_default_catalogue

    report = SeedReport()
    for clinic in CLINICS:
        created = seed_default_catalogue(session, site_ids[clinic.slug])
        if created:
            report.created += 1
        else:
            report.unchanged += 1
    session.flush()
    return report


def seed_holidays(session: Session) -> SeedReport:
    """Write this year's and next year's public holidays (Issue 24). Caller commits."""
    from src.modules.sites.hours_service import seed_public_holidays

    this_year = business_date().year
    created, unchanged = seed_public_holidays(session, (this_year, this_year + 1))
    return SeedReport(created=created, unchanged=unchanged)


def seed(session: Session, *, password: str) -> dict[str, SeedReport]:
    """Every seeding step, in dependency order. Issues 25 and 39 add theirs here.

    Clinics first: a staff member's role is held **at a site**, and opening hours hang off one, so
    the ``site`` rows have to exist before anything that points at them.

    Returns:
        ``{what was seeded: report}``, in the order it ran.
    """
    sites, ids = seed_sites(session)
    return {
        "Clinics": sites,
        "Opening hours": seed_opening_hours(session, ids),
        "Queues": seed_queues(session, ids),
        "Services": seed_services(session, ids),
        "Public holidays": seed_holidays(session),
        "Staff accounts": seed_staff(
            session, password=password, site_id=ids[DEMO_SITE_SLUG]
        ),
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """The command line: ``--dry-run`` only."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.db.seed_dev_data",
        description="Seed the ClinicQ demo world into a local development database.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change and roll back.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Refuse or seed; print what happened. Exit 0 on success, 2 when refused."""
    args = _parse_args(argv)
    settings = get_settings()
    try:
        refuse_unless_local_development(settings)
    except SeedRefusedError as exc:
        print(f"seed_dev_data: refused. {exc}", file=sys.stderr)
        print("seed_dev_data: nothing was touched.", file=sys.stderr)
        return 2

    from src.database.session import get_db_context

    password = os.environ.get("SEED_STAFF_PASSWORD") or DEFAULT_STAFF_PASSWORD
    url = make_url(settings.database_url)
    print(
        f"Seeding {url.host}:{url.port or 5432}/{url.database} (schema {settings.db_schema.value})"
    )
    with get_db_context() as db:
        reports = seed(db, password=password)
        if args.dry_run:
            db.rollback()
            print("Dry run: rolled back.")

    for what, report in reports.items():
        print(
            f"{what}: {report.created} created, {report.updated} updated, "
            f"{report.unchanged} unchanged"
        )
    print()
    print("DEVELOPMENT ONLY. These accounts exist to click around a local database:")
    for staff in DEMO_STAFF:
        print(f"  {staff.role.value:<15} {staff.email:<30} {password}")
    print(
        "  Their permissions arrive with Issue 18 (RBAC); until then they can sign in and no more."
    )
    print()
    day = business_date()
    summary = summarise_dataset(
        day, datetime.combine(day, time(11, 30), tzinfo=APP_TIMEZONE)
    )
    provinces = sorted({clinic.province.value for clinic in CLINICS})
    print(
        f"Demo dataset (scripts/db/demo_dataset.py): {summary.clinics} clinics in "
        f"{', '.join(provinces[:-1])} and {provinces[-1]}, {summary.queues} queues, {summary.tickets} tickets by 11:30 today "
        f"({summary.done} done; busiest: {summary.busiest_queue}, {summary.busiest_done} done)."
    )
    print(
        "  The clinics are in the database (Issue 23); queues and tickets are written as their "
        "tables land: queues (Issue 25), tickets (Issue 39)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
