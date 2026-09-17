"""CSRF protection for cookie-authenticated browser sessions (Issue 16).

The dashboard is a server-rendered app authenticated by cookies, so a page on another site could
make the browser send a state-changing request with those cookies attached. Three layers stop it,
cheapest first:

1. **``SameSite=Lax`` session cookies** (set by the auth routes): a cross-site ``POST`` does not
   carry them at all in a current browser.
2. **Fetch Metadata.** A browser labels every request with ``Sec-Fetch-Site``; an unsafe ``/api/``
   request labelled ``cross-site`` is refused outright, cookies or not. That also covers the
   sign-in endpoints, which have no session yet to protect (login CSRF). Clients that are not
   browsers send no such header and are unaffected.
3. **A signed double-submit token.** Every unsafe ``/api/`` request that carries a session cookie
   (the staff access cookie, or a patient's session cookie from Issue 17) must echo the readable CSRF cookie in ``X-CSRF-Token`` (or a ``csrf_token`` form field). The
   token is ``<nonce>.<HMAC(secret, session id, nonce)>``, **bound to the session** (``sid``, the
   refresh-token family): a token another session minted, planted in the victim's cookie jar from
   a sibling subdomain, does not validate against the victim's session. The comparison is
   constant-time.

A request that carries only the refresh cookie (the access cookie has expired) can reach nothing
but ``/auth/refresh`` and ``/auth/logout``; when it has a CSRF cookie it must echo it here, and those
two routes check the binding against the refresh token's own family (:func:`csrf_token_is_bound`).
``Authorization: Bearer`` clients carry no ambient credential and skip the token check.

One browser can hold **two** sessions at once — a staff one and a patient one (Issue 17) — and
they share a single CSRF cookie, so whichever signed in last owns it. The binding is therefore
checked against *every* session the request carries (:func:`_session_ids`): a token minted for
either of them is this browser's own, because both session cookies are ``httpOnly`` and only this
server sets them. Checking it against the staff session alone meant that signing in as a patient
made every staff write fail, and vice versa, until a cookie expired (Issue 229).

A request with **no session cookie at all** — signing in, signing up, asking for a reset link — is
let through whatever is in the CSRF cookie. There is no ambient credential for a token to protect
there, ``SameSite=Lax`` and the Fetch Metadata check above are what stop login CSRF, and requiring
the echo meant that one stale cookie a page could not read locked the browser out of signing in at
all, with no way back from the UI (Issue 229). A refresh cookie is still a session, so it still has
to echo.

Before Issue 16 the check was skipped whenever the CSRF cookie was absent, and compared with
``==``; the CSRF cookie also expired with the access token, 15 minutes in, while the refresh cookie
lived a week.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.core.config import get_settings

CSRF_HEADER_NAME = "X-CSRF-Token"
_CSRF_FORM_FIELD = "csrf_token"
_UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
#: ``Sec-Fetch-Site`` value a browser sends for a request initiated by another site.
_CROSS_SITE = "cross-site"


def _signing_key() -> bytes:
    """The HMAC key: derived from ``JWT_SECRET`` under a label of its own, never the secret itself."""
    return hashlib.sha256(
        b"clinicq-csrf-v1|" + get_settings().jwt_secret.encode()
    ).digest()


def _signature(session_id: str, nonce: str) -> str:
    """HMAC-SHA256 of ``session_id`` and ``nonce``, hex."""
    message = f"{session_id}|{nonce}".encode()
    return hmac.new(_signing_key(), message, hashlib.sha256).hexdigest()


def mint_csrf_token(session_id: str) -> str:
    """Return a fresh CSRF token bound to ``session_id`` (the refresh-token family)."""
    nonce = secrets.token_urlsafe(16)
    return f"{nonce}.{_signature(session_id, nonce)}"


def csrf_token_is_bound(token: str | None, session_id: str | None) -> bool:
    """Return whether ``token`` was minted for ``session_id`` (constant-time)."""
    if not token or not session_id or token.count(".") != 1:
        return False
    nonce, signature = token.split(".")
    return hmac.compare_digest(signature, _signature(session_id, nonce))


def _session_ids(request: Request) -> tuple[str, ...]:
    """Every session id this request is authenticated as, staff and patient, in that order.

    Both cookies are ``httpOnly`` and signed, so a session id that comes out of one is a session
    the browser genuinely holds: an attacker can plant a CSRF cookie, never a session cookie.
    """
    from src.core.security import session_id_from_access_token

    settings = get_settings()
    candidates = (
        request.cookies.get(settings.access_token_cookie_name),
        request.cookies.get(settings.patient_session_cookie_name),
    )
    return tuple(
        session_id
        for token in candidates
        if (session_id := session_id_from_access_token(token)) is not None
    )


def _csrf_protected_path(path: str) -> bool:
    """Return True for paths where the checks apply: the JSON API."""
    return path.startswith("/api/")


def _forbidden(detail: str) -> JSONResponse:
    """The one refusal shape: 403, with a reason a developer can act on and nothing secret."""
    return JSONResponse(status_code=403, content={"detail": detail})


async def _submitted_token(request: Request) -> str | None:
    """The token the request echoes: the header, or a ``csrf_token`` field on a native form post."""
    header = request.headers.get(CSRF_HEADER_NAME)
    if header:
        return header
    content_type = (request.headers.get("content-type") or "").lower()
    if (
        "multipart/form-data" in content_type
        or "application/x-www-form-urlencoded" in content_type
    ):
        try:
            form = await request.form()
        except Exception:
            # Malformed multipart/urlencoded body: treat as "no token submitted".
            return None
        raw = form.get(_CSRF_FORM_FIELD)
        return raw if isinstance(raw, str) else None
    return None


class CsrfProtectMiddleware(BaseHTTPMiddleware):
    """Refuse cross-site and unsigned state-changing ``/api/*`` requests (see the module docs)."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        """Apply Fetch Metadata, then the signed double-submit check, to unsafe API requests."""
        if request.method not in _UNSAFE_METHODS or not _csrf_protected_path(
            request.url.path
        ):
            return await call_next(request)
        if request.headers.get("sec-fetch-site", "").lower() == _CROSS_SITE:
            return _forbidden("Cross-site request refused")
        auth = request.headers.get("Authorization")
        if auth and auth.startswith("Bearer "):
            return await call_next(request)

        settings = get_settings()
        # The session cookies a browser is signed in with: the staff access cookie, a patient's
        # session cookie (Issue 17), or both at once. Either one makes the request
        # cookie-authenticated.
        signed_in = any(
            request.cookies.get(name)
            for name in (
                settings.access_token_cookie_name,
                settings.patient_session_cookie_name,
            )
        )
        csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
        if not signed_in:
            # No session cookie on this request. With only a refresh cookie, the routes it can
            # reach (refresh, logout) verify the binding themselves; the cookie still has to be
            # echoed when there is one.
            if csrf_cookie is None:
                return await call_next(request)
            if request.cookies.get(settings.refresh_token_cookie_name) is None:
                # Signing in, signing up, asking for a reset link: no ambient credential for a
                # token to protect, and a leftover CSRF cookie the page cannot read must not be
                # able to refuse it (see the module docs). Fetch Metadata above stops login CSRF.
                return await call_next(request)
            submitted = await _submitted_token(request)
            if submitted is not None and hmac.compare_digest(submitted, csrf_cookie):
                return await call_next(request)
            return _forbidden("Invalid or missing CSRF token")

        submitted = await _submitted_token(request)
        if (
            submitted is None
            or csrf_cookie is None
            or not hmac.compare_digest(submitted, csrf_cookie)
        ):
            return _forbidden("Invalid or missing CSRF token")
        session_ids = _session_ids(request)
        # A session cookie that is not a genuine token (or predates sessions) leaves nothing to
        # bind to; the route's own authentication answers it with a 401.
        if session_ids and not any(
            csrf_token_is_bound(submitted, session_id) for session_id in session_ids
        ):
            return _forbidden("Invalid or missing CSRF token")
        return await call_next(request)
