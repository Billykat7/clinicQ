"""The sign-in pages: `/signin`, `/signup`, `/forgot-password`, `/reset-password` (Issue 231).

Signing in used to happen in a modal laid over whatever page you were on, so there was nothing to
request and nothing to test here. Each step is a URL now, which means each one has answers worth
checking: whether it exists at all under this deployment's settings, that it survives a database
outage like the rest of the front door, and that `next` cannot send anyone off this site.

Per `docs/IDE/RULES/testing-strategy.mdc` these assert status codes, redirects and the route's own
logic — never HTML body content or template markup.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette import status

from src.core.config import Settings, get_settings
from src.database.session import get_db
from src.main import create_app
from src.web import routes as web_routes
from src.web.routes import _safe_next

_AUTH_PAGES = ("/signin", "/signup", "/forgot-password", "/reset-password")


def _raise_database_outage() -> Generator[Session]:
    """Stand in for `get_db` when the database is down: fail on every use."""
    raise RuntimeError("database is unavailable")
    yield  # pragma: no cover - unreachable; makes the callable a generator


def _client(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> TestClient:
    """A client whose routes read `Settings` built without `.env`, plus `overrides`.

    The routes call the module-level `get_settings()` rather than taking it as a dependency — the
    flags decide whether a page exists at all, which is answered before any dependency runs — so
    the patch goes on the module, not on `app.dependency_overrides`.
    """
    base: dict[str, object] = {
        "_env_file": None,
        "signup_enabled": True,
        "auth_otp_login_enabled": True,
        "auth_password_login_enabled": True,
    }
    base.update(overrides)
    settings = Settings(**base)  # type: ignore[arg-type]
    monkeypatch.setattr(web_routes, "get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    """Everything enabled: all four pages exist."""
    with _client(monkeypatch) as test_client:
        yield test_client


# --- the pages exist, and say what they are for --------------------------------


@pytest.mark.parametrize("path", _AUTH_PAGES)
def test_each_step_of_signing_in_has_an_address(client: TestClient, path: str) -> None:
    """The point of Issue 231: a person can be sent to any of these, and reload them."""
    assert client.get(path).status_code == status.HTTP_200_OK


def test_the_reset_page_answers_without_a_token(client: TestClient) -> None:
    """A link that lost its token renders and explains, rather than 404ing or erroring.

    The token is not checked here at all — `/api/v1/auth/password/reset` decides that, once.
    """
    assert client.get("/reset-password").status_code == status.HTTP_200_OK


# --- a page that cannot work does not exist ------------------------------------


def test_sign_up_is_not_there_when_this_deployment_does_not_allow_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404, not a form whose only outcome is the API's 403."""
    with _client(monkeypatch, signup_enabled=False) as client:
        assert client.get("/signup").status_code == status.HTTP_404_NOT_FOUND
        # Signing in still works: the two flags are independent.
        assert client.get("/signin").status_code == status.HTTP_200_OK


@pytest.mark.parametrize("path", ["/forgot-password", "/reset-password"])
def test_the_password_pages_are_not_there_without_password_sign_in(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """`/auth/password/*` answers 403 when passwords are off; the pages match it."""
    with _client(monkeypatch, auth_password_login_enabled=False) as client:
        assert client.get(path).status_code == status.HTTP_404_NOT_FOUND


# --- ``next``: a way back, never a way off the site ----------------------------


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "//evil.example/pwn",  # a browser reads this as another origin
        "https://evil.example/pwn",
        "javascript:alert(1)",
        "evil.example",
    ],
)
def test_next_that_is_not_a_path_on_this_site_becomes_the_dashboard(
    raw: str | None,
) -> None:
    """An open redirect out of a sign-in is a phishing primitive; this is the one guard."""
    assert _safe_next(raw) == "/dashboard"


@pytest.mark.parametrize(
    "raw",
    ["/dashboard", "/dashboard/sites/abc/board?queue=triage", "/admin/clinics/all"],
)
def test_a_path_on_this_site_is_kept(raw: str) -> None:
    """The interrupted request is what the person came for; it has to survive the sign-in."""
    assert _safe_next(raw) == raw


def test_a_signed_out_visitor_is_sent_to_the_sign_in_page_with_where_they_were_going(
    client: TestClient,
) -> None:
    """The redirect names the page, so signing in lands back on it."""
    refused = client.get("/dashboard", follow_redirects=False)

    assert refused.status_code == status.HTTP_302_FOUND
    assert refused.headers["location"] == "/signin?next=%2Fdashboard"


# --- the front door's resilience -----------------------------------------------


@pytest.mark.parametrize("path", _AUTH_PAGES)
def test_signing_in_still_renders_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """A sign-in page that is down when the database is down tells nobody anything.

    Like `/`, `/features` and the legal pages, these build their context with
    `public_page_context`, which opens its own short-lived session and degrades to the signed-out
    shell on any error.
    """
    with _client(monkeypatch) as client:
        client.app.dependency_overrides[get_db] = _raise_database_outage  # type: ignore[attr-defined]
        assert client.get(path).status_code == status.HTTP_200_OK
