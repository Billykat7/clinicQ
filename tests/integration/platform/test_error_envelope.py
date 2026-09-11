"""One error envelope for every API error, documented in OpenAPI with the enums (Issue 4).

Drives real requests through the app ``create_app()`` builds, so the handlers, the request-logging
middleware (which supplies ``request_id``) and routing are all the production ones. The domain
errors are raised from a stand-in *service* function, below a route that does no mapping of its
own: that is the case the envelope exists for.
"""

from collections.abc import Iterator
from http import HTTPStatus

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.commons.enums import (
    SITE_DEFAULT_DISPLAY_MODE,
    TICKET_TERMINAL_STATUSES,
    AppEnvironment,
    DisplayMode,
    SiteSector,
    TicketSource,
    TicketStatus,
    UserRole,
)
from src.commons.exceptions import (
    BKPropertyError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
    UpstreamError,
)
from src.core.config import Settings
from src.core.rbac import raise_forbidden
from src.main import create_app


class TicketNotFoundError(NotFoundError):
    """What a ClinicQ module's error looks like: a category base and a stable code."""

    def __init__(self, ticket_id: str) -> None:
        super().__init__(f"No such ticket: {ticket_id}.", code="queue.ticket.not_found")


_DOMAIN_ERRORS: dict[str, tuple[BKPropertyError, HTTPStatus]] = {
    "not-found": (TicketNotFoundError("01a0"), HTTPStatus.NOT_FOUND),
    "conflict": (
        ConflictError(
            "Ticket cannot move from done to called.", code="queue.ticket.illegal"
        ),
        HTTPStatus.CONFLICT,
    ),
    "unprocessable": (
        UnprocessableError("Reason text is too long.", code="queue.reason.too_long"),
        HTTPStatus.UNPROCESSABLE_CONTENT,
    ),
    "forbidden": (
        ForbiddenError("Reception cannot close a site.", code="sites.forbidden"),
        HTTPStatus.FORBIDDEN,
    ),
    "upstream": (
        UpstreamError("The SMS gateway failed.", code="sms.gateway_error"),
        HTTPStatus.BAD_GATEWAY,
    ),
    "unavailable": (
        ServiceUnavailableError("SMS is not enabled.", code="sms.disabled"),
        HTTPStatus.SERVICE_UNAVAILABLE,
    ),
    "base": (BKPropertyError("Something the rules refuse."), HTTPStatus.BAD_REQUEST),
}


def _service_that_raises(kind: str) -> None:
    """A stand-in service: it raises a domain error and knows nothing about HTTP."""
    raise _DOMAIN_ERRORS[kind][0]


class _Body(BaseModel):
    count: int


#: Where the demo routes live. They are added to the app directly, not through the ``/api/v1``
#: router, so the OpenAPI check below leaves them out.
_DEMO_PREFIX = "/api/v1/_issue4"


def _demo_router() -> APIRouter:
    """Routes that fail in each of the ways the envelope covers, and map none of them."""
    router = APIRouter(prefix=_DEMO_PREFIX)

    @router.get("/domain/{kind}")
    def domain(kind: str) -> None:
        _service_that_raises(kind)

    @router.get("/rbac")
    def rbac() -> None:
        raise_forbidden("sites", "update")

    @router.get("/unauthorised")
    def unauthorised() -> None:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @router.post("/validated")
    def validated(body: _Body) -> _Body:
        return body

    @router.get("/crash")
    def crash() -> None:
        raise RuntimeError("database password is hunter2")

    return router


@pytest.fixture
def app() -> FastAPI:
    """The production app, with the demo routes added and the local ``.env`` ignored."""
    application = create_app(
        Settings(_env_file=None, environment=AppEnvironment.DEVELOPMENT)
    )
    application.include_router(_demo_router())
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """A client that turns an unhandled exception into its 500 response, as a server would."""
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _assert_envelope(body: dict[str, object]) -> None:
    """Exactly the three envelope fields, with a request id a user could quote."""
    assert set(body) == {"detail", "code", "request_id"}
    assert isinstance(body["request_id"], str) and body["request_id"]


@pytest.mark.parametrize("kind", sorted(_DOMAIN_ERRORS))
def test_a_domain_error_becomes_the_envelope_with_its_category_status(
    client: TestClient, kind: str
) -> None:
    """The route maps nothing: the category decides the status and the error's code is on the wire."""
    error, status = _DOMAIN_ERRORS[kind]

    response = client.get(f"{_DEMO_PREFIX}/domain/{kind}")

    assert response.status_code == status
    body = response.json()
    _assert_envelope(body)
    assert body["detail"] == str(error)
    assert body["code"] == error.code


