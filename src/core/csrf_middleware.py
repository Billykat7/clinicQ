"""CSRF double-submit middleware for cookie-based browser sessions.

Ported from the ``maps`` project. Applies to unsafe methods on ``/api/*``. When the
CSRF cookie is present, an ``X-CSRF-Token`` header (or a ``csrf_token`` form field for
native HTML form posts) must match it. Bearer-authenticated API clients skip the check,
as do requests without a CSRF cookie (e.g. OTP verify before cookies are issued).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.core.config import get_settings

_CSRF_HEADER_NAME = "X-CSRF-Token"
_CSRF_FORM_FIELD = "csrf_token"
_UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _csrf_protected_path(path: str) -> bool:
    """Return True for paths where double-submit CSRF applies when the cookie is set."""
    return path.startswith("/api/")


class CsrfProtectMiddleware(BaseHTTPMiddleware):
    """Validate double-submit CSRF for ``/api/*`` writes when the CSRF cookie is set."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        """Reject unsafe API requests when the CSRF cookie and token do not match."""
        if request.method not in _UNSAFE_METHODS:
            return await call_next(request)
        if not _csrf_protected_path(request.url.path):
            return await call_next(request)
        auth = request.headers.get("Authorization")
        if auth and auth.startswith("Bearer "):
            return await call_next(request)
        csrf_cookie = request.cookies.get(get_settings().csrf_cookie_name)
        if not csrf_cookie:
            return await call_next(request)
        header = request.headers.get(_CSRF_HEADER_NAME)
        if header == csrf_cookie:
            return await call_next(request)
        content_type = (request.headers.get("content-type") or "").lower()
        if (
            "multipart/form-data" in content_type
            or "application/x-www-form-urlencoded" in content_type
        ):
            try:
                form = await request.form()
                raw = form.get(_CSRF_FORM_FIELD)
                submitted = raw if isinstance(raw, str) else None
            except Exception:
                # Malformed multipart/urlencoded body: treat as "no token submitted".
                submitted = None
            if submitted == csrf_cookie:
                return await call_next(request)
        return JSONResponse(
            status_code=403,
            content={"detail": "Invalid or missing CSRF token"},
        )
