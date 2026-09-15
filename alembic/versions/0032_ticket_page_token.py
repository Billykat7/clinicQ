"""ticket: the patient ticket page's unguessable address (Issue 68)

One nullable column, ``page_token``, unique, holding ``secrets.token_urlsafe(32)``: 43 characters from
256 random bits. The ticket page is reached by it, never by the ticket's id or number, so a link shared
with family cannot be guessed or walked from another ticket's.

Tickets still in their day when this runs (waiting, called, recalled, in progress) are given a token,
so a patient already in a queue can open their page straight away; finished tickets keep ``NULL`` and
have no page. Nothing is dropped, so the release before this one runs unaffected.

Revision ID: 0032
Revises: 0031
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import TICKET_ACTIVE_STATUSES, DbSchema

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Add ``page_token`` with its unique constraint, and give every active ticket one."""
    op.add_column(
        "ticket",
        sa.Column("page_token", sa.String(length=43), nullable=True),
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        op.f("uq_ticket_page_token"), "ticket", ["page_token"], schema=SCHEMA
    )
    ticket = sa.table(
        "ticket",
        sa.column("id", sa.String),
        sa.column("status", sa.String),
        sa.column("page_token", sa.String),
        schema=SCHEMA,
    )
    connection = op.get_bind()
    active = connection.execute(
        sa.select(ticket.c.id).where(
            ticket.c.status.in_(
                sorted(status.value for status in TICKET_ACTIVE_STATUSES)
            )
        )
    ).scalars()
    for ticket_id in active.all():
        connection.execute(
            sa.update(ticket)
            .where(ticket.c.id == ticket_id)
            .values(page_token=secrets.token_urlsafe(32))
        )


def downgrade() -> None:
    """Drop ``page_token``. Every shared ticket link stops working."""
    op.drop_constraint(
        op.f("uq_ticket_page_token"), "ticket", type_="unique", schema=SCHEMA
    )
    op.drop_column("ticket", "page_token", schema=SCHEMA)
