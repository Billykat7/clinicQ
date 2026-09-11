"""Exception handlers that answer every API error with the one :class:`ErrorEnvelope` (Issue 4).

Four sources of error, one body shape (``detail``, ``code``, ``request_id``):

* **Domain errors** (:class:`~src.commons.exceptions.BKPropertyError` and its subclasses) raised
  anywhere below a route: the error's category decides the status (a ``NotFoundError`` is a 404, a
  ``ConflictError`` a 409), and its ``code`` goes on the wire. A route no longer needs a
  ``try``/``except`` to translate one; the kernel routes that still do keep working unchanged.
* **``HTTPException``**: ``detail`` and any headers (``WWW-Authenticate``, ``Location``) pass
  through untouched. ``code`` is ``http.<reason>`` (``http.not_found``), except when ``detail`` is
  a mapping that already names a ``code``, as the RBAC 403 does (``insufficient_permission``):
  that code is promoted.
* **Request validation** (422): FastAPI's list of field errors stays in ``detail``; ``code`` is
  ``request.invalid``.
* **Anything unhandled** (500): a fixed, generic ``detail`` so no internal message or stack trace
  reaches a client; the exception itself is still raised to the server log.

:data:`ERROR_RESPONSES` documents the envelope in OpenAPI for every ``/api/v1`` route.
"""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.utils import is_body_allowed_for_status_code
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from src.commons.exceptions import BKPropertyError, ErrorEnvelope
from src.core.request_logging import get_request_id

logger = logging.getLogger(__name__)

#: ``code`` for a request that failed FastAPI's validation of its path, query or body.
REQUEST_INVALID_CODE = "request.invalid"

#: ``code`` for an unhandled exception. Deliberately says nothing about what broke.
INTERNAL_ERROR_CODE = "internal.error"

#: Merged into the ``/api/v1`` router's ``responses`` so OpenAPI documents the envelope on every
#: route. The ``4XX`` key also replaces FastAPI's default ``HTTPValidationError`` 422 schema, which
#: would otherwise document a body this API no longer sends.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    "4XX": {
        "model": ErrorEnvelope,
        "description": "Client error: the request was refused. See `code`.",
    },
    "5XX": {
        "model": ErrorEnvelope,
        "description": "Server error: the request could not be completed. See `code`.",
    },
}


def http_error_code(status_code: int) -> str:
    """Return the envelope ``code`` for a bare HTTP status, e.g. ``404`` -> ``http.not_found``."""
    try:
        return f"http.{HTTPStatus(status_code).name.lower()}"
    except ValueError:
        return f"http.{status_code}"


def _envelope_response(
    *,
    status_code: int,
    detail: Any,
    code: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the JSON response for one error, stamping the current request id."""
    body = {"detail": detail, "code": code, "request_id": get_request_id()}
    return JSONResponse(
        jsonable_encoder(body), status_code=status_code, headers=headers
    )


async def domain_error_handler(request: Request, exc: Exception) -> Response:
    """Answer a domain error with its category's status and its own ``code``."""
    error = cast(BKPropertyError, exc)
    status_code = int(error.status_code)
    # One line naming the code, so a request id in the logs leads to *which* rule refused it;
    # the middleware's own line only has the status.
    logger.info(
        "Domain error %s answered %s",
        error.code,
        status_code,
        extra={"error_code": error.code, "status": status_code},
    )
    envelope = error.to_envelope(request_id=get_request_id())
    return JSONResponse(envelope.model_dump(mode="json"), status_code=status_code)


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    """Answer an ``HTTPException`` in the envelope, keeping its detail and headers."""
    error = cast(StarletteHTTPException, exc)
    if not is_body_allowed_for_status_code(error.status_code):
        return Response(status_code=error.status_code, headers=error.headers)
    detail = error.detail
    code = (
        detail["code"]
        if isinstance(detail, dict) and isinstance(detail.get("code"), str)
        else http_error_code(error.status_code)
    )
    return _envelope_response(
        status_code=error.status_code, detail=detail, code=code, headers=error.headers
    )


async def request_validation_handler(request: Request, exc: Exception) -> Response:
    """Answer a request-validation failure in the envelope, keeping FastAPI's error list."""
    error = cast(RequestValidationError, exc)
    return _envelope_response(
        status_code=HTTPStatus.UNPROCESSABLE_CONTENT,
        detail=error.errors(),
        code=REQUEST_INVALID_CODE,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> Response:
    """Answer an unhandled exception with a generic 500 envelope.

    Starlette calls this from its outermost middleware and then re-raises ``exc``, so the traceback
    still reaches the server log; the client only ever sees the fixed message below.
    """
    return _envelope_response(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        detail=HTTPStatus.INTERNAL_SERVER_ERROR.phrase,
        code=INTERNAL_ERROR_CODE,
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register the envelope handlers on ``app``. Called once, from ``create_app``."""
    app.add_exception_handler(BKPropertyError, domain_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
