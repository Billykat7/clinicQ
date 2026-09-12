"""site: display and privacy settings, defaulting to number-only (Issue 27)

Five columns on the existing ``site`` table; no new table, and nothing is dropped, so the release
before this one runs unaffected on the new schema.

**The server defaults are the safeguard, not a convenience.** Non-negotiable 4 says every new site
is created with ``display_mode = number_only`` and that no code path may change that. The column's
``server_default`` below is what makes that true even for a row inserted by a migration, a restore
or a hand-written ``INSERT`` that never touched the ORM — which is exactly the path a Python-side
default would miss. ``tests/unit/sites/test_display_defaults.py`` covers the paths that *do* go
through Python.

``display_show_comment`` defaults **false** for the same reason: a patient's reason for a visit is
health information, and it is a separate decision from showing a name.

The existing rows are the demo clinics, and the backfill puts them where a new clinic would be:
number-only, no comment, English, audio on, thirty days.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import (
    SITE_DEFAULT_BOARD_LANGUAGE,
    SITE_DEFAULT_DISPLAY_MODE,
    DbSchema,
)
from src.modules.sites.settings import REASON_RETENTION_DEFAULT_DAYS

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add the five display columns, each with the safe default already in it."""
    op.add_column(
        "site",
        sa.Column(
            "display_mode",
            sa.String(length=16),
            nullable=False,
            # The one place this member is written outside src.commons.enums: non-negotiable 4.
            server_default=SITE_DEFAULT_DISPLAY_MODE.value,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "site",
        sa.Column(
            "display_show_comment",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "site",
        sa.Column(
            "board_language",
            sa.String(length=8),
            nullable=False,
            server_default=SITE_DEFAULT_BOARD_LANGUAGE.value,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "site",
        sa.Column(
            "announce_audio", sa.Boolean(), nullable=False, server_default="true"
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "site",
        sa.Column(
            "reason_retention_days",
            sa.Integer(),
            nullable=False,
            server_default=str(REASON_RETENTION_DEFAULT_DAYS),
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the five columns. Reversible, and a downgrade loses only the clinic's own choices."""
    for column in (
        "reason_retention_days",
        "announce_audio",
        "board_language",
        "display_show_comment",
        "display_mode",
    ):
        op.drop_column("site", column, schema=SCHEMA)
