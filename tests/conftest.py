"""Pytest fixtures."""

import os
from collections.abc import Generator
from pathlib import Path
from typing import NoReturn

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.commons.ids import new_id
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


# ── PostgreSQL + PostGIS (Issue 3) ───────────────────────────────────────────────────────────
#
# SQLite is enough for most tests, but not for the database layer itself: migrations, PostGIS,
# the schema, constraint names and connection handling are PostgreSQL behaviour. Tests marked
# ``postgres`` get a **throwaway database** on the server named by ``TEST_DATABASE_URL``: created
# empty for the test, dropped afterwards, called ``clinicq_test_<uuid7>`` so nothing else on the
# server is ever touched. With the compose stack up (``make db-up``) that is::
#
#     TEST_DATABASE_URL=postgresql://btk_user:change-me@localhost:5432/btk pytest -m postgres
#
# or ``make test-postgres``. The URL is never guessed: whatever answers on localhost:5432 on a
# developer's machine may be another product's database. Without it the tests are skipped with
# that instruction, unless ``REQUIRE_POSTGRES_TESTS=1`` (CI sets it) turns the skip into a failure,
# so a missing service container cannot pass as green.

MIGRATIONS_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
_TEST_DB_PREFIX = "clinicq_test_"


def _postgres_unavailable(reason: str) -> None:
    """Skip, or fail when the run requires PostgreSQL."""
    if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1":
        pytest.fail(f"PostgreSQL tests are required here: {reason}")
    pytest.skip(
        f"{reason}. Set TEST_DATABASE_URL to a PostgreSQL 18 + PostGIS server (make db-up)."
    )


@pytest.fixture(scope="session")
def postgres_server_url() -> URL:
    """The server the throwaway databases are created on; skips (or fails) when there is none."""
    raw = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not raw:
        _postgres_unavailable("TEST_DATABASE_URL is not set")
    url = make_url(raw)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        _postgres_unavailable(
            f"cannot reach {url.render_as_string(hide_password=True)}: {exc.orig}"
        )
    finally:
        engine.dispose()
    return url


