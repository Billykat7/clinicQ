"""clinic setup: what a clinic has confirmed for itself, and when it finished (Issue 223)

The setup checklist is derived from a clinic's own rows — its rooms, its services, its hours — so a
step finished anywhere shows as finished everywhere. These three columns are the exceptions, each
because no other row can answer its question:

* ``setup_confirmed_details`` and ``setup_confirmed_board`` are **decisions**. A clinic starts with
  the operator's typed details and with ``display_mode = number_only``; neither shows whether the
  clinic has looked and said yes, and that is what those two steps ask.
* ``setup_completed_at`` is when the clinic said it was finished, which is not the same question as
  whether everything is done.

Every existing clinic gets ``false``/``NULL``: a clinic listed before this migration has plainly
been through whatever checking it needed, and the checklist is for the ones onboarded from now on.
Nothing reads these columns unless the setup tab is opened.

Revision ID: 0048
Revises: 0047
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every object this project owns is schema-qualified; nothing goes in ``public``.
SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add what a clinic has confirmed, and when it finished."""
    op.add_column(
        "site",
        sa.Column(
            "setup_confirmed_details",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "site",
        sa.Column(
            "setup_confirmed_board",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "site",
        sa.Column("setup_completed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop them. No queue, ticket or listing depends on any of the three."""
    op.drop_column("site", "setup_completed_at", schema=SCHEMA)
    op.drop_column("site", "setup_confirmed_board", schema=SCHEMA)
    op.drop_column("site", "setup_confirmed_details", schema=SCHEMA)
