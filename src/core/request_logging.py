"""Request context for structured logging, and the request id a user can quote (Issue 6).

Every request gets one :class:`RequestContext`: its id, method, path and client IP, plus the
``site_id`` and ``actor_id`` that are known only once the request has been authenticated and
scoped. :class:`RequestContextFilter` copies it onto every log record written while the request is
in flight, from any module, so ``grep <request id>`` finds every line about one request.

**One mutable object per request, in one context variable.** The site and actor are bound late:
``get_current_user`` binds the actor inside a dependency, and a sync dependency runs in a worker
thread. A value *set* there in a context variable would never be seen by this middleware's own
closing line, which runs in the request's task. Mutating the one shared object is seen everywhere
(:func:`bind_request_context`).

**The request id is returned** in the ``X-Request-ID`` response header, on every response this
middleware sees, so a user can quote it in a support message and a developer can find the lines.
It is a UUIDv7 (sortable by time). Behind the trusted proxy (``TRUST_PROXY_HEADERS``), an incoming
``X-Request-ID`` in a safe format is kept instead, so the id nginx logged is the id the app logs.

The middleware is plain ASGI rather than ``BaseHTTPMiddleware``, so the header goes on streamed
responses too (the board's server-sent events, M8), and it is installed outermost, so even a
request the CSRF middleware refuses carries an id. It logs one line per error response: 4xx at
WARNING, 5xx at ERROR, plus a ``SECURITY_HTTP`` JSON line for 401/403. An unhandled exception is
logged with its traceback and the request's context before it propagates.
"""

import json
import logging
import re
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.commons.enums import HttpSecurityResponseEvent, SecurityAuditOutcome
from src.commons.ids import new_id
from src.core.client_ip import resolve_client_ip
from src.core.config import get_settings
from src.core.telemetry import tag_request

#: The response (and trusted request) header that carries the request id.
REQUEST_ID_HEADER: Final = "X-Request-ID"

#: What an incoming request id may look like before it is trusted: nginx's ``$request_id`` is 32
#: hex characters, a UUID is 36. Anything else (too long, spaces, control characters that could
#: forge a log line) is replaced by a fresh id.
_SAFE_REQUEST_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")

#: The ``scope["state"]`` key the context is also kept under, for code that runs outside the
#: context variable's lifetime (Starlette's outermost error handler).
_STATE_KEY: Final = "request_context"


@dataclass(slots=True)
class RequestContext:
    """What every log line about one request carries. ``None`` until known."""

    request_id: str
    method: str
    path: str
    client_ip: str | None
    site_id: str | None = None
    actor_id: str | None = None


#: The one context variable: the object for the request being served.
_current: ContextVar[RequestContext | None] = ContextVar(
    "request_context", default=None
)


def current_request_context() -> RequestContext | None:
    """Return the context of the request being served, or None outside a request."""
    return _current.get()


def bind_request_context(
    *, site_id: str | None = None, actor_id: str | None = None
) -> None:
    """Attach the site and/or actor to the current request's context, once they are known.

    Called by ``get_current_user`` (the actor) and, from Issue 19, by the tenancy helper (the
    site). Arguments left ``None`` leave the context as it is. Outside a request it does nothing.
    """
    ctx = _current.get()
    if ctx is None:
        return
    if site_id is not None:
        ctx.site_id = str(site_id)
    if actor_id is not None:
        ctx.actor_id = str(actor_id)


def get_request_id() -> str | None:
    """Return the current request's id, or None outside a request context."""
    ctx = _current.get()
    return ctx.request_id if ctx else None


def request_id_for(request: Request) -> str | None:
    """Return ``request``'s id, even where the context variable has already been reset.

    Starlette runs its handler for an unhandled exception outside every middleware, after this
    one has finished; the context is still on ``request.state`` there.
    """
    ctx = getattr(request.state, _STATE_KEY, None) or _current.get()
    return ctx.request_id if ctx else None


def get_client_ip() -> str | None:
    """Return the current request's client IP, or None outside a request context."""
    ctx = _current.get()
    return ctx.client_ip if ctx else None


def get_request_path() -> str | None:
    """Return the current request's path, or None outside a request context."""
    ctx = _current.get()
    return (ctx.path or None) if ctx else None


def get_request_method() -> str | None:
    """Return the current request's HTTP method, or None outside a request context."""
    ctx = _current.get()
    return (ctx.method or None) if ctx else None


