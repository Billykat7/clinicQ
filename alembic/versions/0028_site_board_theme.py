"""site: the waiting-room board's theme, chosen per clinic (Issue 59)

One column on the existing ``site`` table, with its default in the server so every existing clinic and
every row inserted outside the ORM gets the ``dim`` theme the board had before this migration. Nothing is
dropped, so the release before this one runs unaffected on the new schema.

Revision ID: 0028
Revises: 0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import SITE_DEFAULT_BOARD_THEME, DbSchema

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add ``board_theme``, defaulting to the theme every board already had."""
    op.add_column(
        "site",
        sa.Column(
            "board_theme",
            sa.String(length=16),
            nullable=False,
            server_default=SITE_DEFAULT_BOARD_THEME.value,
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop ``board_theme``. Reversible: a downgrade loses only each clinic's choice of theme."""
    op.drop_column("site", "board_theme", schema=SCHEMA)
