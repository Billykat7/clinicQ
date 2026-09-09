"""
Security headers middleware for web responses (Issue 9).

Adds OWASP-recommended headers and a per-request CSP nonce for inline ``<style>`` blocks.
Ported from the ``maps`` project.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def _generate_csp_nonce() -> str:
    """Return a cryptographically strong nonce for CSP ``nonce-`` sources."""
    return secrets.token_urlsafe(16)


def build_content_security_policy(nonce: str) -> str:
    """Build a Content-Security-Policy value for HTML responses.

    The property map (Issue #129) fits this policy without loosening it, by design:

    * **Scripts.** Leaflet is *vendored* under ``/static/vendor/leaflet/``, so it loads from
      ``'self'`` — no CDN host, and no inline script (the map is driven by
      ``/static/js/property-map.js``).
    * **Tiles.** Map tiles are images from the tile host, already covered by the ``img-src``
      ``https:`` source below.
    * **Geocoding.** Address search goes to *this* origin, which proxies the provider server-side
      (see :mod:`src.modules.properties.geocoding`), so ``connect-src 'self'`` stays untouched —
      the browser never contacts a third-party geocoding host.
    """
    n = f"'nonce-{nonce}'"
    parts = [
        "default-src 'self'",
        f"script-src 'self' {n}",
        # Issue #181 (M30) removed ``'unsafe-inline'``, closing pen-test finding **F-03**. The
        # remaining surface was four ``style="…"`` attributes across three templates; they are now
        # classes in ``admin.css``. A nonce could not have covered *those* — a CSP nonce applies to
        # ``<style>`` **elements**, never to style *attributes* — so the choice was between moving
        # four declarations into CSS and keeping a weaker policy to accommodate them.
        # ``'unsafe-hashes'`` would have been the same compromise under a different name.
        #
        # The nonce **is** required here, and this is the part that is easy to get wrong: ``'self'``
        # covers same-origin *stylesheets*, not inline ``<style>`` elements. Dropping
        # ``'unsafe-inline'`` without adding the nonce silently blocks the one ``<style>`` block in
        # the tree (the login modal), which then renders completely unstyled — the nonce attribute
        # it already carried was decorative until the policy named a nonce source. Caught in a
        # browser, not by a header assertion, which is why Issue #181 insisted on verifying against
        # a real response; ``tests/integration/platform/test_security_headers.py`` now asserts the
        # nonce is in this directive so the same mistake cannot pass again.
        #
        # Unlike ``script-src``, adding a nonce here does *not* disable the host sources: source
        # expressions are OR-ed, and it is ``'strict-dynamic'`` — a script-only keyword, not used —
        # that would make host sources ignored. So same-origin stylesheets, the Google Fonts
        # stylesheet and the nonced block all continue to load.
        #
        # ``https://fonts.googleapis.com`` stays: it serves the font stylesheet, it is a named
        # audited host rather than a wildcard, and self-hosting the font is a separate change with
        # its own trade-off. It cannot be used to inject a style attribute.
        f"style-src 'self' {n} https://fonts.googleapis.com",
        # ``https:`` covers the OSM tile hosts the property map draws from; ``data:``/``blob:``
        # cover generated previews (e.g. an avatar chosen but not yet uploaded).
        "img-src 'self' data: blob: https:",
        "font-src 'self' https://fonts.gstatic.com",
        "connect-src 'self'",
        "worker-src 'self' blob:",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'self'",
        "object-src 'none'",
    ]
    return "; ".join(parts) + ";"


# Permissions-Policy: deny access to powerful browser features the app never uses, so a
# script injected despite the CSP still cannot reach the camera, microphone, geolocation,
# or payment/USB APIs. An empty allowlist ``()`` disables the feature for every origin,
# including this one.
_PERMISSIONS_POLICY = (
    "accelerometer=(), autoplay=(), camera=(), display-capture=(), "
    "encrypted-media=(), fullscreen=(self), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), midi=(), payment=(), usb=()"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.csp_nonce = _generate_csp_nonce()
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
        # Isolate this origin's browsing context so cross-origin popups it opens (and any
        # opener that framed it) cannot share a window reference — closes the reverse-tabnabbing
        # and Spectre-style cross-origin leakage vectors that frame-ancestors alone does not.
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = build_content_security_policy(
            request.state.csp_nonce
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
        _apply_html_cache_control(response)
        return response


def _apply_html_cache_control(response: Response) -> None:
    """Mark rendered HTML pages ``no-store`` so sensitive pages are never cached.

    Every server-rendered page (portal balances, tenant/owner data, the admin consoles) can carry
    PII, so it must not survive in a browser disk cache or a shared proxy — otherwise a
    back-button after sign-out, or the next user on a shared machine, could read it. This gates on
    the ``text/html`` content type so **static assets (CSS/JS/images/fonts) stay cacheable**, and
    it never overrides a handler that already set ``Cache-Control`` (the signed-document and
    statement downloads set their own ``no-store``). Pentest hardening, Issue #102.
    """
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" in content_type and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
