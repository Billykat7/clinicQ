"""visit_note: private notes written in the consulting room, encrypted at rest (Issue 53)

One new table; nothing existing changes.

A row per note: the clinic, queue, ticket and visit it belongs to, the patient when there is a record,
the author (name, and id cleared if the account is removed), the note itself and when it was written,
and ``expires_at``, after which the nightly retention sweep deletes the row.

``note_text`` holds a Fernet token, never the words: the model's ``EncryptedString`` encrypts on the
way in (``src/core/encryption.py``), so the column is plain ``VARCHAR`` here and a dump of the table is
unreadable without the application's key. A note is deleted with its ticket (``CASCADE``); nothing
else refers to it.

Business time: ``created_at`` and ``expires_at`` are Africa/Johannesburg.

Revision ID: 0026
Revises: 0025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create ``visit_note`` with its keys and indexes."""
    op.create_table(
        "visit_note",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        sa.Column("visit_id", sa.String(length=36), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=True),
        sa.Column("author_user_id", sa.String(length=36), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=False),
        # A Fernet token: the words are never stored.
        sa.Column("note_text", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_visit_note_author_user_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_visit_note_queue_id_queue"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_visit_note_site_id_site"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            [f"{SCHEMA}.ticket.id"],
            name=op.f("fk_visit_note_ticket_id_ticket"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visit_note")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_visit_note_ticket",
        "visit_note",
        ["ticket_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_visit_note_patient",
        "visit_note",
        ["site_id", "patient_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_visit_note_expires",
        "visit_note",
        ["expires_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the table and every note in it. The audit rows (which never held the words) remain."""
    for index in (
        "ix_clinicq_visit_note_expires",
        "ix_clinicq_visit_note_patient",
        "ix_clinicq_visit_note_ticket",
    ):
        op.drop_index(index, table_name="visit_note", schema=SCHEMA)
    op.drop_table("visit_note", schema=SCHEMA)
