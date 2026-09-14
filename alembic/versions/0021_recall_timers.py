"""recall timers: when a ticket was recalled, and a clinic's and a queue's timeout (Issue 43)

Three nullable columns; nothing is rewritten.

* ``ticket.recalled_at``: when a called patient who had not arrived was recalled, by the timer or by
  staff. The no-show clock starts here, and because it is a column, **the timer's state is in the
  database, not in a worker**: a restart, a deploy or a second instance finds every deadline where
  it left it (open decision 1: an APScheduler sweep under a PostgreSQL advisory lock, not an
  ``arq`` worker).
* ``site.recall_timeout_minutes`` and ``queue.recall_timeout_minutes``: minutes to arrive before a
  recall, and again before a no-show. ``NULL`` means "use the level above": the queue's own, then the
  clinic's, then ``QUEUE_RECALL_TIMEOUT_MINUTES``. Both are checked to 1–60 minutes by the API.

Business time: ``recalled_at`` is Africa/Johannesburg, stored as ``timestamptz``.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add the three columns."""
    op.add_column(
        "ticket",
        # Business time, Africa/Johannesburg.
        sa.Column("recalled_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "queue",
        sa.Column("recall_timeout_minutes", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "site",
        sa.Column("recall_timeout_minutes", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop them. Called tickets are then timed by nothing until staff act, as before Issue 43."""
    op.drop_column("site", "recall_timeout_minutes", schema=SCHEMA)
    op.drop_column("queue", "recall_timeout_minutes", schema=SCHEMA)
    op.drop_column("ticket", "recalled_at", schema=SCHEMA)
