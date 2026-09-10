"""Raw INSERTs in migrations must name every NOT NULL column without a default (Issue #177 follow-up).

Migrations `0066` and `0067` each wrote `INSERT INTO properties.role_permission (...)` without the
`id` column. `role_permission.id` is `NOT NULL` with **no server default** — the ORM supplies a
Python-side `uuid4()`, which a raw `sa.text()` INSERT never goes through. So both revisions failed on
PostgreSQL with `NotNullViolation` the first time anyone ran them.

Nothing caught it because **nothing in this repository runs the migrations**. The test suite builds
its schema with `Base.metadata.create_all`, and the one CI job that does run `alembic upgrade head`
(`rbac-catalog-drift`) is gated on a semver tag push — so revisions merged since the last tag had
never been executed anywhere.

The real fix is to run the migrations in CI on every change, which needs a PostgreSQL service and is
a larger change than this. This is the cheap guard that catches the specific class in the meantime:
a static scan of every raw INSERT in every revision, asserting it names each column the table
requires and cannot fill in itself.

Static on purpose — it needs no database, so it runs in every shard on every change, which is
precisely the property the thing it replaces did not have.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

import re
from pathlib import Path

import pytest

from src.database.models import Base

VERSIONS = Path(__file__).resolve().parents[3] / "alembic" / "versions"


#: Matches the column list of a raw INSERT: ``INSERT INTO {SCHEMA}.some_table (a, b, c)``. The
#: table name is captured from the f-string form every revision in this project uses.
_INSERT = re.compile(
    r"INSERT\s+INTO\s+\{SCHEMA\}\.(?P<table>\w+)\s*\((?P<columns>[^)]*)\)",
    re.IGNORECASE,
)


def _required_columns(table_name: str) -> set[str]:
    """Return the columns an INSERT must supply: NOT NULL, no server default, not autoincrement.

    Read off the live ORM metadata rather than from a hand-kept list, so a column added to a model
    later is covered without editing this test. A Python-side `default=` deliberately does **not**
    count as filled in — that is exactly the trap: it applies to ORM inserts and never to raw SQL.
    """
    table = Base.metadata.tables.get(f"{Base.metadata.schema}.{table_name}")
    if table is None:
        return set()
    return {
        column.name
        for column in table.columns
        if not column.nullable
        and column.server_default is None
        and column.autoincrement is not True
    }


def _revisions() -> list[Path]:
    """Every migration script, sorted by revision number."""
    return sorted(path for path in VERSIONS.glob("*.py") if path.name[0].isdigit())


@pytest.mark.parametrize("revision", _revisions(), ids=lambda p: p.stem)
def test_every_raw_insert_names_the_columns_the_table_cannot_fill_in(
    revision: Path,
) -> None:
    """The assertion `0066` and `0067` would have failed.

    `role_permission.id` is the concrete case, but the rule is general: if the database cannot
    supply a value and the statement does not either, the INSERT raises — and it raises at deploy
    time, on the one machine where a failed migration is most expensive.
    """
    source = revision.read_text(encoding="utf-8")
    for match in _INSERT.finditer(source):
        table = match.group("table")
        named = {
            column.strip().strip('"')
            for column in match.group("columns").split(",")
            if column.strip()
        }
        missing = _required_columns(table) - named
        assert not missing, (
            f"{revision.name}: INSERT INTO {table} omits {sorted(missing)}, which the table "
            f"requires and cannot default. A Python-side ORM `default=` does not apply to a raw "
            f"INSERT — supply the value explicitly (see 0059/0062/0064 for the pattern)."
        )