@pytest.fixture
def empty_database(postgres_server_url: URL) -> Generator[URL]:
    """A brand-new, empty database for one test, dropped (connections and all) afterwards."""
    name = f"{_TEST_DB_PREFIX}{new_id().replace('-', '')}"
    admin = create_engine(postgres_server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield postgres_server_url.set(database=name)
    finally:
        assert name.startswith(
            _TEST_DB_PREFIX
        )  # never drop anything this fixture did not create
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def run_alembic(url: URL, *commands: tuple[str, ...]) -> Config:
    """Run Alembic ``commands`` (``("upgrade", "head")``, ``("check",)``...) against ``url``.

    The migrations run on a connection handed to ``alembic/env.py`` (its cookbook hook), so they
    reach the test database whatever ``DATABASE_URL`` the local ``.env`` names.
    """
    config = Config(str(MIGRATIONS_INI))
    config.attributes["configure_logger"] = (
        False  # keep the test process's logging as it is
    )
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            for name, *arguments in commands:
                getattr(command, name)(config, *arguments)
    finally:
        engine.dispose()
    return config


@pytest.fixture
def migrated_database(empty_database: URL) -> URL:
    """An empty database brought to ``head`` by the real migrations."""
    run_alembic(empty_database, ("upgrade", "head"))
    return empty_database


@pytest.fixture
def migrated_engine(migrated_database: URL) -> Generator[Engine]:
    """An engine on the migrated database, with the app's ``search_path``, disposed afterwards."""
    from src.database.schema import apply_postgres_search_path

    engine = create_engine(migrated_database, pool_size=5, max_overflow=0)
    apply_postgres_search_path(engine)
    yield engine
    engine.dispose()


# ── Redis (Issue 9) ──────────────────────────────────────────────────────────────────────────
#
# The same contract as PostgreSQL above. Tests marked ``redis`` talk to the server named by
# ``TEST_REDIS_URL`` and are skipped without it, unless ``REQUIRE_REDIS_TESTS=1`` (CI sets it)
# turns the skip into a failure. The URL is never guessed either. The tests write only keys they
# made unique and delete them afterwards, so a spare database number (CI and ``make
# test-services`` use ``/15``) is enough; nothing is flushed.


def _redis_unavailable(reason: str) -> NoReturn:
    """Skip, or fail when the run requires Redis."""
    if os.environ.get("REQUIRE_REDIS_TESTS") == "1":
        pytest.fail(f"Redis tests are required here: {reason}")
    pytest.skip(f"{reason}. Set TEST_REDIS_URL to a Redis 8 server (make db-up).")


@pytest.fixture(scope="session")
def redis_server_url() -> str:
    """The Redis the ``redis``-marked tests use; skips (or fails) when there is none."""
    import redis

    url = os.environ.get("TEST_REDIS_URL", "").strip()
    if not url:
        _redis_unavailable("TEST_REDIS_URL is not set")
    client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    except redis.RedisError as exc:
        _redis_unavailable(f"cannot reach Redis at {url}: {exc}")
    finally:
        client.close()
    return url


# ── Markers (Issue 7) ────────────────────────────────────────────────────────────────────────
#
# Applied by location, so nobody has to remember to: everything under tests/unit/ is ``unit``,
# everything else ``integration``. ``slow`` marks what does not belong in the inner loop: the
# PostgreSQL tests (a server, seconds each) and the timing tests, which mark themselves.
#
#   pytest -m unit          the essential logic, in seconds
#   pytest -m "not slow"    make test-fast: the loop while coding
#   pytest                  everything, as make check and CI run it

_UNIT_ROOT = Path(__file__).resolve().parent / "unit"

#: Kernel guard tests that read files a later issue creates, and so fail until that issue lands.
#: They run as ``xfail(strict=True)``: the day the file appears the test passes, strict reports
#: that as a failure, and the entry below has to be deleted by the issue that made it pass. Nothing
#: here is hidden: each one says what it waits for, and ``make check`` stays a real gate meanwhile.
_SCAN_WORKFLOW = (
    "needs .github/workflows/vulnerability-scan.yml, which Issue 97 creates (pip-audit and Trivy "
    "are local-only until then: scripts/README.md)"
)
_SECURITY_DOCS = (
    "needs docs/SECURITY/{THREAT-MODEL,SECURITY-CHECKLIST,AUTHZ-BOUNDARIES}.md, which no issue "
    "creates yet (raised in PR #110)"
)
PENDING_ON_LATER_ISSUES: dict[str, str] = {
    "tests/unit/platform/test_workflow_guardrails.py::"
    "test_the_scheduled_scan_runs_weekly_not_more_often": _SCAN_WORKFLOW,
    **{
        f"tests/unit/platform/test_scanning_config.py::{name}": _SCAN_WORKFLOW
        for name in (
            "test_every_ignored_cve_carries_a_written_reason",
            "test_the_cve_ignore_list_is_identical_locally_and_in_ci",
        )
    },
    **{
        f"tests/integration/security/test_m30_exit_criteria.py::{name}": _SCAN_WORKFLOW
        for name in (
            "test_all_three_scanners_are_present_and_blocking",
            "test_scanning_runs_on_a_schedule_and_on_the_files_that_can_break_it",
        )
    },
    "tests/integration/security/test_m30_exit_criteria.py::"
    "test_no_residual_risk_is_left_unowned": _SECURITY_DOCS,
    **{
        f"tests/integration/security/test_authz_boundaries.py::"
        f"test_the_security_docs_reference_each_other[path{i}]": _SECURITY_DOCS
        for i in range(3)
    },
}


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Mark each test ``unit`` or ``integration`` by location, and ``slow`` when it needs a server.

    Also marks the guard tests in :data:`PENDING_ON_LATER_ISSUES` as strict expected failures.
    """
    for item in items:
        in_unit = _UNIT_ROOT in item.path.parents
        item.add_marker(pytest.mark.unit if in_unit else pytest.mark.integration)
        if item.get_closest_marker("postgres"):
            item.add_marker(pytest.mark.slow)
        reason = PENDING_ON_LATER_ISSUES.get(item.nodeid)
        if reason:
            item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
