"""baseline — every table this project ships with

The whole schema in one revision. A generated project starts here rather than replaying the
source repo's migration history, which described tables it does not have; there is nothing to
squash and nothing to preserve, so the baseline *is* the history.

From here on, add a revision per change::

    ./scripts/db/alembic-revision.sh "add the thing"     # autogenerate against your models
    make migrate-up

Two things this revision does that autogenerate will not do for you, and which any later revision
touching them has to keep in mind:

* it **creates the schema** before anything else. Every object this project owns is
  schema-qualified (``src.commons.enums.DbSchema``); nothing goes in ``public``, which is left to
  PostGIS and to whatever else shares the database.
* it installs the **append-only trigger** on ``audit_event``. The application never updates or
  deletes an audit row, and this makes that hold against a bug or a compromised code path too —
  the guarantee is in the database, not in the code that writes to it.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.commons.enums import DbSchema
from src.database.schema import create_schema_sql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every object this project owns is schema-qualified; nothing goes in ``public``.
SCHEMA = DbSchema.CLINICQ.value

# ``audit_event`` is append-only, enforced by the database rather than by convention: the trigger
# raises on any UPDATE or DELETE, so "audit records cannot be edited or removed through the
# application" survives a bug in the code that writes them. See docs/SECURITY/.
_APPEND_ONLY_FUNCTION = f"{SCHEMA}.audit_event_append_only"
_APPEND_ONLY_TRIGGER = "trg_audit_event_append_only"

_CREATE_APPEND_ONLY_SQL = f"""
CREATE OR REPLACE FUNCTION {_APPEND_ONLY_FUNCTION}()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER {_APPEND_ONLY_TRIGGER}
    BEFORE UPDATE OR DELETE ON {SCHEMA}.audit_event
    FOR EACH ROW EXECUTE FUNCTION {_APPEND_ONLY_FUNCTION}();
