"""The RBAC matrix: every role against every resource, as the seeded database decides it (Issue 18).

``docs/architecture/rbac-matrix.md`` is the document a person reads to answer "what may a
receptionist do?". It is **generated**, from a throwaway database seeded through
:func:`~src.core.rbac_manifest_sync.sync_rbac_catalog`, the entrypoint ``make seed-rbac`` and the
deploy sequence run, and resolved through the same functions a request uses (inheritance, the
resource tree, deny-beats-allow, the grant's tier). So the document, the seed and the enforcement
cannot disagree: ``tests/test_rbac_matrix.py`` fails when the committed table is not what the code
decides, and ``make rbac-matrix`` rewrites it deliberately.

The golden decision snapshot (``tests/snapshots/rbac_decisions.txt``) pins the same decisions one
per line for machines; this is the same set, one table, for people.
"""

from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.commons.enums import UserRole
from src.core.rbac import load_resource_parent_map, resource_permissions_for_role
from src.core.rbac_snapshot import seed_snapshot_database
from src.database.models import Base
from src.database.schema import sqlite_schema_translate_map

#: The document, relative to the repository root.
MATRIX_DOC = Path("docs") / "architecture" / "rbac-matrix.md"
BEGIN_MARKER = "<!-- BEGIN GENERATED RBAC MATRIX -->"
END_MARKER = "<!-- END GENERATED RBAC MATRIX -->"

#: The columns, in the order a reader thinks about them: ClinicQ's five, then the kernel's two.
MATRIX_ROLES: tuple[UserRole, ...] = (
    UserRole.PATIENT,
    UserRole.RECEPTIONIST,
    UserRole.NURSE_DOCTOR,
    UserRole.CLINIC_MANAGER,
    UserRole.PLATFORM_ADMIN,
    UserRole.ADMIN,
    UserRole.USER,
)

#: What an empty cell means: the role holds no verb on the resource at all.
NO_ACCESS = "—"


def _cell(verb: str | None, tier: str | None) -> str:
    """One cell: ``update · assigned``, or the no-access mark."""
    if verb is None:
        return NO_ACCESS
    return f"{verb} · {tier}" if tier else verb


def matrix_rows(
    db: Session, roles: Iterable[UserRole] = MATRIX_ROLES
) -> list[list[str]]:
    """``[resource, cell per role]`` for every catalog resource, in catalog order.

    The verb is the effective one (after inheritance and the resource tree); the tier is the one
    the grant that decided carries (:func:`~src.core.scope.scope_tiers_for_roles`).
    """
    from src.core.scope import scope_tiers_for_roles

    role_list = list(roles)
    keys = list(load_resource_parent_map(db))
    verbs = {role: resource_permissions_for_role(db, role.value) for role in role_list}
    tiers = {role: scope_tiers_for_roles(db, [role.value], keys) for role in role_list}
    rows: list[list[str]] = []
    for key in sorted(keys):
        cells = [
            _cell(
                verbs[role].get(key),
                tiers[role][key].value if verbs[role].get(key) else None,
            )
            for role in role_list
        ]
        rows.append([f"`{key}`", *cells])
    return rows


def render_block(db: Session) -> str:
    """The generated block: a Markdown table between the markers."""
    header = ["Resource", *(f"`{role.value}`" for role in MATRIX_ROLES)]
    lines = [
        BEGIN_MARKER,
        "",
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
        *("| " + " | ".join(row) + " |" for row in matrix_rows(db)),
        "",
        END_MARKER,
    ]
    return "\n".join(lines)


def build_block() -> str:
    """Seed a throwaway database the way a deployment is seeded, and render the block from it."""
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
            return render_block(db)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def committed_block(doc: Path) -> str:
    """The generated block as committed in ``doc`` (markers included), or ``""``."""
    if not doc.exists():
        return ""
    text = doc.read_text(encoding="utf-8")
    if BEGIN_MARKER not in text or END_MARKER not in text:
        return ""
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    return text[start:end]


def write_block(doc: Path, block: str) -> None:
    """Replace the generated block in ``doc`` (which must already carry the markers)."""
    text = doc.read_text(encoding="utf-8")
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    doc.write_text(text[:start] + block + text[end:], encoding="utf-8")
