"""
Alembic environment for migrations.
Project uses PostgreSQL with PostGIS. DATABASE_URL from settings (.env).
Application tables — and Alembic's own ``alembic_version`` — live in the schema named
by ``DB_SCHEMA`` (``src.commons.enums.DbSchema``), never in ``public``.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, create_engine

# Importing the package is what registers every model on ``Base.metadata``; naming them one by
# one here would be a second list to keep in sync, and a model missing from it reflects to
# autogenerate as a table to *drop*. Add your models to ``src/database/models/__init__.py``
# instead — that is the only list.
import src.database.models  # noqa: F401
from src.core.config import get_settings
from src.database.models.base import Base
from src.database.schema import create_schema_sql, ensure_postgres_schema

# Alembic Config object
config = context.config

# The CLI gets alembic.ini's logging. A caller that runs Alembic inside its own process (the
# migration tests) sets ``configure_logger`` to False and keeps its own; and existing loggers are
# never disabled, so the ``src`` modules imported above keep logging either way.
if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _configure_sqlalchemy_url_from_env() -> None:
    """Use DATABASE_URL from settings (.env) so migrations use the same DB as the app."""
    config.set_main_option("sqlalchemy.url", get_settings().database_url)


def _include_name(name: str | None, type_: str, _parent_names: dict) -> bool:
    """Restrict autogenerate reflection to the application schema.

    ``include_schemas`` makes Alembic reflect named schemas rather than only the
    connection's default one — without it every schema-qualified table reflects as
    unqualified and autogenerate proposes dropping and recreating the lot. The
    filter is the other half: it keeps ``public`` (PostGIS' ``spatial_ref_sys`` and
    any other product sharing this database) out of the comparison, so a diff can
    only ever describe tables this project owns.
    """
    if type_ == "schema":
        return name == get_settings().db_schema.value
    return True


def _alembic_context_kwargs() -> dict:
    """Shared Alembic context options for online and offline modes."""
    settings = get_settings()
    return {
        "target_metadata": target_metadata,
        "version_table_schema": settings.db_schema.value,
        "include_schemas": True,
        "include_name": _include_name,
    }


def run_migrations_offline() -> None:
    """Run migrations in offline mode (generate SQL only)."""
    _configure_sqlalchemy_url_from_env()
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_alembic_context_kwargs(),
    )
    with context.begin_transaction():
        # ``alembic_version`` lives in the application schema and Alembic emits its
        # CREATE before the first migration, so the schema has to come first for the
        # generated script to apply to an empty database. Online runs get this from
        # ``ensure_postgres_schema`` below; the statement is idempotent either way.
        context.execute(create_schema_sql(get_settings().db_schema))
        context.run_migrations()


def _run_on(connection: Connection) -> None:
    """Configure the context on ``connection`` and run the migrations."""
    if connection.dialect.name != "postgresql":
        raise RuntimeError(
            "BK ClinicQ requires PostgreSQL with PostGIS. Set DATABASE_URL to a PostgreSQL connection."
        )
    ensure_postgres_schema(connection, get_settings().db_schema)
    context.configure(connection=connection, **_alembic_context_kwargs())
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode. Requires PostgreSQL with PostGIS.

    A caller that already holds a connection (the migration tests, Issue 3) passes it in
    ``config.attributes["connection"]``, the pattern from Alembic's cookbook, and the migrations run
    on it, against whatever database it points at. Otherwise they run against ``DATABASE_URL``.
    """
    given = config.attributes.get("connection")
    if given is not None:
        _run_on(given)
        return
    _configure_sqlalchemy_url_from_env()
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.begin() as connection:
        _run_on(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
