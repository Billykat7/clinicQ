"""The CORS policy and debug mode the settings choose are the ones the app serves (Issue 12).

Refusing ``CORS_ORIGINS=*`` and ``DEBUG=true`` outside development only matters if those settings
drive the app; these tests prove they do, over HTTP. By default the app sends no CORS headers at
all: a page on another origin cannot read its responses.
"""

from fastapi.testclient import TestClient

from src.commons.enums import AppEnvironment
from src.core.config import CORS_ANY_ORIGIN, Settings
from src.main import create_app

CLINIC_ORIGIN = "https://clinicq.example.org"
OTHER_ORIGIN = "https://elsewhere.example.com"
ALLOW_ORIGIN = "access-control-allow-origin"
ALLOW_CREDENTIALS = "access-control-allow-credentials"


def _client(**values: object) -> TestClient:
    """A client for an app built from ``values``, isolated from the local .env."""
    settings = Settings(
        _env_file=None, environment=AppEnvironment.DEVELOPMENT, **values
    )
    return TestClient(create_app(settings))


def test_the_app_sends_no_cors_headers_unless_origins_are_named() -> None:
    """Same-origin only by default: no header for any origin, preflight included."""
    client = _client()
    response = client.get("/health", headers={"Origin": OTHER_ORIGIN})
    preflight = client.options(
        "/health",
        headers={"Origin": OTHER_ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert ALLOW_ORIGIN not in response.headers
    assert ALLOW_ORIGIN not in preflight.headers


def test_a_named_origin_is_allowed_with_credentials_and_no_other_is() -> None:
    """Exactly the origins listed, which may send cookies; every other origin gets nothing."""
    client = _client(cors_origins=CLINIC_ORIGIN)
    allowed = client.get("/health", headers={"Origin": CLINIC_ORIGIN})
    preflight = client.options(
        "/health",
        headers={"Origin": CLINIC_ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    refused = client.get("/health", headers={"Origin": OTHER_ORIGIN})

    assert allowed.headers[ALLOW_ORIGIN] == CLINIC_ORIGIN
    assert allowed.headers[ALLOW_CREDENTIALS] == "true"
    assert preflight.status_code == 200
    assert preflight.headers[ALLOW_ORIGIN] == CLINIC_ORIGIN
    assert ALLOW_ORIGIN not in refused.headers


def test_the_development_wildcard_never_offers_credentials() -> None:
    """``*`` (development only) opens reads to every origin, but never with the user's cookies."""
    response = _client(cors_origins=CORS_ANY_ORIGIN).get(
        "/health", headers={"Origin": OTHER_ORIGIN}
    )
    assert response.headers[ALLOW_ORIGIN] == CORS_ANY_ORIGIN
    assert ALLOW_CREDENTIALS not in response.headers


def test_debug_mode_follows_the_setting_and_is_off_by_default() -> None:
    """FastAPI's debug flag (tracebacks in error responses) is DEBUG, false unless set."""
    assert _client().app.debug is False  # type: ignore[attr-defined]
    assert _client(debug=True).app.debug is True  # type: ignore[attr-defined]
