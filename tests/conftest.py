"""Pytest fixtures."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.core import security
from src.core.rate_limit import reset_all_limiters
from src.database.models import Base
from src.database.schema import sqlite_schema_translate_map
from src.main import create_app

# The auth/RBAC suites are bcrypt-bound (dozens of password logins each). Hash at a low cost in
# the test process only by overriding the work-factor lookup that ``hash_password`` uses — this
# touches neither ``Settings`` nor the environment, so ``Settings.bcrypt_rounds`` (default 12) and
# its production guard are unchanged and production hashing stays at cost 12.
security._bcrypt_rounds = lambda: 4  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _reset_rate_limiters() -> Generator[None]:
    """Reset every process-global rate limiter around each test.

    The limiters are module-global sliding windows keyed by client IP/email; under ``TestClient``
    every request shares the ``testserver`` IP, so without a reset a per-IP budget would accumulate
    across a file's tests and spuriously ``429`` later requests.

    Issue #179 widened this from the password-login limiter alone to all of them: OTP moved off its
    own hand-rolled dictionaries onto the same shared mechanism, so one fixture now covers what two
    reset paths used to.
    """
    reset_all_limiters()
    yield
    reset_all_limiters()


@pytest.fixture
def client() -> TestClient:
    """HTTP client against the FastAPI app."""
    return TestClient(create_app())


@pytest.fixture
def session_factory() -> Generator[sessionmaker[Session]]:
    """In-memory SQLite engine with ORM tables (schema mapped away for SQLite)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


# Your fixtures go here. Two conventions the ones above follow and yours should:
#
# * `client` builds the app fresh per test, so a route added by a module under test is present
#   and no state leaks between tests;
# * `session_factory` gives an in-memory SQLite database with the schema mapped away
#   (`sqlite_schema_translate_map`), so a test never needs PostgreSQL to exercise a query. Reach
#   for the real database only when you are testing something PostgreSQL does and SQLite does not.
