"""PostgreSQL schema helpers for BK ClinicQ ORM tables."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from src.commons.enums import DbSchema

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

DEFAULT_DB_SCHEMA = DbSchema.CLINICQ


def postgres_search_path_sql(schema: DbSchema = DEFAULT_DB_SCHEMA) -> str:
    """Return SQL that sets search_path (app schema first, public for PostGIS)."""
    return f"SET search_path TO {schema.value}, public"


def apply_postgres_search_path(
    engine: Engine, schema: DbSchema = DEFAULT_DB_SCHEMA
) -> None:
    """Register a connect listener so PostgreSQL sessions use the app schema."""

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, _connection_record) -> None:
        if engine.dialect.name != "postgresql":
            return
        cursor = dbapi_connection.cursor()
        cursor.execute(postgres_search_path_sql(schema))
        cursor.close()


def apply_async_postgres_session_settings(
    async_engine: AsyncEngine,
    schema: DbSchema = DEFAULT_DB_SCHEMA,
    *,
    statement_timeout_ms: int = 0,
    lock_timeout_ms: int = 0,
) -> None:
    """Set search_path and (optionally) statement/lock timeouts on async PG connections (Issue #81).

    Registers a ``connect`` listener on the async engine's underlying sync engine — the documented
    hook for async engines — so every pooled connection starts with the app schema on its
    ``search_path`` and, on PostgreSQL, a bounded ``statement_timeout``/``lock_timeout``. The
    timeouts make a runaway query or a stuck lock fail fast instead of pinning an event-loop worker
    for the whole round-trip. A non-positive timeout is left unset (server default). Non-PostgreSQL
    backends (the SQLite used by tests) are skipped.
    """

    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_session_settings(dbapi_connection, _connection_record) -> None:
        if async_engine.dialect.name != "postgresql":
            return
        # The asyncpg DBAPI adapter runs these synchronously against the event loop's connection.
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(postgres_search_path_sql(schema))
            if statement_timeout_ms > 0:
                cursor.execute(f"SET statement_timeout TO {statement_timeout_ms}")
            if lock_timeout_ms > 0:
                cursor.execute(f"SET lock_timeout TO {lock_timeout_ms}")
        finally:
            cursor.close()


def create_schema_sql(schema: DbSchema = DEFAULT_DB_SCHEMA) -> str:
    """Return idempotent DDL creating the application schema."""
    return f'CREATE SCHEMA IF NOT EXISTS "{schema.value}"'


def ensure_postgres_schema(connection, schema: DbSchema = DEFAULT_DB_SCHEMA) -> None:
    """Create the application schema if missing (idempotent)."""
    connection.execute(text(create_schema_sql(schema)))
    connection.execute(text(postgres_search_path_sql(schema)))


def sqlite_schema_translate_map(
    schema: DbSchema = DEFAULT_DB_SCHEMA,
) -> dict[str | None, str | None]:
    """Map the PostgreSQL schema to None so SQLite tests can use ORM metadata."""
    return {schema.value: None}
