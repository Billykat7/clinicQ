"""refresh_token: a family per sign-in, and when a token was rotated (Issue 16)

A sign-in starts a *family*: the first refresh row and every row rotated from it. Presenting a
token that was already rotated is a replay, and the answer to a replay is to revoke the whole
family, because the server cannot tell whether the thief or the owner is holding the newest token.
That needs two facts the table did not record:

* ``family_id``: the id of the family's first row, copied onto every row rotated from it;
* ``rotated_at``: set when a row is exchanged for its successor, so a replay of a rotated token can
  be told apart from a token that was simply revoked (a sign-out, an idle timeout).

**Expand only.** Both columns are nullable, so the release before this one, which inserts rows
without them, keeps working on the new schema during a deploy and after a rollback
(docs/CICD/RUNBOOK_DEPLOY.md). Existing rows are backfilled as families of one; the application
reads a missing family as the row's own id. Making ``family_id`` ``NOT NULL`` is a later release's
contract step, once no running release can write a row without it.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add ``family_id`` (indexed) and ``rotated_at``; backfill every row as its own family."""
    op.add_column(
        "refresh_token",
        sa.Column("family_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "refresh_token",
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_clinicq_refresh_token_family_id"),
        "refresh_token",
        ["family_id"],
        unique=False,
        schema=SCHEMA,
    )
    refresh_token = sa.table(
        "refresh_token",
        sa.column("id", sa.String),
        sa.column("family_id", sa.String),
        schema=SCHEMA,
    )
    op.execute(
        refresh_token.update()
        .where(refresh_token.c.family_id.is_(None))
        .values(family_id=refresh_token.c.id)
    )


def downgrade() -> None:
    """Drop both columns and the index."""
    op.drop_index(
        op.f("ix_clinicq_refresh_token_family_id"),
        table_name="refresh_token",
        schema=SCHEMA,
    )
    op.drop_column("refresh_token", "rotated_at", schema=SCHEMA)
    op.drop_column("refresh_token", "family_id", schema=SCHEMA)
