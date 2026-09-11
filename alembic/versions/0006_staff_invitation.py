"""staff_invitation: who was invited to which clinic, in which role (Issue 22)

One new table; nothing existing changes, so the release before this one runs unaffected.

The row is what an invitation link means: the link carries an id, and the role, the clinic and the
deadline are read from here when it is used. ``accepted_at`` is what makes the link single use, and
``revoked_at`` is what lets a manager take it back before it is used. Rows are kept after they are
used or expire — who invited whom, and when, is part of a clinic's record of its own staff.

``invited_by`` is ``RESTRICT``: a deactivated account is still the author of the invitations it
issued, so the trail stays attributable (the same reason its audit rows are not deleted).

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create the invitation table and the two indexes its reads use."""
    op.create_table(
        "staff_invitation",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=True),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("invited_by", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_user_id", sa.String(length=36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["invited_by"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_staff_invitation_invited_by_user"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_staff_invitation_accepted_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_invitation")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_staff_invitation_site",
        "staff_invitation",
        ["site_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_staff_invitation_email",
        "staff_invitation",
        ["email"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the table and its indexes."""
    op.drop_index(
        "ix_clinicq_staff_invitation_email",
        table_name="staff_invitation",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_clinicq_staff_invitation_site",
        table_name="staff_invitation",
        schema=SCHEMA,
    )
    op.drop_table("staff_invitation", schema=SCHEMA)
