"""The UI shell's routes and assets, over real requests (Issue 5).

Behaviour only, per ``.cursor/rules/testing-strategy.mdc``: status codes, redirects, headers and
content types. Nothing here reads a page's markup; what the pages look like is in the PR's
screenshots of all three layouts in both themes.
"""

import json
from collections.abc import Iterator
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from src.commons.enums import AppEnvironment
from src.core.config import Settings
from src.main import create_app
from src.web.components import TOAST_EVENT
from src.web.dev import DEFAULT_LAYOUT, HTMX_REQUEST_HEADER, DevLayout

_DEV_PAGES = ["/dev/components", *(f"/dev/layouts/{layout}" for layout in DevLayout)]


def _client(environment: AppEnvironment) -> TestClient:
    """A client for an app built for ``environment``, ignoring the local ``.env``."""
    return TestClient(
        create_app(
            Settings(
                _env_file=None,
                environment=environment,
                jwt_secret="issue-5-test-secret-that-is-long-enough",
            )
        )
    )


@pytest.fixture
def dev() -> Iterator[TestClient]:
    """The development app, where the catalogue exists."""
    with _client(AppEnvironment.DEVELOPMENT) as client:
        yield client


@pytest.mark.parametrize("path", _DEV_PAGES)
def test_the_catalogue_and_layout_samples_render_in_development(
    dev: TestClient, path: str
) -> None:
    """Each development page answers 200 with HTML."""
    response = dev.get(path)
    assert response.status_code == HTTPStatus.OK
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize(
    "environment", [AppEnvironment.STAGING, AppEnvironment.PRODUCTION]
)
@pytest.mark.parametrize("path", [*_DEV_PAGES, "/dev/fragments/queue"])
def test_development_pages_do_not_exist_outside_development(
    environment: AppEnvironment, path: str
) -> None:
    """Outside development the routes are not registered at all: 404, not 403."""
    with _client(environment) as client:
        response = client.get(path, headers={HTMX_REQUEST_HEADER: "true"})
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("/dev", "/dev/components"),
        ("/dev/layouts", f"/dev/layouts/{DEFAULT_LAYOUT}"),
        ("/dev/layouts/kiosk", f"/dev/layouts/{DEFAULT_LAYOUT}"),
    ],
)
def test_bare_and_unknown_tabs_redirect_to_the_default(
    dev: TestClient, path: str, target: str
) -> None:
    """Every tab is a URL; the bare one and an unknown one land on the default, never a 404."""
    response = dev.get(path, follow_redirects=False)
    assert response.status_code == HTTPStatus.FOUND
    assert response.headers["location"] == target


def test_the_htmx_fragment_swaps_in_and_raises_a_toast(dev: TestClient) -> None:
    """An htmx request gets the rows, and HX-Trigger carries the toast ui-feedback.js shows."""
    response = dev.get("/dev/fragments/queue", headers={HTMX_REQUEST_HEADER: "true"})

    assert response.status_code == HTTPStatus.OK
    trigger = json.loads(response.headers["HX-Trigger"])
    assert trigger[TOAST_EVENT]["kind"] == "ok"
    assert trigger[TOAST_EVENT]["message"].startswith("Queue refreshed at ")


def test_the_lazy_stats_fragment_answers_htmx(dev: TestClient) -> None:
    """The fragment behind the skeleton answers an htmx load."""
    response = dev.get("/dev/fragments/stats", headers={HTMX_REQUEST_HEADER: "true"})
    assert response.status_code == HTTPStatus.OK


@pytest.mark.parametrize("fragment", ["/dev/fragments/queue", "/dev/fragments/stats"])
def test_a_fragment_opened_directly_redirects_to_its_page(
    dev: TestClient, fragment: str
) -> None:
    """A bookmark or a refresh on a fragment URL lands on the full page, not a bare fragment."""
    response = dev.get(fragment, follow_redirects=False)
    assert response.status_code == HTTPStatus.SEE_OTHER
    assert response.headers["location"] == f"/dev/layouts/{DevLayout.DASHBOARD}"


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/static/vendor/htmx-2.0.0.min.js", "text/javascript"),
        ("/static/css/site.css", "text/css"),
        ("/static/css/components.css", "text/css"),
        ("/static/css/layouts.css", "text/css"),
        ("/static/fonts/dm-sans-latin.woff2", "font/woff2"),
        ("/static/fonts/roboto-latin.woff2", "font/woff2"),
    ],
)
def test_the_shells_code_styles_and_fonts_are_served_by_this_app(
    dev: TestClient, path: str, content_type: str
) -> None:
    """Everything a layout loads comes from this origin: nothing depends on a CDN being up."""
    response = dev.get(path)
    assert response.status_code == HTTPStatus.OK
    assert response.headers["content-type"].startswith(content_type)


def test_a_layout_page_names_no_other_host_in_its_policy(dev: TestClient) -> None:
    """The CSP a layout page is served with allows styles, scripts and fonts from itself only."""
    policy = dev.get("/dev/layouts/board").headers["Content-Security-Policy"]
    for directive in ("script-src", "style-src", "font-src"):
        value = next(
            p.strip() for p in policy.split(";") if p.strip().startswith(directive)
        )
        assert "http" not in value, value
