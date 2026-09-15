"""visit feedback: one post-visit question per completed visit, and its screened answer (Issue 87)

* ``visit_feedback``: the request (unique per visit, ``uq_visit_feedback_visit``; an unguessable answer
  ``token``), whether it was sent or suppressed, and the answer: a score from 1 to 5
  (``ck_visit_feedback_score_range``), a comment stored only after screening for personal information,
  when that comment is emptied, and the channel it came through.

Nothing existing changes; the release before this one runs unaffected.

Revision ID: 0041
Revises: 0040
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every object this project owns is schema-qualified; nothing goes in ``public``.
SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create the feedback table."""
    op.create_table(
        "visit_feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("queue_id", sa.String(length=36), nullable=False),
        sa.Column("visit_id", sa.String(length=36), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        sa.Column("patient_id", sa.String(length=36), nullable=False),
        sa.Column("served_by", sa.String(length=36), nullable=True),
        sa.Column("token", sa.String(length=43), nullable=False),
        sa.Column("request_status", sa.String(length=16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "comment_redactions", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("comment_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_via", sa.String(length=16), nullable=True),
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
        sa.CheckConstraint(
            "(score IS NULL) = (answered_at IS NULL)",
            name=op.f("ck_visit_feedback_answered_with_score"),
        ),
        sa.CheckConstraint(
            "comment IS NULL OR answered_at IS NOT NULL",
            name=op.f("ck_visit_feedback_comment_only_when_answered"),
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 1 AND 5",
            name=op.f("ck_visit_feedback_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            [f"{SCHEMA}.patient.id"],
            name=op.f("fk_visit_feedback_patient_id_patient"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            [f"{SCHEMA}.queue.id"],
            name=op.f("fk_visit_feedback_queue_id_queue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["served_by"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_visit_feedback_served_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["site_id"],
            [f"{SCHEMA}.site.id"],
            name=op.f("fk_visit_feedback_site_id_site"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            [f"{SCHEMA}.ticket.id"],
            name=op.f("fk_visit_feedback_ticket_id_ticket"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["visit_id"],
            [f"{SCHEMA}.visit.id"],
            name=op.f("fk_visit_feedback_visit_id_visit"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visit_feedback")),
        sa.UniqueConstraint("token", name="uq_visit_feedback_token"),
        sa.UniqueConstraint("visit_id", name="uq_visit_feedback_visit"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_visit_feedback_patient_requested",
        "visit_feedback",
        ["patient_id", "requested_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_clinicq_visit_feedback_site_requested",
        "visit_feedback",
        ["site_id", "requested_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop it. Every request, score and comment is lost; visits and tickets are untouched."""
    op.drop_index(
        "ix_clinicq_visit_feedback_site_requested",
        table_name="visit_feedback",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_clinicq_visit_feedback_patient_requested",
        table_name="visit_feedback",
        schema=SCHEMA,
    )
    op.drop_table("visit_feedback", schema=SCHEMA)
