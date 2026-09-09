"""Golden decision snapshot: every decision pinned, so a change to one is a diff (Issue #177, M29).

``docs/architecture/rbac-decision-transparency.md`` §7. M28 #164 shipped a change that moved console
reachability for three seeded roles, against its own acceptance criterion that reachability be
"byte-for-byte unchanged". It passed review and it passed CI. It was found later, by hand, with a
bespoke probe run across two git worktrees.

Nothing caught it because the *decision set* was never an artifact. Individual tests pin individual
decisions; nothing pinned them all, so a change that moved fifty at once read as a green build.

Once Issue #174's simulator exists, pinning the whole set is cheap — and it is the single cheapest
way to stop that class of regression permanently.

**Where this lives, and why.** In ``src/`` rather than in ``scripts/`` or ``tests/`` because two
callers must produce byte-identical output or the guard is worthless: ``make rbac-snapshot``
(``scripts/generate_rbac_snapshot.py``) writes the file, and
``tests/integration/admin/test_rbac_decision_snapshot.py`` re-derives it and diffs. One
implementation, imported by both — the same rule Issue #174 applied to the simulator itself.

**Determinism** is a hard requirement, not an aspiration: a file that wobbles between runs is a file
nobody reads. So the format is one decision per line, fixed field order, sorted; the roles come from
:func:`~src.core.rbac.default_system_roles` and the resources from the manifest-derived catalog,
both of which are code, not data; and nothing here reads a clock, a random source or an
environment variable.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.commons.enums import PermissionVerb
from src.core.nav_registry import NAV_DESTINATIONS
from src.core.rbac import (
    default_role_permissions,
    default_system_roles,
    load_resource_parent_map,
    refresh_resource_descendants_py,
    seed_resource_catalog,
    seeded_grant_scope,
)
from src.core.rbac_simulator import (
    SimulationPrincipal,
    SimulationTarget,
    simulate,
)
from src.database.models import Base, RbacRole, RolePermission
from src.database.schema import sqlite_schema_translate_map

#: Where the committed golden file lives, relative to the repository root.
SNAPSHOT_PATH = Path("tests") / "snapshots" / "rbac_decisions.txt"

#: The header written above the decisions. Fixed text, no counts and no timestamp: a header that
#: changed on every regeneration would put noise at the top of every diff, and the count belongs in
#: the *failure message* — where "1 changed" and "137 changed" call for very different reviews —
#: not in the artifact.
SNAPSHOT_HEADER = (
    "# RBAC golden decision snapshot (Issue #177, M29).",
    "#",
    "# Every seeded role against every catalog resource and verb, and against every nav surface,",
    "# resolved through the same code path a live request uses (src/core/rbac_simulator.py).",
    "#",
    "# DO NOT EDIT BY HAND. Regenerate deliberately with `make rbac-snapshot`, and review the diff",
    "# in the PR that caused it. CI re-derives and diffs; it never regenerates, because a guard",
    "# that fixes itself is a rubber stamp.",
    "#",
    "# Each line records the effective TIER alongside the verdict, so a change that quietly widens",
    "# a grant from `own` to `business` shows up even when allow/deny does not move.",
    "#",
    "# Format:",
    "#   resource | role=<role> | key=<resource> | verb=<verb> | <allow|deny> | tier=<tier>",
    "#   surface  | role=<role> | key=<surface>  | path=<href> | <allow|deny> | tier=<tier>",
)


def snapshot_roles() -> tuple[str, ...]:
    """Every role a fresh deployment seeds, sorted — the snapshot's first axis.

    Derived from the seed helpers rather than listed here, so a role added to a future deployment
    joins the snapshot in the same PR that adds it instead of being silently unpinned.
    """
    return tuple(sorted({name for name, _description in default_system_roles()}))


def seed_snapshot_database(db: Session) -> None:
    """Seed ``db`` exactly as a fresh deployment is seeded: roles, the grant matrix, the catalog.

    The snapshot is only meaningful if it pins what a *deployment* decides, so this is the shipped
    seed data — the same helpers the Alembic migrations and the integration fixtures use — never a
    reduced fixture chosen to make the file small.
    """
    for name, description in default_system_roles():
        db.add(RbacRole(name=name, description=description, is_system=True))
    seen: set[tuple[str, str]] = set()
    for helper in (default_role_permissions,):
        for role, resource_key, verb in helper():
            if (role, resource_key) in seen:
                continue
            seen.add((role, resource_key))
            db.add(
                RolePermission(
                    role=role,
                    resource=resource_key,
                    max_verb=verb.value,
                    # A fixed instant, not ``now()``: nothing in the snapshot reads this column, and
                    # a clock read in a fixture is how "deterministic across runs and machines"
                    # quietly stops being true.
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    scope=seeded_grant_scope(role).value,
                )
            )
    db.commit()
    seed_resource_catalog(db)
    db.commit()
    refresh_resource_descendants_py(db)
    db.commit()


def _resource_lines(db: Session, roles: Iterable[str]) -> list[str]:
    """Render ``role`` by ``resource`` by ``verb`` — "may this role do X"."""
    keys = sorted(load_resource_parent_map(db))
    lines: list[str] = []
    for role in roles:
        for key in keys:
            for verb in PermissionVerb:
                result = simulate(
                    db,
                    SimulationPrincipal(role=role),
                    SimulationTarget(resource=key, verb=verb.value),
                )
                lines.append(
                    f"resource | role={role} | key={key} | verb={verb.value} | "
                    f"{result.decision.value} | tier={result.effective_tier.value}"
                )
    return lines


def _surface_lines(db: Session, roles: Iterable[str]) -> list[str]:
    """Render ``role`` by nav surface — "can this role **open this page**".

    Covers both kinds of surface: a rail destination with a :class:`~src.core.nav_registry.\
NavDestination` of its own, and a console sub-tab that has none and is gated by
    :meth:`~src.core.nav_visibility.NavVisibility.can_surface` against its manifest-declared tier.

    The half M28 #164 turned on. A resource+verb-only snapshot would have recorded the tenant role's
    ``leases:read`` (which it genuinely holds, for its own lease) and said nothing at all about
    whether ``/admin/leases`` opened for them.
    """
    from src.core.rbac_manifest_registry import manifest_nav_scope_map

    lines: list[str] = []
    # Every rail destination, plus every console **sub-tab** a manifest declares (Issue #165's
    # ``NavMeta`` surfaces — the maintenance boards, the Communications Inbox/Sent/Drafts/Deleted
    # tabs, the report tabs). The sub-tabs matter as much as the rail: three of the consoles M28
    # #164 opened for a portal role were sub-tabs reached through a coarse parent grant, and a
    # snapshot of the rail alone would have recorded none of them.
    hrefs = {dest.key: dest.href for dest in NAV_DESTINATIONS}
    keys = sorted(set(hrefs) | set(manifest_nav_scope_map()))
    for role in roles:
        for key in keys:
            result = simulate(
                db, SimulationPrincipal(role=role), SimulationTarget(surface=key)
            )
            lines.append(
                f"surface  | role={role} | key={key} | path={hrefs.get(key, '-')} | "
                f"{result.decision.value} | tier={result.effective_tier.value}"
            )
    return lines


def snapshot_lines(db: Session) -> list[str]:
    """Return every pinned decision as a sorted list of lines.

    Sorted rather than emitted in enumeration order so the file's ordering survives a change to how
    the catalog or the registry happens to be iterated — a reordering diff would be indistinguishable
    from a decision diff, which defeats the point of the artifact.
    """
    roles = snapshot_roles()
    return sorted([*_resource_lines(db, roles), *_surface_lines(db, roles)])


def render_snapshot(db: Session) -> str:
    """Render the full golden file — header, then every decision, one per line."""
    return "\n".join([*SNAPSHOT_HEADER, "", *snapshot_lines(db)]) + "\n"


def build_snapshot() -> str:
    """Seed a throwaway in-memory database and render the snapshot from it.

    Self-contained on purpose: the generator and the CI guard must not depend on a developer's
    local database, which would make the golden file a record of *someone's* data rather than of
    what the code decides.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    try:
        with factory() as db:
            seed_snapshot_database(db)
            return render_snapshot(db)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def read_snapshot(path: Path | None = None) -> str:
    """Read the committed golden file, or return ``""`` when it does not exist yet."""
    target = path or SNAPSHOT_PATH
    return target.read_text(encoding="utf-8") if target.exists() else ""


def write_snapshot(text: str, path: Path | None = None) -> None:
    """Write the golden file. Called only by ``make rbac-snapshot``, never by CI."""
    target = path or SNAPSHOT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _decisions(text: str) -> dict[str, str]:
    """Index a snapshot's decision lines by everything left of the verdict.

    The key is the *question* (role, target, verb) and the value the *answer* (verdict + tier), so a
    diff can report ``tenant, /admin/leases : deny -> allow`` instead of two opaque line changes.
    """
    indexed: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3:
            continue
        indexed[" | ".join(parts[:-2])] = " ".join(parts[-2:])
    return indexed


def diff_snapshots(committed: str, derived: str) -> list[str]:
    """Return one readable line per moved decision, sorted.

    Reports moves, additions and removals separately: a decision that changed verdict or tier is a
    behaviour change to review, while an added or removed line means the *catalog* moved, which is a
    different (and usually intentional) kind of change.
    """
    before, after = _decisions(committed), _decisions(derived)
    changes: list[str] = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if old is None:
            changes.append(f"+ {key} : (new) → {new}")
        elif new is None:
            changes.append(f"- {key} : {old} → (gone)")
        else:
            changes.append(f"~ {key} : {old} → {new}")
    return changes
