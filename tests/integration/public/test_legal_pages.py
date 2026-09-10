"""Public legal pages — privacy notice and service terms (Issue #64 / M10).

POPIA requires the public site to carry a privacy notice and terms, linked from the
footer. Both belong to the DB-independent front door: like ``/`` and ``/features`` they
render from config alone, so a database outage never takes the legal pages down.

Per ``.cursor/rules/testing-strategy.mdc`` these tests assert status codes only — never
HTML body content or template markup. The page copy, the retention table and the footer
links are verified in review, not here.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette import status

from src.database.session import get_db
from src.main import create_app


def _raise_database_outage() -> Generator[Session]:
    """Stand in for `get_db` when the database is down: fail on every use."""
    raise RuntimeError("database is unavailable")
    yield  # pragma: no cover - unreachable; makes the callable a generator


@pytest.fixture
def client_without_database() -> Generator[TestClient]:
    """Client whose DB dependency raises, simulating a stopped database."""
    app = create_app()
    app.dependency_overrides[get_db] = _raise_database_outage
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.mark.parametrize("path", ["/privacy", "/terms"])
def test_legal_page_answers_when_database_is_down(
    client_without_database: TestClient, path: str
) -> None:
    """The privacy notice and terms render even when the database is stopped.

    Both are config-only routes with no DB dependency, so the footer's legal links keep
    working during an outage — the same front-door resilience as ``/features``.
    """
    response = client_without_database.get(path)

    assert response.status_code == status.HTTP_200_OK
