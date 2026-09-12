"""site: who submitted a clinic, and who decided about it (Issue 29)

Seven nullable columns on the existing ``site`` table; no new table, nothing dropped, so the release
before this one runs unaffected on the new schema.

**There is no ``site_registration`` table**, and that is a decision rather than an omission. A
submission *is* the clinic — it has a name, a sector, an address and a coordinate from the moment it
is put forward, and a separate row would mean copying all of it across on approval and then keeping
two records of the same place. What a submission adds is a **contact** and a **decision**, and those
are these columns. The lifecycle itself is ``site.status``, which Issue 23 already created.

The contact is a person at the clinic, not a patient. It is still personal information: the field
names it shares with the kernel's redaction set (``email``, ``phone_e164``) are already covered
there, and Issue 95's data map lists it.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value

#: Every column this revision adds, in the order it adds them.
_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("contact_name", sa.String(length=120), nullable=True),
    sa.Column("contact_email", sa.String(length=255), nullable=True),
    sa.Column("contact_phone", sa.String(length=20), nullable=True),
    # Business time, Africa/Johannesburg: when it was put forward, and when it was decided.
    sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    # No foreign key: the decision outlives the account of the admin who made it.
    sa.Column("reviewed_by", sa.String(length=36), nullable=True),
    sa.Column("review_note", sa.Text(), nullable=True),
)


def upgrade() -> None:
    """Add the submission and decision columns. All nullable: an operator-typed clinic has none."""
    for column in _COLUMNS:
        op.add_column("site", column, schema=SCHEMA)
    # The queue is read by status, oldest submission first, on every page of the console.
    op.create_index(
        "ix_clinicq_site_status_submitted_at",
        "site",
        ["status", "submitted_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the index and the seven columns; a downgrade loses the submission trail, not the clinic."""
    op.drop_index(
        "ix_clinicq_site_status_submitted_at", table_name="site", schema=SCHEMA
    )
    for column in reversed(_COLUMNS):
        op.drop_column("site", column.name, schema=SCHEMA)
