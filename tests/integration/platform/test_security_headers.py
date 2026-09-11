"""Integration tests for the security-headers middleware (Issue #101).

``SecurityHeadersMiddleware`` runs on every response, so these hit a dependency-free
endpoint (``/health/live``) and assert the OWASP-recommended headers are present with the
expected values — including the ``Permissions-Policy`` and ``Cross-Origin-Opener-Policy``
added in the M17 hardening pass, and the CSP directives (``frame-ancestors``, ``object-src``,
``form-action``) that anchor the framing/clickjacking controls.

Per ``.cursor/rules/testing-strategy.mdc`` these assert response headers (never HTML body)
and build isolated ``Settings`` (``_env_file=None``) so a developer's local ``.env`` cannot
change outcomes.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from starlette import status

from src.commons.enums import AppEnvironment
from src.core.config import Settings, get_settings
from src.main import create_app

_TEST_JWT_SECRET = "security-headers-test-secret-min-32-characters"


def _settings(**overrides: object) -> Settings:
    """Isolated ``Settings`` (no ``.env``)."""
    base: dict[str, object] = {
        "_env_file": None,
        "environment": AppEnvironment.DEVELOPMENT,
        "jwt_secret": _TEST_JWT_SECRET,
        "smtp_host": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def client() -> Generator[TestClient]:
    """A ``TestClient`` with isolated settings (no DB needed for ``/health/live``)."""
    settings = _settings()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_baseline_security_headers_present(client: TestClient) -> None:
    """nosniff, frame options, referrer policy and CSP are set on every response."""
    resp = client.get("/health/live")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in resp.headers


def test_permissions_policy_denies_powerful_features(client: TestClient) -> None:
    """Permissions-Policy locks down camera/microphone/geolocation/payment."""
    policy = client.get("/health/live").headers["Permissions-Policy"]
    for feature in ("camera=()", "microphone=()", "geolocation=()", "payment=()"):
        assert feature in policy


def test_cross_origin_opener_policy_isolates_origin(client: TestClient) -> None:
    """COOP is same-origin so opened popups cannot share a window reference."""
    resp = client.get("/health/live")
    assert resp.headers["Cross-Origin-Opener-Policy"] == "same-origin"


def test_csp_pins_framing_and_object_controls(client: TestClient) -> None:
    """CSP restricts framing ancestors, form actions and plugin/object sources."""
    csp = client.get("/health/live").headers["Content-Security-Policy"]
    assert "frame-ancestors 'self'" in csp
    assert "object-src 'none'" in csp
    assert "form-action 'self'" in csp


def test_style_src_has_no_unsafe_inline(client: TestClient) -> None:
    """Pen-test finding **F-03**, closed — asserted on a served header, not on the middleware.

    Reading the policy out of `build_content_security_policy` would prove only that a string
    literal changed. What matters is what a browser receives, so this reads the header off a real
    response, which is also what a re-test would do.
    """
    csp = client.get("/health/live").headers["Content-Security-Policy"]
    style_src = next(
        part.strip() for part in csp.split(";") if part.strip().startswith("style-src")
    )
    assert "'unsafe-inline'" not in style_src, style_src


def test_styles_and_fonts_come_only_from_this_origin(client: TestClient) -> None:
    """No third-party host serves CSS or fonts (Issue 5).

    Issue #181 kept `https://fonts.googleapis.com` and named self-hosting "a separate change with
    its own trade-off". Issue 5 made that change: the fonts are self-hosted under `/static/fonts/`,
    because a runtime CDN request for CSS is exactly what its acceptance criteria forbid, a clinic's
    board must render offline, and a visitor's address should not go to a font CDN. So both
    directives now name this origin alone, plus the style nonce.
    """
    csp = client.get("/health/live").headers["Content-Security-Policy"]
    directives = {
        part.strip().split(" ", 1)[0]: part.strip()
        for part in csp.split(";")
        if part.strip()
    }
    assert directives["style-src"].startswith("style-src 'self' 'nonce-")
    assert "https:" not in directives["style-src"]
    assert directives["font-src"] == "font-src 'self'"


def test_script_src_is_still_nonce_locked(client: TestClient) -> None:
    """The directive Issue #181 did **not** touch, asserted so the change is provably narrow."""
    csp = client.get("/health/live").headers["Content-Security-Policy"]
    script_src = next(
        part.strip() for part in csp.split(";") if part.strip().startswith("script-src")
    )
    assert script_src.startswith("script-src 'self' 'nonce-")
    assert "'unsafe-inline'" not in script_src


def test_no_directive_anywhere_in_the_policy_allows_unsafe_inline(
    client: TestClient,
) -> None:
    """The claim a reader actually cares about: the policy contains no `'unsafe-inline'` at all."""
    csp = client.get("/health/live").headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in csp, csp


def test_style_src_carries_the_nonce_so_a_style_block_can_still_load(
    client: TestClient,
) -> None:
    """Dropping `'unsafe-inline'` without this silently breaks every `<style>` element.

    `'self'` covers same-origin **stylesheets**, not inline `<style>` blocks — so the login modal's
    block, which already carried a nonce attribute, was only ever working because of
    `'unsafe-inline'`. Removing that keyword blocked it outright and the modal rendered as unstyled
    text at the bottom of the page. A header assertion would not have caught it (the JSON endpoint
    these tests hit has no `<style>` block); a browser did.

    Asserted as *the same nonce as* `script-src`, because a different one would also pass a naive
    "contains 'nonce-'" check while blocking the block just as thoroughly.
    """
    csp = client.get("/health/live").headers["Content-Security-Policy"]
    directives = {
        part.strip().split(" ", 1)[0]: part.strip()
        for part in csp.split(";")
        if part.strip()
    }
    nonce = directives["script-src"].split("'nonce-", 1)[1].split("'", 1)[0]
    assert f"'nonce-{nonce}'" in directives["style-src"], directives["style-src"]


def test_html_pages_are_not_cached(client: TestClient) -> None:
    """Rendered HTML pages are ``no-store`` so sensitive pages never sit in a cache (Issue #102).

    The home page is server-rendered HTML; a rendered page can carry PII, so it must not be
    retained by a browser disk cache or a shared proxy (back-button-after-signout exposure).
    """
    resp = client.get("/")
    assert "text/html" in resp.headers.get("content-type", "").lower()
    assert resp.headers.get("Cache-Control") == "no-store"
    assert resp.headers.get("Pragma") == "no-cache"


def test_non_html_responses_are_not_forced_no_store(client: TestClient) -> None:
    """The no-store rule is gated on HTML, so JSON/static responses keep their own caching.

    ``/health/live`` is JSON, so the HTML-only cache rule must not stamp ``no-store`` on it —
    static assets (CSS/JS/images) must stay cacheable.
    """
    resp = client.get("/health/live")
    assert "text/html" not in resp.headers.get("content-type", "").lower()
    assert resp.headers.get("Cache-Control") != "no-store"