class RequestContextFilter(logging.Filter):
    """Copy the current request's context onto every record (never drops a record)."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Set ``request_id``, ``method``, ``path``, ``client_ip``, ``site_id``, ``actor_id``.

        A field is only set when the record does not already carry it, so an explicit
        ``extra={"path": ...}`` from the caller wins.
        """
        ctx = _current.get()
        if ctx is not None:
            for name, value in asdict(ctx).items():
                if value is not None and not hasattr(record, name):
                    setattr(record, name, value)
        return True


def add_request_context_filter_to_logger(logger: logging.Logger) -> None:
    """Add ``RequestContextFilter`` to a logger once (e.g. the root logger)."""
    for f in logger.filters:
        if isinstance(f, RequestContextFilter):
            return
    logger.addFilter(RequestContextFilter())


def _request_id_from(request: Request, *, trust_proxy_headers: bool) -> str:
    """Keep a trusted proxy's safe ``X-Request-ID``; otherwise mint a new UUIDv7."""
    if trust_proxy_headers:
        inbound = request.headers.get(REQUEST_ID_HEADER, "")
        if _SAFE_REQUEST_ID.fullmatch(inbound):
            return inbound
    return new_id()


class RequestLoggingMiddleware:
    """Give each HTTP request its context and id, echo the id, and log error responses."""

    def __init__(
        self, app: ASGIApp, *, trust_proxy_headers: bool | None = None
    ) -> None:
        """Wrap ``app``. ``trust_proxy_headers`` defaults to the process settings' value.

        ``create_app`` passes the value of the settings it was built with, so an app built for a
        test (or a second app in one process) honours its own trust boundary.
        """
        self.app = app
        self._trust_proxy_headers = trust_proxy_headers
        self._logger = logging.getLogger(__name__)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one request with its context bound; log how it ended."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        ctx = RequestContext(
            request_id=_request_id_from(
                request,
                trust_proxy_headers=(
                    self._trust_proxy_headers
                    if self._trust_proxy_headers is not None
                    else get_settings().trust_proxy_headers
                ),
            ),
            method=request.method,
            path=request.url.path,
            client_ip=resolve_client_ip(request),
        )
        scope.setdefault("state", {})[_STATE_KEY] = ctx
        token = _current.set(ctx)
        # Error tracking tags this request's events with its id (Issue 14); a no-op when it is off.
        tag_request(ctx.request_id)
        status_code: int | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = ctx.request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            # Logged here, while the context is still bound, so the traceback carries the
            # request id; Starlette's outermost handler then answers the 500 and re-raises.
            self._logger.exception(
                "Unhandled error on %s %s", ctx.method, ctx.path, extra={"status": 500}
            )
            raise
        else:
            if status_code is not None:
                self._log_response(status_code, ctx)
        finally:
            _current.reset(token)

    def _log_response(self, status_code: int, ctx: RequestContext) -> None:
        """One line per error response; 2xx and 3xx are not logged here."""
        if status_code >= 500:
            self._logger.error(
                "HTTP %s %s %s",
                status_code,
                ctx.method,
                ctx.path,
                extra={"status": status_code},
            )
        elif status_code in (401, 403):
            event = (
                HttpSecurityResponseEvent.UNAUTHORIZED
                if status_code == 401
                else HttpSecurityResponseEvent.FORBIDDEN
            )
            self._emit_security_http(event=event, status_code=status_code, ctx=ctx)
        elif status_code >= 400:
            self._logger.warning(
                "HTTP %s %s %s",
                status_code,
                ctx.method,
                ctx.path,
                extra={"status": status_code},
            )

    def _emit_security_http(
        self,
        *,
        event: HttpSecurityResponseEvent,
        status_code: int,
        ctx: RequestContext,
    ) -> None:
        """Emit one ``SECURITY_HTTP`` JSON line for a 401/403 response."""
        signal_payload = {
            "client_ip": ctx.client_ip,
            "event": event.value,
            "http_status": status_code,
            "method": ctx.method,
            "outcome": SecurityAuditOutcome.FAILURE.value,
            "path": ctx.path,
        }
        self._logger.warning(
            "SECURITY_HTTP %s",
            json.dumps(signal_payload, sort_keys=True),
            extra={"status": status_code, "security_http_event": event.value},
        )
