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

**What it seeds today.** One staff account per ClinicQ staff role, printed with its development
password, each (except the platform admin) assigned to the first demo clinic so the site-scoped
routes work locally. The clinics, queues and the day of ticket history (``scripts/db/demo_dataset.py``) are
built and summarised, and are written as their tables land: sites with Issue 23, queues with
Issue 25, tickets with Issue 39. Each of those issues adds its step to :func:`seed`.
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
    TicketStatus,
    UserRole,
)
from src.commons.time import APP_TIMEZONE, business_date
from src.core.config import Settings, get_settings
from src.core.security import hash_password, verify_password
from src.database.models import User, UserRoleAssignment

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


#: The clinic the demo staff work at, until Issue 23 gives sites real ids: the first demo clinic's
#: slug, used as the ``scope_id`` of their site-scoped role assignment (Issue 19). Without one, a
#: seeded account reaches no site route at all, which is the guard working, not a bug.
DEMO_SITE_ID: Final = CLINICS[0].slug

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


def seed_staff(session: Session, *, password: str) -> SeedReport:
    """Create or update one account per staff role; never a duplicate.

    An existing account (matched by email) keeps its password unless it no longer matches
    ``password``, and is brought back to the dataset's role, names and verified, active state.
    Its unscoped role assignment, the one RBAC resolves it by, is added if missing.
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
        has_assignment = session.execute(
            select(UserRoleAssignment.id).where(
                UserRoleAssignment.user_id == user.id,
                UserRoleAssignment.role == staff.role.value,
                UserRoleAssignment.scope_type.is_(None),
            )
        ).first()
        if has_assignment is None:
            session.add(UserRoleAssignment(user_id=user.id, role=staff.role.value))
        # Where they work (Issue 19). The platform admin is assigned to no clinic on purpose: the
        # operator reaches one only through the audited cross-site hatch.
        if staff.role is UserRole.PLATFORM_ADMIN:
            continue
        has_site = session.execute(
            select(UserRoleAssignment.id).where(
                UserRoleAssignment.user_id == user.id,
                UserRoleAssignment.scope_type == AssignmentScopeType.SITE.value,
                UserRoleAssignment.scope_id == DEMO_SITE_ID,
            )
        ).first()
        if has_site is None:
            session.add(
                UserRoleAssignment(
                    user_id=user.id,
                    role=staff.role.value,
                    scope_type=AssignmentScopeType.SITE.value,
                    scope_id=DEMO_SITE_ID,
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


def seed(session: Session, *, password: str) -> SeedReport:
    """Every seeding step, in dependency order. Issues 23, 25 and 39 add theirs here."""
    return seed_staff(session, password=password)


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
        report = seed(db, password=password)
        if args.dry_run:
            db.rollback()
            print("Dry run: rolled back.")

    print(
        f"Staff accounts: {report.created} created, {report.updated} updated, "
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
    print(
        f"Demo dataset (scripts/db/demo_dataset.py): {summary.clinics} clinics in Gauteng and "
        f"KwaZulu-Natal, {summary.queues} queues, {summary.tickets} tickets by 11:30 today "
        f"({summary.done} done; busiest: {summary.busiest_queue}, {summary.busiest_done} done)."
    )
    print(
        "  Written to the database as their tables land: sites (Issue 23), queues (Issue 25), "
        "tickets (Issue 39)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