"""

_DROP_APPEND_ONLY_SQL = f"""
DROP TRIGGER IF EXISTS {_APPEND_ONLY_TRIGGER} ON {SCHEMA}.audit_event;
DROP FUNCTION IF EXISTS {_APPEND_ONLY_FUNCTION}();
"""


def _is_postgres() -> bool:
    """Whether this run is against PostgreSQL (the SQLite test database skips PG-only DDL)."""
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    """Create the schema, every table, and the audit append-only trigger."""
    if _is_postgres():
        op.execute(create_schema_sql())
        # PostGIS is shared and belongs in ``public``, not in this project's schema. The
        # ``WITH SCHEMA`` is not optional: the connection's search_path puts the app schema
        # first, so without it the extension's own tables (``spatial_ref_sys``) land there and
        # every later `alembic check` proposes dropping them. Creating it is idempotent.
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public")

    op.create_table(
        "actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_actions")),
        sa.UniqueConstraint("key", name=op.f("uq_actions_key")),
        schema=SCHEMA,
    )
    op.create_table(
        "effective_role_permissions",
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("resource", sa.String(length=64), nullable=False),
        sa.Column("verb", sa.String(length=16), nullable=False),
        sa.Column("effect", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint(
            "role", "resource", "effect", name="pk_effective_role_permissions"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "message_thread",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("anchor_type", sa.String(length=20), nullable=False),
        sa.Column("anchor_id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("audience_type", sa.String(length=30), nullable=True),
        sa.Column("audience_ref", sa.String(length=36), nullable=True),
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
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_thread")),
        sa.UniqueConstraint(
            "anchor_type", "anchor_id", name="uq_message_thread_anchor"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "nav_gate_overrides",
        sa.Column("surface_key", sa.String(length=80), nullable=False),
        sa.Column("resource_key", sa.String(length=120), nullable=False),
        sa.Column("verb", sa.String(length=16), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=True),
        sa.Column(
            "scope", sa.String(length=16), server_default="business", nullable=False
        ),
        sa.Column(
            "is_admin_override", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope in ('business', 'own')",
            name=op.f("ck_nav_gate_overrides_ck_nav_gate_overrides_scope"),
        ),
        sa.CheckConstraint(
            "(verb IS NULL) != (action IS NULL)",
            name=op.f("ck_nav_gate_overrides_nav_gate_overrides_verb_xor_action"),
        ),
        sa.PrimaryKeyConstraint("surface_key", name=op.f("pk_nav_gate_overrides")),
        schema=SCHEMA,
    )
    op.create_table(
        "notification",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=10), nullable=False),
        sa.Column("template_key", sa.String(length=50), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_notification_provider_message_id",
        "notification",
        ["provider_message_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_notification_status_next_attempt_at",
        "notification",
        ["status", "next_attempt_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "paystack_event",
        sa.Column("id", sa.String(length=320), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("reference", sa.String(length=255), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paystack_event")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_paystack_event_reference",
        "paystack_event",
        ["reference"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "permission_usage",
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("resource", sa.String(length=120), nullable=False),
        sa.Column("verb", sa.String(length=32), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "hit_count",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            server_default="0",
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "role", "resource", "verb", name=op.f("pk_permission_usage")
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "permission_usage_window",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission_usage_window")),
        schema=SCHEMA,
    )
    op.create_table(
        "rbac_role",
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "is_scope_exempt", sa.Boolean(), server_default="false", nullable=False
        ),
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
        sa.PrimaryKeyConstraint("name", name=op.f("pk_rbac_role")),
        schema=SCHEMA,
    )
    op.create_table(
        "resources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            [f"{SCHEMA}.resources.id"],
            name=op.f("fk_resources_parent_id_resources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resources")),
        sa.UniqueConstraint("key", name=op.f("uq_resources_key")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_resources_parent_id",
        "resources",
        ["parent_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "stripe_event",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payment_intent_id", sa.String(length=255), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stripe_event")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_stripe_event_payment_intent_id",
        "stripe_event",
        ["payment_intent_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "user",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("role", sa.String(length=50), server_default="user", nullable=False),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("avatar_key", sa.String(length=64), nullable=True),
        sa.Column("auth_provider", sa.String(length=50), nullable=True),
        sa.Column("auth_provider_sub", sa.String(length=255), nullable=True),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_verification_email_sent_at", sa.DateTime(timezone=True), nullable=True
        ),
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
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint(
            "auth_provider", "auth_provider_sub", name="uq_user_auth_provider_sub"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f(f"ix_{SCHEMA}_user_email"),
        "user",
        ["email"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_table(
        "widget",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_widget")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_widget_is_deleted_created_at",
        "widget",
        ["is_deleted", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "alert",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("author_id", sa.String(length=36), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("audience_type", sa.String(length=30), nullable=True),
        sa.Column("audience_ref", sa.String(length=36), nullable=True),
        sa.Column("entity_type", sa.String(length=30), nullable=True),
        sa.Column("entity_id", sa.String(length=36), nullable=True),
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
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_alert_author_id_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert")),
        schema=SCHEMA,
    )
    op.create_table(
        "alert_draft",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("author_id", sa.String(length=36), nullable=False),
        sa.Column("audience_type", sa.String(length=30), nullable=True),
        sa.Column("audience_ref", sa.String(length=36), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=10), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["author_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_alert_draft_author_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_draft")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_alert_draft_author_id_modified_at",
        "alert_draft",
        ["author_id", "modified_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "audit_event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column(
            "diff",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("context", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_audit_event_actor_id_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_event")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_audit_event_actor",
        "audit_event",
        ["actor", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_audit_event_entity",
        "audit_event",
        ["entity_type", "entity_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "document",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_type", sa.String(length=30), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("document_type", sa.String(length=40), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("retention_class", sa.String(length=30), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("virus_scan_status", sa.String(length=20), nullable=False),
        sa.Column("uploaded_by", sa.String(length=36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_reason", sa.String(length=100), nullable=True),
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
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_document_uploaded_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_document_expires_at",
        "document",
        ["expires_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_document_owner",
        "document",
        ["owner_type", "owner_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "in_app_notification",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_in_app_notification_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_in_app_notification")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_in_app_notification_user_id_created_at",
        "in_app_notification",
        ["user_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_in_app_notification_user_id_read_at",
        "in_app_notification",
        ["user_id", "read_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "message",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("author_id", sa.String(length=36), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["author_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_message_author_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            [f"{SCHEMA}.message_thread.id"],
            name=op.f("fk_message_thread_id_message_thread"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_message_thread_id_created_at",
        "message",
        ["thread_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "message_draft",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("author_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=True),
        sa.Column("audience_type", sa.String(length=30), nullable=True),
        sa.Column("audience_ref", sa.String(length=36), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["author_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_message_draft_author_id_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            [f"{SCHEMA}.message_thread.id"],
            name=op.f("fk_message_draft_thread_id_message_thread"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_draft")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_message_draft_author_id_modified_at",
        "message_draft",
        ["author_id", "modified_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "message_participant",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["thread_id"],
            [f"{SCHEMA}.message_thread.id"],
            name=op.f("fk_message_participant_thread_id_message_thread"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_message_participant_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_participant")),
        sa.UniqueConstraint(
            "thread_id", "user_id", name="uq_message_participant_thread_user"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_message_participant_thread_id",
        "message_participant",
        ["thread_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_message_participant_user_id",
        "message_participant",
        ["user_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "notification_preference",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("channel_by_category", sa.JSON(), nullable=False),
        sa.Column("quiet_hours_start", sa.Time(), nullable=True),
        sa.Column("quiet_hours_end", sa.Time(), nullable=True),
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default="Africa/Johannesburg",
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_notification_preference_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_preference")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f(f"ix_{SCHEMA}_notification_preference_user_id"),
        "notification_preference",
        ["user_id"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_table(
        "permission_audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        sa.Column(
            "before",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "after",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action in ('grant', 'revoke')",
            name=op.f("ck_permission_audit_log_ck_permission_audit_log_action"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_permission_audit_log_actor_id_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission_audit_log")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_permission_audit_log_actor",
        "permission_audit_log",
        ["actor", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_permission_audit_log_target",
        "permission_audit_log",
        ["target_type", "target_id", "created_at"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "permissions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["action_id"],
            [f"{SCHEMA}.actions.id"],
            name=op.f("fk_permissions_action_id_actions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            [f"{SCHEMA}.resources.id"],
            name=op.f("fk_permissions_resource_id_resources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permissions")),
        sa.UniqueConstraint(
            "resource_id", "action_id", name="uq_permissions_resource_id"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "refresh_token",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("sign_in_ip", sa.String(length=64), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_refresh_token_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_token")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f(f"ix_{SCHEMA}_refresh_token_token_hash"),
        "refresh_token",
        ["token_hash"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        op.f(f"ix_{SCHEMA}_refresh_token_user_id"),
        "refresh_token",
        ["user_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "resource_descendants",
        sa.Column("ancestor_id", sa.String(length=36), nullable=False),
        sa.Column("descendant_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["ancestor_id"],
            [f"{SCHEMA}.resources.id"],
            name=op.f("fk_resource_descendants_ancestor_id_resources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["descendant_id"],
            [f"{SCHEMA}.resources.id"],
            name=op.f("fk_resource_descendants_descendant_id_resources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "ancestor_id", "descendant_id", name="pk_resource_descendants"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_resource_descendants_descendant_id",
        "resource_descendants",
        ["descendant_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "role_hierarchy",
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("inherits_role", sa.String(length=50), nullable=False),
        sa.CheckConstraint(
            "role <> inherits_role",
            name=op.f("ck_role_hierarchy_ck_role_hierarchy_no_self_edge"),
        ),
        sa.ForeignKeyConstraint(
            ["inherits_role"],
            [f"{SCHEMA}.rbac_role.name"],
            name=op.f("fk_role_hierarchy_inherits_role_rbac_role"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role"],
            [f"{SCHEMA}.rbac_role.name"],
            name=op.f("fk_role_hierarchy_role_rbac_role"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "role", "inherits_role", name=op.f("pk_role_hierarchy")
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "user_roles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=True),
        sa.Column("scope_id", sa.String(length=64), nullable=True),
        sa.Column("granted_by", sa.String(length=36), nullable=True),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_user_roles_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_roles")),
        sa.UniqueConstraint(
            "user_id",
            "role",
            "scope_type",
            "scope_id",
            name="uq_user_roles_user_role_scope",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_user_roles_user_id",
        "user_roles",
        ["user_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "alert_recipient",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("alert_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["alert_id"],
            [f"{SCHEMA}.alert.id"],
            name=op.f("fk_alert_recipient_alert_id_alert"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_alert_recipient_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_recipient")),
        sa.UniqueConstraint(
            "alert_id", "user_id", name="uq_alert_recipient_alert_user"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_alert_recipient_alert_id",
        "alert_recipient",
        ["alert_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_alert_recipient_user_id",
        "alert_recipient",
        ["user_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "esign_envelope",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("provider_envelope_id", sa.String(length=255), nullable=True),
        sa.Column("owner_type", sa.String(length=30), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("signed_document_id", sa.String(length=36), nullable=True),
        sa.Column("certificate_document_id", sa.String(length=36), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default="created", nullable=False
        ),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("recipient_name", sa.String(length=200), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decline_reason", sa.Text(), nullable=True),
        sa.Column("is_paper", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
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
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["certificate_document_id"],
            [f"{SCHEMA}.document.id"],
            name=op.f("fk_esign_envelope_certificate_document_id_document"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_esign_envelope_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["signed_document_id"],
            [f"{SCHEMA}.document.id"],
            name=op.f("fk_esign_envelope_signed_document_id_document"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            [f"{SCHEMA}.document.id"],
            name=op.f("fk_esign_envelope_source_document_id_document"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_esign_envelope")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_esign_envelope_owner",
        "esign_envelope",
        ["owner_type", "owner_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_esign_envelope_provider_envelope_id",
        "esign_envelope",
        ["provider_envelope_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "message_receipt",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["message_id"],
            [f"{SCHEMA}.message.id"],
            name=op.f("fk_message_receipt_message_id_message"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user.id"],
            name=op.f("fk_message_receipt_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message_receipt")),
        sa.UniqueConstraint(
            "message_id", "user_id", name="uq_message_receipt_message_user"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_message_receipt_message_id",
        "message_receipt",
        ["message_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_table(
        "role_permission",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("resource", sa.String(length=64), nullable=False),
        sa.Column("max_verb", sa.String(length=16), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=True),
        sa.Column("permission_id", sa.String(length=36), nullable=True),
        sa.Column(
            "effect", sa.String(length=16), server_default="allow", nullable=False
        ),
        sa.Column(
            "applies_to_descendants",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=16), server_default="own", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "effect in ('allow', 'deny')",
            name=op.f("ck_role_permission_ck_role_permission_effect"),
        ),
        sa.CheckConstraint(
            "scope in ('assigned', 'business', 'own')",
            name=op.f("ck_role_permission_ck_role_permission_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            [f"{SCHEMA}.permissions.id"],
            name=op.f("fk_role_permission_permission_id_permissions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_permission")),
        schema=SCHEMA,
    )
    op.create_index(
        op.f(f"ix_{SCHEMA}_role_permission_role"),
        "role_permission",
        ["role"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        "uq_role_permission_action",
        "role_permission",
        ["role", "resource", "action"],
        unique=True,
        schema=SCHEMA,
        sqlite_where=sa.text("action IS NOT NULL"),
        postgresql_where=sa.text("action IS NOT NULL"),
    )
    op.create_index(
        "uq_role_permission_cumulative",
        "role_permission",
        ["role", "resource"],
        unique=True,
        schema=SCHEMA,
        sqlite_where=sa.text("action IS NULL"),
        postgresql_where=sa.text("action IS NULL"),
    )
    op.create_table(
        "esign_event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("envelope_id", sa.String(length=36), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["envelope_id"],
            [f"{SCHEMA}.esign_envelope.id"],
            name=op.f("fk_esign_event_envelope_id_esign_envelope"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_esign_event")),
        sa.UniqueConstraint(
            "provider_event_id", name="uq_esign_event_provider_event_id"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{SCHEMA}_esign_event_envelope_id",
        "esign_event",
        ["envelope_id"],
        unique=False,
        schema=SCHEMA,
    )

    if _is_postgres():
        op.execute(_CREATE_APPEND_ONLY_SQL)


def downgrade() -> None:
    """Drop everything this revision created, newest table first."""
    if _is_postgres():
        op.execute(_DROP_APPEND_ONLY_SQL)

    op.drop_index(
        f"ix_{SCHEMA}_esign_event_envelope_id",
        table_name="esign_event",
        schema=SCHEMA,
    )
    op.drop_table("esign_event", schema=SCHEMA)
    op.drop_index(
        "uq_role_permission_cumulative",
        table_name="role_permission",
        schema=SCHEMA,
        sqlite_where=sa.text("action IS NULL"),
        postgresql_where=sa.text("action IS NULL"),
    )
    op.drop_index(
        "uq_role_permission_action",
        table_name="role_permission",
        schema=SCHEMA,
        sqlite_where=sa.text("action IS NOT NULL"),
        postgresql_where=sa.text("action IS NOT NULL"),
    )
    op.drop_index(
        op.f(f"ix_{SCHEMA}_role_permission_role"),
        table_name="role_permission",
        schema=SCHEMA,
    )
    op.drop_table("role_permission", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_message_receipt_message_id",
        table_name="message_receipt",
        schema=SCHEMA,
    )
    op.drop_table("message_receipt", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_esign_envelope_provider_envelope_id",
        table_name="esign_envelope",
        schema=SCHEMA,
    )
    op.drop_index(
        f"ix_{SCHEMA}_esign_envelope_owner",
        table_name="esign_envelope",
        schema=SCHEMA,
    )
    op.drop_table("esign_envelope", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_alert_recipient_user_id",
        table_name="alert_recipient",
        schema=SCHEMA,
    )
    op.drop_index(
        f"ix_{SCHEMA}_alert_recipient_alert_id",
        table_name="alert_recipient",
        schema=SCHEMA,
    )
    op.drop_table("alert_recipient", schema=SCHEMA)
    op.drop_index("ix_user_roles_user_id", table_name="user_roles", schema=SCHEMA)
    op.drop_table("user_roles", schema=SCHEMA)
    op.drop_table("role_hierarchy", schema=SCHEMA)
    op.drop_index(
        "ix_resource_descendants_descendant_id",
        table_name="resource_descendants",
        schema=SCHEMA,
    )
    op.drop_table("resource_descendants", schema=SCHEMA)
    op.drop_index(
        op.f(f"ix_{SCHEMA}_refresh_token_user_id"),
        table_name="refresh_token",
        schema=SCHEMA,
    )
    op.drop_index(
        op.f(f"ix_{SCHEMA}_refresh_token_token_hash"),
        table_name="refresh_token",
        schema=SCHEMA,
    )
    op.drop_table("refresh_token", schema=SCHEMA)
    op.drop_table("permissions", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_permission_audit_log_target",
        table_name="permission_audit_log",
        schema=SCHEMA,
    )
    op.drop_index(
        f"ix_{SCHEMA}_permission_audit_log_actor",
        table_name="permission_audit_log",
        schema=SCHEMA,
    )
    op.drop_table("permission_audit_log", schema=SCHEMA)
    op.drop_index(
        op.f(f"ix_{SCHEMA}_notification_preference_user_id"),
        table_name="notification_preference",
        schema=SCHEMA,
    )
    op.drop_table("notification_preference", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_message_participant_user_id",
        table_name="message_participant",
        schema=SCHEMA,
    )
    op.drop_index(
        f"ix_{SCHEMA}_message_participant_thread_id",
        table_name="message_participant",
        schema=SCHEMA,
    )
    op.drop_table("message_participant", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_message_draft_author_id_modified_at",
        table_name="message_draft",
        schema=SCHEMA,
    )
    op.drop_table("message_draft", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_message_thread_id_created_at",
        table_name="message",
        schema=SCHEMA,
    )
    op.drop_table("message", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_in_app_notification_user_id_read_at",
        table_name="in_app_notification",
        schema=SCHEMA,
    )
    op.drop_index(
        f"ix_{SCHEMA}_in_app_notification_user_id_created_at",
        table_name="in_app_notification",
        schema=SCHEMA,
    )
    op.drop_table("in_app_notification", schema=SCHEMA)
    op.drop_index(f"ix_{SCHEMA}_document_owner", table_name="document", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_document_expires_at", table_name="document", schema=SCHEMA
    )
    op.drop_table("document", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_audit_event_entity",
        table_name="audit_event",
        schema=SCHEMA,
    )
    op.drop_index(
        f"ix_{SCHEMA}_audit_event_actor", table_name="audit_event", schema=SCHEMA
    )
    op.drop_table("audit_event", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_alert_draft_author_id_modified_at",
        table_name="alert_draft",
        schema=SCHEMA,
    )
    op.drop_table("alert_draft", schema=SCHEMA)
    op.drop_table("alert", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_widget_is_deleted_created_at",
        table_name="widget",
        schema=SCHEMA,
    )
    op.drop_table("widget", schema=SCHEMA)
    op.drop_index(op.f(f"ix_{SCHEMA}_user_email"), table_name="user", schema=SCHEMA)
    op.drop_table("user", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_stripe_event_payment_intent_id",
        table_name="stripe_event",
        schema=SCHEMA,
    )
    op.drop_table("stripe_event", schema=SCHEMA)
    op.drop_index("ix_resources_parent_id", table_name="resources", schema=SCHEMA)
    op.drop_table("resources", schema=SCHEMA)
    op.drop_table("rbac_role", schema=SCHEMA)
    op.drop_table("permission_usage_window", schema=SCHEMA)
    op.drop_table("permission_usage", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_paystack_event_reference",
        table_name="paystack_event",
        schema=SCHEMA,
    )
    op.drop_table("paystack_event", schema=SCHEMA)
    op.drop_index(
        f"ix_{SCHEMA}_notification_status_next_attempt_at",
        table_name="notification",
        schema=SCHEMA,
    )
    op.drop_index(
        f"ix_{SCHEMA}_notification_provider_message_id",
        table_name="notification",
        schema=SCHEMA,
    )
    op.drop_table("notification", schema=SCHEMA)
    op.drop_table("nav_gate_overrides", schema=SCHEMA)
    op.drop_table("message_thread", schema=SCHEMA)
    op.drop_table("effective_role_permissions", schema=SCHEMA)
    op.drop_table("actions", schema=SCHEMA)
