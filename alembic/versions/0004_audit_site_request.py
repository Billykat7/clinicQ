"""audit_event: which clinic, which request, and what the actor was (Issue 20)

Three columns the trail could not answer without:

* ``site_id`` — the clinic the action happened at, so a clinic manager can read their own trail and
  nothing else (the read API is site-scoped through Issue 19's guard);
* ``request_id`` — the id of the request that caused the row, so an audit row joins to the
  application log lines of the same request (Issue 6);
* ``actor_role`` — what the actor was acting as at the time, because a role withdrawn later must
  not change what the trail says happened.

All three are nullable, so the release before this one keeps inserting rows on the new schema
during a deploy and after a rollback. Existing rows are left NULL: a value cannot be invented for a
row whose request is long over, and the table is append-only — a backfill is exactly the UPDATE the
trigger exists to refuse.

This revision also extends that protection to ``TRUNCATE``. The row-level trigger the baseline
installs never fires for it, so ``TRUNCATE audit_event`` would have emptied an append-only table in
one statement.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value

_TRUNCATE_TRIGGER = "trg_audit_event_no_truncate"
#: The baseline's function raises for any operation, naming it; reuse it rather than write a second.
_APPEND_ONLY_FUNCTION = f"{SCHEMA}.audit_event_append_only"

_CREATE_TRUNCATE_GUARD = f"""
CREATE TRIGGER {_TRUNCATE_TRIGGER}
    BEFORE TRUNCATE ON {SCHEMA}.audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION {_APPEND_ONLY_FUNCTION}();
"""

_DROP_TRUNCATE_GUARD = (
    f"DROP TRIGGER IF EXISTS {_TRUNCATE_TRIGGER} ON {SCHEMA}.audit_event;"
)


def _is_postgres() -> bool:
    """Whether this run is against PostgreSQL (the SQLite test database skips PG-only DDL)."""
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    """Add the three columns and their indexes, and refuse TRUNCATE as well."""
    op.add_column(
        "audit_event",
        sa.Column("site_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "audit_event",
        sa.Column("request_id", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "audit_event",
        sa.Column("actor_role", sa.String(length=50), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_audit_event_site",
        "audit_event",
        ["site_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_audit_event_request_id",
        "audit_event",
        ["request_id"],
        unique=False,
        schema=SCHEMA,
    )
    if _is_postgres():
        op.execute(_CREATE_TRUNCATE_GUARD)


def downgrade() -> None:
    """Drop the TRUNCATE guard, the indexes and the columns."""
    if _is_postgres():
        op.execute(_DROP_TRUNCATE_GUARD)
    op.drop_index(
        "ix_clinicq_audit_event_request_id", table_name="audit_event", schema=SCHEMA
    )
    op.drop_index(
        "ix_clinicq_audit_event_site", table_name="audit_event", schema=SCHEMA
    )
    op.drop_column("audit_event", "actor_role", schema=SCHEMA)
    op.drop_column("audit_event", "request_id", schema=SCHEMA)
    op.drop_column("audit_event", "site_id", schema=SCHEMA)
