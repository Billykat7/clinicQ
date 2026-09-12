"""staff_queue_assignment: which rooms a nurse or doctor works (Issue 28)

One new table; nothing existing changes, so the release before this one runs unaffected.

**There is deliberately no ``staff_site_assignments`` table.** Site membership is a role held *at* a
site (``user_roles`` with ``scope_type='site'``, Issue 15) — the same record the tenancy guard
resolves by — and a second table answering "who works here" would be a second answer that can
disagree with the one authorization uses. Room membership is the part that does not exist yet, and
this is it.

``site_id`` is carried alongside ``queue_id`` even though the queue already knows its clinic. That
is about the tenancy guard rather than normalisation: it puts every read of this table through
``src.core.site_scope.scoped_select`` like every other site-scoped read (the guard test discovers
the model *by* that column), and it makes "everyone on a room at this clinic" one query.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create the room-assignment table, its unique pair and the per-clinic index."""
    op.create_table(
        "staff_queue_assignment",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        # Cleared rather than deleted, so "who was on Room 2 last Tuesday" stays answerable.
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        # No foreign key: the record of who assigned somebody survives that manager's removal.
        sa.Column("assigned_by", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "modified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_staff_queue_assignment_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_staff_queue_assignment_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_staff_queue_assignment_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_queue_assignment")),
        sa.UniqueConstraint(
            "user_id", "queue_id", name="uq_staff_queue_assignment_user_queue"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_staff_queue_assignment_site_user",
        "staff_queue_assignment",
        ["site_id", "user_id", "is_active"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the table and its index.

    Reversible, with one thing worth knowing: the kernel's queue-scoped ``user_roles`` rows this
    table shadows are **not** removed by a downgrade, because they belong to the kernel's own
    schema and predate this revision. Re-upgrading and re-saving an assignment brings the two back
    into step.
    """
    op.drop_index(
        "ix_clinicq_staff_queue_assignment_site_user",
        table_name="staff_queue_assignment",
        schema=SCHEMA,
    )
    op.drop_table("staff_queue_assignment", schema=SCHEMA)
