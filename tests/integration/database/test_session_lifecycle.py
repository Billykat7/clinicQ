"""The session dependencies roll back on an exception and never leak a connection (Issue 3).

Against a migrated PostgreSQL database, through the real ``get_db`` / ``get_async_db`` and real
requests. The engine behind them has a pool of five connections and no overflow, with a short
checkout timeout: a dependency that leaked one connection per failed request would exhaust it
within five requests, and the fiftieth would time out rather than pass.
"""

import asyncio
from collections.abc import Iterator
from http import HTTPStatus
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

import src.database.session as session_module
from src.commons.enums import AppEnvironment
from src.commons.exceptions import ConflictError
from src.core.config import Settings
from src.database.models import Widget
from src.database.schema import (
    apply_async_postgres_session_settings,
    apply_postgres_search_path,
)
from src.database.session import get_async_db, get_db
from src.main import create_app

pytestmark = pytest.mark.postgres

_PREFIX = "issue3-"

#: The dependency the kernel's routers take (decision 5: sync by default).
DbSession = Annotated[Session, Depends(get_db)]


@pytest.fixture
def pooled_engine(migrated_database: URL) -> Iterator[Engine]:
    """Five connections, no overflow, a two-second wait: a leak shows up as a timeout."""
    engine = create_engine(
        migrated_database, pool_size=5, max_overflow=0, pool_timeout=2
    )
    apply_postgres_search_path(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def wired(pooled_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Engine:
    """Point the application's sync engine and session factory at the test database."""
    monkeypatch.setattr(session_module, "_engine", pooled_engine)
    monkeypatch.setattr(
        session_module,
        "_session_local",
        sessionmaker(bind=pooled_engine, autocommit=False, autoflush=False),
    )
    return pooled_engine


def _routes() -> APIRouter:
    """Write a widget, then keep it, fail with a crash, or fail with a domain error."""
    router = APIRouter(prefix="/_issue3")

    @router.post("/keep/{name}")
    def keep(name: str, db: DbSession) -> dict[str, str]:
        db.add(Widget(name=f"{_PREFIX}{name}"))
        db.commit()
        return {"kept": name}

    @router.post("/crash/{name}")
    def crash(name: str, db: DbSession) -> None:
        db.add(Widget(name=f"{_PREFIX}{name}"))
        db.flush()  # the row is in the transaction, on the connection, when the route fails
        raise RuntimeError("the route failed after writing")

    @router.post("/refuse/{name}")
    def refuse(name: str, db: DbSession) -> None:
        db.add(Widget(name=f"{_PREFIX}{name}"))
        db.flush()
        raise ConflictError("Refused after writing.", code="issue3.refused")

    return router


@pytest.fixture
def client(wired: Engine) -> TestClient:
    """The real app with the routes above, answering crashes with a 500 as a server would."""
    app: FastAPI = create_app(
        Settings(_env_file=None, environment=AppEnvironment.DEVELOPMENT)
    )
    app.include_router(_routes())
    return TestClient(app, raise_server_exceptions=False)


def _names(engine: Engine) -> list[str]:
    """The test's widgets actually in the database, read on a fresh connection."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(Widget.name)
            .where(Widget.name.like(f"{_PREFIX}%"))
            .order_by(Widget.name)
        )
        return list(rows.scalars())


def _open_transactions(engine: Engine) -> int:
    """Connections to this database sitting in an open transaction, as the server sees them."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()"
            )
        ).scalar_one()


def test_a_committed_write_is_kept(client: TestClient, wired: Engine) -> None:
    """The control: a route that commits leaves its row, and its connection back in the pool."""
    assert client.post("/_issue3/keep/kept").status_code == HTTPStatus.OK
    assert _names(wired) == [f"{_PREFIX}kept"]
    assert wired.pool.checkedout() == 0


@pytest.mark.parametrize(
    ("route", "status"),
    [
        ("crash", HTTPStatus.INTERNAL_SERVER_ERROR),
        ("refuse", HTTPStatus.CONFLICT),
    ],
)
def test_a_request_that_raises_after_writing_is_rolled_back(
    client: TestClient, wired: Engine, route: str, status: HTTPStatus
) -> None:
    """A crash or a domain error after a flushed write: nothing persists, nothing stays open."""
    assert client.post(f"/_issue3/{route}/lost").status_code == status

    assert _names(wired) == []
    assert wired.pool.checkedout() == 0
    assert _open_transactions(wired) == 0


def test_fifty_failing_requests_do_not_leak_a_connection(
    client: TestClient, wired: Engine
) -> None:
    """Ten times the pool size in failures, then a success: the pool is intact."""
    for i in range(50):
        route = "crash" if i % 2 else "refuse"
        assert client.post(f"/_issue3/{route}/{i}").status_code >= 400

    assert client.post("/_issue3/keep/after").status_code == HTTPStatus.OK
    assert _names(wired) == [f"{_PREFIX}after"]
    assert wired.pool.checkedout() == 0
    assert _open_transactions(wired) == 0


def test_get_db_rolls_back_when_the_exception_is_thrown_into_it(wired: Engine) -> None:
    """The dependency itself, driven the way FastAPI drives it: the error goes in, the row goes."""
    dependency = get_db()
    db = next(dependency)
    db.add(Widget(name=f"{_PREFIX}direct"))
    db.flush()

    with pytest.raises(RuntimeError):
        dependency.throw(RuntimeError("route failed"))

    assert _names(wired) == []
    assert wired.pool.checkedout() == 0


def test_get_async_db_rolls_back_and_returns_its_connection(
    migrated_database: URL, monkeypatch: pytest.MonkeyPatch, pooled_engine: Engine
) -> None:
    """The async dependency keeps the same contract, on asyncpg."""
    async_url = migrated_database.set(drivername="postgresql+asyncpg")

    async def scenario() -> tuple[int, int]:
        engine = create_async_engine(async_url, pool_size=2, max_overflow=0)
        apply_async_postgres_session_settings(engine)
        monkeypatch.setattr(session_module, "_async_engine", engine)
        monkeypatch.setattr(
            session_module,
            "_async_session_local",
            async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
        )
        try:
            dependency = get_async_db()
            db = await anext(dependency)
            db.add(Widget(name=f"{_PREFIX}async"))
            await db.flush()
            with pytest.raises(RuntimeError):
                await dependency.athrow(RuntimeError("stream failed"))
            async with engine.connect() as conn:
                count = (
                    await conn.execute(
                        select(func.count()).where(Widget.name.like(f"{_PREFIX}%"))
                    )
                ).scalar_one()
            return count, engine.pool.checkedout()  # type: ignore[attr-defined]
        finally:
            await engine.dispose()

    count, checked_out = asyncio.run(scenario())
    assert count == 0
    assert checked_out == 0
