"""Shared top bar apps menu + footer — Issue 17 / M3.

The apps menu is progressive enhancement: `site.js` opens the panel, but the
acceptance criteria require the site to work with JavaScript disabled. That
fallback rests on one server-side promise — the menu's tiles and the footer point
at real, public destinations that render without a database, exactly like the home
page. The launcher is a three-column grid whose rows grow with the module list;
every module tile targets `/features#…` (fragments are resolved client-side, so
each is a plain `GET /features`), and the footer repeats the `/features` link so
the same navigation exists without any script.

The panel's open/close, `aria-expanded`, Escape, outside-click and focus-trap
behaviours are client-side and verified in the browser, not here. Per
`docs/IDE/RULES/testing-strategy.mdc` these tests assert status codes only, never
HTML body content or template markup.
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


def test_apps_menu_destination_answers_when_database_is_down(
    client_without_database: TestClient,
) -> None:
    """The apps menu / footer fallback (`GET /features`) survives a DB outage.

    Every apps tile anchor (`/features#…`) and the footer's Features link resolve
    to this one endpoint. It has no DB dependency, so the JavaScript-free path out
    of the top bar keeps working even when the database is stopped.
    """
    response = client_without_database.get("/features")

    assert response.status_code == status.HTTP_200_OK
