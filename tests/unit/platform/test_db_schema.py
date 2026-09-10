"""The project owns a PostgreSQL schema of its own; these guard that it stays true."""

from __future__ import annotations

from src.core.config import get_settings
from src.database.models import Base


def test_settings_accept_the_repo_env_file() -> None:
    """DB_SCHEMA in .env must be a value the enum admits, or the app cannot boot."""
    assert get_settings().db_schema.value


def test_every_table_lives_in_the_application_schema() -> None:
    """No model may fall back to `public`, which belongs to PostGIS."""
    schema = get_settings().db_schema.value
    misplaced = [t.fullname for t in Base.metadata.sorted_tables if t.schema != schema]
    assert not misplaced
