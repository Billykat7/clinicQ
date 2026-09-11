"""The migration baseline, proven against PostgreSQL 18 + PostGIS (Issue 3).

Every test starts from a database that did not exist a moment earlier (``empty_database`` in
``tests/conftest.py``) and runs the real revisions through ``alembic/env.py``, so what is proven
is what ``alembic upgrade head`` does on a fresh server: the whole chain, not ``create_all``.
Nothing else in the suite runs the migrations (see ``test_migration_raw_inserts.py``); these do.
"""

import pytest
from alembic.autogenerate import (
    compare_metadata,
    produce_migrations,
    render_python_code,
)
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    MetaData,
    String,
    Table,
    create_engine,
    text,
)
from sqlalchemy.engine import URL

from src.commons.enums import DbSchema
from src.database.models import Base
from src.database.models.base import convention
from tests.conftest import MIGRATIONS_INI, run_alembic

pytestmark = pytest.mark.postgres

SCHEMA = DbSchema.CLINICQ.value


def _head() -> str:
    """The head revision the script directory declares."""
    from alembic.config import Config

    return (
        ScriptDirectory.from_config(Config(str(MIGRATIONS_INI))).get_current_head()
        or ""
    )


def _scalar(url: URL, sql: str) -> object:
    """Run one query on ``url`` and return its single value."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def _app_tables(url: URL) -> set[str]:
    """The tables in the application schema, Alembic's own version table excepted."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_name <> 'alembic_version'"
                ),
                {"schema": SCHEMA},
            )
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def _context_opts() -> dict[str, object]:
    """The autogenerate options env.py uses: the app schema only, version table inside it."""
    return {
        "version_table_schema": SCHEMA,
        "include_schemas": True,
        "include_name": lambda name, type_, _parents: (
            type_ != "schema" or name == SCHEMA
        ),
    }


def test_upgrade_head_on_an_empty_database_creates_the_schema_and_installs_postgis(
    empty_database: URL,
) -> None:
    """From nothing: PostGIS absent before, installed after; every model's table exists."""
    assert (
        _scalar(
            empty_database,
            "SELECT count(*) FROM pg_extension WHERE extname = 'postgis'",
        )
        == 0
    )

    run_alembic(empty_database, ("upgrade", "head"))

    assert (
        _scalar(
            empty_database, "SELECT extname FROM pg_extension WHERE extname = 'postgis'"
        )
        == "postgis"
    )
    # In ``public``, as the baseline says, so the app schema holds only this project's tables.
    assert (
        _scalar(
            empty_database,
            "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
            "WHERE e.extname = 'postgis'",
        )
        == "public"
    )
    assert _app_tables(empty_database) == {t.name for t in Base.metadata.sorted_tables}
    assert (
        _scalar(empty_database, f"SELECT version_num FROM {SCHEMA}.alembic_version")
        == _head()
    )


def test_postgis_answers_a_real_distance_query_after_upgrade(
    migrated_database: URL,
) -> None:
    """The extension works, not just exists: Johannesburg to Durban is about 500 km."""
    metres = _scalar(
        migrated_database,
        "SELECT ST_Distance("
        "'SRID=4326;POINT(28.0473 -26.2041)'::geography, "
        "'SRID=4326;POINT(31.0218 -29.8587)'::geography)",
    )
    assert 480_000 < float(metres) < 520_000  # type: ignore[arg-type]


def test_downgrade_to_base_then_upgrade_to_head_round_trips(
    empty_database: URL,
) -> None:
    """Up, all the way down, and up again, each step its own run: no error, same schema."""
    run_alembic(empty_database, ("upgrade", "head"))
    tables = _app_tables(empty_database)

    run_alembic(empty_database, ("downgrade", "base"))
    assert _app_tables(empty_database) == set()
    assert (
        _scalar(empty_database, f"SELECT count(*) FROM {SCHEMA}.alembic_version") == 0
    )
    # PostGIS is shared with anything else in the database: a downgrade leaves it installed.
    assert (
        _scalar(
            empty_database,
            "SELECT count(*) FROM pg_extension WHERE extname = 'postgis'",
        )
        == 1
    )

    run_alembic(empty_database, ("upgrade", "head"))
    assert _app_tables(empty_database) == tables
    assert (
        _scalar(empty_database, f"SELECT version_num FROM {SCHEMA}.alembic_version")
        == _head()
    )


def test_autogenerate_finds_nothing_to_change_straight_after_upgrade(
    migrated_database: URL,
) -> None:
    """``alembic check`` passes and the model/database comparison is empty."""
    run_alembic(
        migrated_database, ("check",)
    )  # raises AutogenerateDiffsDetected on a diff

    engine = create_engine(migrated_database)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts=_context_opts())
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


#: The prefix each kind of constraint must carry (``src/database/models/base.py``).
_PREFIX = {"p": "pk_", "u": "uq_", "f": "fk_", "c": "ck_"}


def test_every_constraint_and_index_in_the_schema_follows_the_naming_convention(
    migrated_database: URL,
) -> None:
    """Primary keys ``pk_<table>``; unique, foreign-key and check constraints and indexes prefixed.

    Alembic's own ``alembic_version`` table is not ours to name and is left out.
    """
    engine = create_engine(migrated_database)
    try:
        with engine.connect() as conn:
            constraints = conn.execute(
                text(
                    "SELECT c.conname, c.contype, t.relname FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "WHERE n.nspname = :schema AND c.contype IN ('p', 'u', 'f', 'c') "
                    "AND t.relname <> 'alembic_version'"
                ),
                {"schema": SCHEMA},
            ).all()
            indexes = (
                conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = :schema AND tablename <> 'alembic_version'"
                    ),
                    {"schema": SCHEMA},
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()

    assert constraints and indexes
    misnamed = [
        f"{kind} {name} on {table}"
        for name, kind, table in constraints
        if not name.startswith(_PREFIX[kind]) or (kind == "p" and name != f"pk_{table}")
    ]
    misnamed += [
        f"index {name}"
        for name in indexes
        if not name.startswith(("pk_", "uq_", "ix_"))
    ]
    assert misnamed == []


def test_a_generated_migration_names_its_constraints_by_the_convention(
    migrated_database: URL,
) -> None:
    """Autogenerate a revision for a new table: every constraint it emits is named, and by rule.

    Unnamed constraints are what make autogenerate unstable (PostgreSQL invents a name, the next
    diff sees a rename). The metadata's naming convention is what prevents it; this renders the
    migration a developer would get and reads the names out of it.
    """
    metadata = MetaData(naming_convention=convention, schema=SCHEMA)
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)
    Table(
        "issue3_probe",
        metadata,
        Column("id", String(36), primary_key=True),
        Column(
            "widget_id", String(36), ForeignKey(f"{SCHEMA}.widget.id"), nullable=False
        ),
        Column("code", String(12), unique=True, nullable=False),
        CheckConstraint("length(code) > 0", name="code_not_empty"),
    )

    engine = create_engine(migrated_database)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts=_context_opts())
            rendered = render_python_code(
                produce_migrations(context, metadata).upgrade_ops
            )
    finally:
        engine.dispose()

    for name in (
        "pk_issue3_probe",
        "fk_issue3_probe_widget_id_widget",
        "uq_issue3_probe_code",
        "ck_issue3_probe_code_not_empty",
    ):
        assert f"op.f('{name}')" in rendered, rendered
