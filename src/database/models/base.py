"""SQLAlchemy declarative base and metadata. Mixins live in ``models.mixins``."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from src.commons.enums import DbSchema

# Naming convention for constraints (helps Alembic autogenerate stable names).
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Every table is schema-qualified here, so `Base.metadata` and the migrations in
# `alembic/versions` describe the same objects and autogenerate stays quiet. SQLite
# tests map the schema away via `src.database.schema.sqlite_schema_translate_map`.
metadata = MetaData(
    naming_convention=convention,
    schema=DbSchema.CLINICQ.value,
)


class Base(DeclarativeBase):
    """Base class for ORM models."""

    metadata = metadata
