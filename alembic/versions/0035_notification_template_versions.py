"""notification templates: immutable versions per language, and the version and language each message used (Issue 66)

* ``notification_template_version``: one row per version of a patient message template, per channel and
  language, unique by ``(template_key, channel, language, version)``. Rows are never updated or deleted, so
  a message can be reproduced exactly later.
* ``notification.template_version_id`` (``RESTRICT``: a version that was sent cannot be removed) and
  ``notification.language``: what each message was rendered from.
* ``patient_notification_preference.language``: the language a patient reads messages in.

Every new column is nullable; the release before this one runs unaffected. Messages recorded before this
migration keep ``NULL`` and render from the current English words.

Revision ID: 0035
Revises: 0034
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.commons.enums import DbSchema

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = DbSchema.CLINICQ.value


def upgrade() -> None:
    """Create the versions table and the three columns."""
    op.create_table(
        "notification_template_version",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_key", sa.String(length=50), nullable=False),
        sa.Column("channel", sa.String(length=10), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=120), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_by", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_template_version")),
        sa.UniqueConstraint(
            "template_key",
            "channel",
            "language",
            "version",
            name="uq_notification_template_version",
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "notification",
        sa.Column("template_version_id", sa.String(length=36), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "notification",
        sa.Column("language", sa.String(length=5), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_notification_template_version_id",
        "notification",
        "notification_template_version",
        ["template_version_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.add_column(
        "patient_notification_preference",
        sa.Column("language", sa.String(length=5), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Drop the columns and the table. Which words each message used is lost."""
    op.drop_column("patient_notification_preference", "language", schema=SCHEMA)
    op.drop_constraint(
        "fk_notification_template_version_id",
        "notification",
        type_="foreignkey",
        schema=SCHEMA,
    )
    op.drop_column("notification", "language", schema=SCHEMA)
    op.drop_column("notification", "template_version_id", schema=SCHEMA)
    op.drop_table("notification_template_version", schema=SCHEMA)
