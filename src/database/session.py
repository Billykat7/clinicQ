"""Database engine and session factory.

Uses ``DATABASE_URL`` from settings. Models are imported so
``Base.metadata`` is populated for ``create_all`` and Alembic.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import get_settings
from src.database.models import Base, RefreshToken, User  # noqa: F401
from src.database.schema import (
    apply_async_postgres_session_settings,
    apply_postgres_search_path,
)

logger = logging.getLogger(__name__)

# Engine/session factory are created lazily so importing this module (e.g. for the
# FastAPI ``get_db`` dependency) does not require a live database driver or connection.
_engine: Engine | None = None
_session_local: sessionmaker[Session] | None = None

# The async engine/factory back the request path (Issue #81); the sync pair above stays for
# Alembic, cron, the CLI and scripts. Both are created lazily and are independent process-wide
# singletons over the same database.
_async_engine: AsyncEngine | None = None
_async_session_local: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            future=True,
        )
        apply_postgres_search_path(_engine, get_settings().db_schema)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory, creating it on first use."""
    global _session_local
    if _session_local is None:
        _session_local = sessionmaker(
            autocommit=False, autoflush=False, bind=get_engine()
        )
    return _session_local


def get_db() -> Generator[Session]:
    """FastAPI dependency that yields a DB session and closes it after the request."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session]:
    """Context manager for DB access outside the request lifecycle."""
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# Async request-path engine/session (Issue #81)
# --------------------------------------------------------------------------------------


def get_async_engine() -> AsyncEngine:
    """Return the process-wide async SQLAlchemy engine, creating it on first use.

    Backs the request path so an ``async def`` handler's queries run off the event loop instead of
    blocking it. Built from ``database_url_async`` (the sync DSN with an async driver) with the
    async pool sized and its wait bounded from settings; on PostgreSQL each connection also gets the
    app ``search_path`` and a bounded ``statement_timeout``/``lock_timeout``. The synchronous engine
    is untouched and remains the one Alembic, cron, the CLI and scripts use.
    """
    global _async_engine
    if _async_engine is None:
        settings = get_settings()
        _async_engine = create_async_engine(
            settings.database_url_async,
            pool_pre_ping=True,
            pool_size=settings.db_async_pool_size,
            max_overflow=settings.db_async_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
        )
        apply_async_postgres_session_settings(
            _async_engine,
            settings.db_schema,
            statement_timeout_ms=settings.db_statement_timeout_seconds * 1000,
            lock_timeout_ms=settings.db_lock_timeout_seconds * 1000,
        )
    return _async_engine


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory, creating it on first use."""
    global _async_session_local
    if _async_session_local is None:
        _async_session_local = async_sessionmaker(
            bind=get_async_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _async_session_local


async def get_async_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency that yields an async DB session and closes it after the request."""
    async with get_async_session_factory()() as db:
        yield db


@asynccontextmanager
async def get_async_db_context() -> AsyncGenerator[AsyncSession]:
    """Async context manager for DB access outside the request lifecycle."""
    async with get_async_session_factory()() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