def test_an_unknown_api_path_is_a_404_envelope(client: TestClient) -> None:
    """Routing's own ``HTTPException`` answers in the envelope too."""
    response = client.get("/api/v1/no-such-thing")

    assert response.status_code == HTTPStatus.NOT_FOUND
    body = response.json()
    _assert_envelope(body)
    assert body == {**body, "detail": "Not Found", "code": "http.not_found"}


def test_http_exception_headers_survive(client: TestClient) -> None:
    """A 401's ``WWW-Authenticate`` header is kept, so a client still knows how to sign in."""
    response = client.get(f"{_DEMO_PREFIX}/unauthorised")

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["code"] == "http.unauthorized"


def test_the_rbac_refusal_keeps_its_detail_and_promotes_its_code(
    client: TestClient,
) -> None:
    """The kernel's structured 403 is unchanged for existing clients, and gains the top-level code."""
    response = client.get(f"{_DEMO_PREFIX}/rbac")

    assert response.status_code == HTTPStatus.FORBIDDEN
    body = response.json()
    _assert_envelope(body)
    assert body["code"] == body["detail"]["code"] == "insufficient_permission"
    assert body["detail"]["resource"] == "sites"


def test_a_validation_error_keeps_fastapis_field_list(client: TestClient) -> None:
    """422 carries the field errors the static JS already reads from ``detail[0].msg``."""
    response = client.post(f"{_DEMO_PREFIX}/validated", json={"count": "many"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
    body = response.json()
    _assert_envelope(body)
    assert body["code"] == "request.invalid"
    assert body["detail"][0]["loc"] == ["body", "count"]
    assert body["detail"][0]["msg"]


def test_an_unhandled_error_is_a_generic_500_that_leaks_nothing(
    client: TestClient,
) -> None:
    """The exception's message never reaches the client; the code says only that it was internal."""
    response = client.get(f"{_DEMO_PREFIX}/crash")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.json()
    assert body["detail"] == "Internal Server Error"
    assert body["code"] == "internal.error"
    assert "hunter2" not in response.text


def test_the_envelope_is_documented_on_every_api_route(app: FastAPI) -> None:
    """OpenAPI names ``ErrorEnvelope`` for 4XX and 5XX on every ``/api/v1`` operation."""
    schema = app.openapi()
    assert schema["components"]["schemas"]["ErrorEnvelope"]["required"] == [
        "detail",
        "code",
    ]
    undocumented = [
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        if path.startswith("/api/v1/") and not path.startswith(_DEMO_PREFIX)
        for method, operation in operations.items()
        if operation["responses"]
        .get("4XX", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
        != {"$ref": "#/components/schemas/ErrorEnvelope"}
    ]
    assert undocumented == []


@pytest.mark.parametrize(
    "enum",
    [SiteSector, TicketStatus, TicketSource, DisplayMode, UserRole],
    ids=lambda enum: enum.__name__,
)
def test_openapi_lists_each_enum_with_its_allowed_values(
    app: FastAPI, enum: type
) -> None:
    """Each ClinicQ enum is a named schema whose ``enum`` is exactly its members' values."""
    component = app.openapi()["components"]["schemas"][enum.__name__]
    assert component["type"] == "string"
    assert component["enum"] == [member.value for member in enum]


def test_the_reference_endpoint_returns_the_vocabulary(client: TestClient) -> None:
    """``GET /api/v1/reference/enums`` needs no sign-in and returns every allowed value."""
    response = client.get("/api/v1/reference/enums")

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["ticket_statuses"] == [s.value for s in TicketStatus]
    assert set(body["ticket_terminal_statuses"]) == {
        s.value for s in TICKET_TERMINAL_STATUSES
    }
    assert body["ticket_sources"] == ["web", "ussd", "whatsapp", "walk_in"]
    assert body["site_sectors"] == ["public", "private"]
    assert body["display_modes"] == ["number_only", "name_lite", "full"]
    assert (
        body["site_default_display_mode"] == SITE_DEFAULT_DISPLAY_MODE == "number_only"
    )
    assert UserRole.RECEPTIONIST.value in body["user_roles"]
