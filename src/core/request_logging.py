"""Request context for structured logging (request_id, path, method, client_ip).

``RequestLoggingMiddleware`` sets per-request context vars for the duration of each
request and logs 4xx at WARNING / 5xx at ERROR (one line per response). 401/403
responses additionally emit a ``SECURITY_HTTP`` JSON line for auth-anomaly signals.
``RequestContextFilter`` injects the context onto every log record so console and S3
logs can be correlated. Ported and adapted from the ``maps`` project.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.commons.enums import HttpSecurityResponseEvent, SecurityAuditOutcome
from src.core.client_ip import resolve_client_ip

# Context vars set per request; cleared when the request ends.
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
_path_ctx: ContextVar[str | None] = ContextVar("path", default=None)
_method_ctx: ContextVar[str | None] = ContextVar("method", default=None)
_client_ip_ctx: ContextVar[str | None] = ContextVar("client_ip", default=None)


class RequestContextFilter(logging.Filter):
    """Inject request context (request_id, path, method, client_ip) onto records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach any set context vars to the record and always return True."""
        request_id = _request_id_ctx.get()
        if request_id is not None:
            record.request_id = request_id
        path = _path_ctx.get()
        if path is not None:
            record.path = path
        method = _method_ctx.get()
        if method is not None:
            record.method = method
        client_ip = _client_ip_ctx.get()
        if client_ip is not None:
            record.client_ip = client_ip
        return True


def add_request_context_filter_to_logger(logger: logging.Logger) -> None:
    """Add ``RequestContextFilter`` to a logger once (e.g. the root logger)."""
    for f in logger.filters:
        if isinstance(f, RequestContextFilter):
            return
    logger.addFilter(RequestContextFilter())


def _set_request_context(request: Request) -> None:
    """Set context vars for the current request."""
    _request_id_ctx.set(str(uuid.uuid4()))
    _path_ctx.set(request.url.path or "")
    _method_ctx.set(request.method or "")
    _client_ip_ctx.set(resolve_client_ip(request))


def _clear_request_context() -> None:
    """Clear context vars after the request completes."""
    _request_id_ctx.set(None)
    _path_ctx.set(None)
    _method_ctx.set(None)
    _client_ip_ctx.set(None)


def get_request_id() -> str | None:
    """Return the current request's id, or None outside a request context."""
    return _request_id_ctx.get()


def get_client_ip() -> str | None:
    """Return the current request's client IP, or None outside a request context."""
    return _client_ip_ctx.get()


def get_request_path() -> str | None:
    """Return the current request's path, or None outside a request context."""
    p = _path_ctx.get()
    return p if p else None


def get_request_method() -> str | None:
    """Return the current request's HTTP method, or None outside a request context."""
    m = _method_ctx.get()
    return m if m else None


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Set request context, then log 4xx at WARNING and 5xx at ERROR.

    401/403 also emit a ``SECURITY_HTTP`` JSON line with the auth-anomaly signal.
    """

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._logger = logging.getLogger(__name__)

    def _emit_security_http(
        self,
        *,
        event: HttpSecurityResponseEvent,
        status_code: int,
        request: Request,
        client_ip: str | None,
    ) -> None:
        """Emit one ``SECURITY_HTTP`` JSON line for a 401/403 response."""
        signal_payload = {
            "client_ip": client_ip,
            "event": event.value,
            "http_status": status_code,
            "method": request.method,
            "outcome": SecurityAuditOutcome.FAILURE.value,
            "path": request.url.path or "",
        }
        self._logger.warning(
            "SECURITY_HTTP %s",
            json.dumps(signal_payload, sort_keys=True),
            extra={"status": status_code, "security_http_event": event.value},
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Set context, call downstream, log the response, then clear context."""
        _set_request_context(request)
        try:
            response = await call_next(request)
            status = response.status_code
            client_ip = _client_ip_ctx.get()
            if status >= 500:
                self._logger.error(
                    "HTTP %s %s %s",
                    status,
                    request.method,
                    request.url.path,
                    extra={"status": status},
                )
            elif status == 401:
                self._emit_security_http(
                    event=HttpSecurityResponseEvent.UNAUTHORIZED,
                    status_code=status,
                    request=request,
                    client_ip=client_ip,
                )
            elif status == 403:
                self._emit_security_http(
                    event=HttpSecurityResponseEvent.FORBIDDEN,
                    status_code=status,
                    request=request,
                    client_ip=client_ip,
                )
            elif status >= 400:
                self._logger.warning(
                    "HTTP %s %s %s",
                    status,
                    request.method,
                    request.url.path,
                    extra={"status": status},
                )
            return response
        finally:
            _clear_request_context()
