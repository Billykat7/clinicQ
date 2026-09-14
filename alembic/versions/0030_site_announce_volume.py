"""site: how loud the waiting-room board's announcements are, per clinic (Issue 60)

One column on the existing ``site`` table, a percentage from 0 to 100 held to that range by a check
constraint, with its default in the server so every existing clinic gets the volume a new one does.
Muting stays ``announce_audio``: a volume of 0 and a muted board are the same to a patient, but "off"
is a separate, clearer choice for a manager. Nothing is dropped, so the release before this one runs
unaffected on the new schema.

Revision ID: 0030
Revises: 0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import SITE_DEFAULT_ANNOUNCE_VOLUME, DbSchema

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add ``announce_volume``, a percentage, defaulting to the volume a new clinic gets."""
    op.add_column(
        "site",
        sa.Column(
            "announce_volume",
            sa.Integer(),
            nullable=False,
            server_default=str(SITE_DEFAULT_ANNOUNCE_VOLUME),
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_site_announce_volume"),
        "site",
        "announce_volume BETWEEN 0 AND 100",
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop ``announce_volume``. Reversible: a downgrade loses only each clinic's volume."""
    op.drop_constraint(
        op.f("ck_site_announce_volume"), "site", type_="check", schema=SCHEMA
    )
    op.drop_column("site", "announce_volume", schema=SCHEMA)
