"""Sliding-window rate limiting for abuse-prone endpoints.

The public listing search (Issue #25), public application submission (#28), the password sign-in
fallback (#101), OTP request (via :mod:`src.core.otp_store`) and address geocoding (#129) are all
fronted by a per-key sliding window: a list of hit timestamps, pruned to a window, compared against
a cap.

**M30 (Issue #179) moved the storage, not the semantics.** Where the window lives is now a
configuration choice — see :mod:`src.core.rate_limit_backend` — so the same budget can hold across
every worker and instance instead of being multiplied by the worker count (pen-test finding
**F-02**). ``RATE_LIMIT_BACKEND`` defaults to ``memory``, which is exactly the pre-M30 behaviour, so
a single-worker development or CI run needs no store, no service and no new environment variable.

The keys these limiters count against come from :mod:`src.core.client_ip` — the one client-IP
resolver — which is the other half of the same finding: a per-IP budget keyed on the reverse proxy's
own address is one bucket for the entire internet (pen-test finding **F-04**).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.commons.enums import RateLimitBackendKind
from src.core.config import Settings, get_settings
from src.core.rate_limit_backend import (
    InProcessRateLimitBackend,
    RateLimitBackend,
    SharedRateLimitBackend,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _build_backend(settings: Settings) -> RateLimitBackend:
    """Construct the backend the settings select, falling back to in-process on any problem.

    A misconfigured shared store (no URL, driver not installed, bad URL) must not stop the app from
    booting: the limiter is a safety belt, and refusing to start because the belt's *preferred*
    storage is unavailable trades a small loss of strength for a total loss of service. The
    downgrade is logged at WARNING so it is visible rather than silent.
    """
    if settings.rate_limit_backend is not RateLimitBackendKind.REDIS:
        return InProcessRateLimitBackend()
    if not settings.rate_limit_redis_url:
        logger.warning(
            "RATE_LIMIT_BACKEND=redis but RATE_LIMIT_REDIS_URL is empty; "
            "using process-local rate-limit windows."
        )
        return InProcessRateLimitBackend()
    try:
        import redis  # imported lazily so the driver stays optional
    except ImportError:
        logger.warning(
            "RATE_LIMIT_BACKEND=redis but the redis package is not installed; "
            "using process-local rate-limit windows."
        )
        return InProcessRateLimitBackend()
    try:
        client = redis.Redis.from_url(
            settings.rate_limit_redis_url,
            socket_timeout=settings.rate_limit_redis_timeout_seconds,
            socket_connect_timeout=settings.rate_limit_redis_timeout_seconds,
            decode_responses=True,
        )
    except Exception as exc:  # a bad URL must not prevent boot
        logger.warning(
            "Rate-limit store could not be constructed (%s: %s); "
            "using process-local rate-limit windows.",
            type(exc).__name__,
            exc,
        )
        return InProcessRateLimitBackend()
    return SharedRateLimitBackend(client)


class SlidingWindowRateLimiter:
    """Per-key sliding-window limiter: at most ``limit`` hits per ``window_seconds``.

    The budget comparison lives here rather than in the backend, so every backend agrees on what
    "over the limit" means and a backend swap can never change a verdict — only where the count is
    kept. The backend is resolved lazily on first use and cached, because module-level limiters are
    constructed at import time, before ``Settings`` (and any test override of it) exists.
    """

    def __init__(
        self, backend_factory: Callable[[], RateLimitBackend] | None = None
    ) -> None:
        """Initialise an empty limiter.

        Args:
            backend_factory: Builds the backend on first use. Defaults to reading ``Settings``;
                tests inject a stub (e.g. a failing store) here.
        """
        self._backend: RateLimitBackend | None = None
        self._factory = backend_factory

    @property
    def backend(self) -> RateLimitBackend:
        """The resolved backend, constructed on first use."""
        if self._backend is None:
            factory = self._factory or (lambda: _build_backend(get_settings()))
            self._backend = factory()
        return self._backend

    def set_backend(self, backend: RateLimitBackend | None) -> None:
        """Replace (or clear, with ``None``) the resolved backend — test and reconfigure seam."""
        self._backend = backend

    def within_limit(self, key: str, *, limit: int, window_seconds: float) -> bool:
        """Report whether ``key`` is still under its budget **without recording a hit**.

        The OTP surface checks before sending the mail and records only after the send succeeds, so
        a provider failure does not spend the caller's allowance. Everything else uses
        :meth:`check_and_record`, which is the safer default: it cannot be called and then forgotten.
        """
        return self.backend.count(key, window_seconds=window_seconds) < limit

    def record(self, key: str, *, window_seconds: float) -> None:
        """Record one hit against ``key``, ignoring the resulting count.

        The recording half of the two-phase :meth:`within_limit` shape.
        """
        self.backend.hit(key, window_seconds=window_seconds)

    def check_and_record(self, key: str, *, limit: int, window_seconds: float) -> bool:
        """Record a hit for ``key`` and report whether it stays within the limit.

        Args:
            key: The bucket to count against (e.g. ``ip:203.0.113.7`` or ``email:a@b.com``).
            limit: Maximum hits allowed inside the window.
            window_seconds: Width of the sliding window, in seconds.

        Returns:
            ``True`` if the hit is allowed, ``False`` if it exceeds the limit. An over-limit hit is
            **still recorded**, so sustained abuse keeps tripping the limit rather than draining out
            of the window while the attacker keeps pushing.
        """
        return self.backend.hit(key, window_seconds=window_seconds) <= limit

    def reset(self) -> None:
        """Clear all tracked keys (test-isolation helper)."""
        if self._backend is not None:
            self._backend.reset()


# Module-global limiter for the public listing search. Kept here (not per-request) so the
# window is shared across requests; tests reset it via :meth:`SlidingWindowRateLimiter.reset`.
public_listings_limiter = SlidingWindowRateLimiter()

# Module-global limiter for public application submissions (Issue #28). Keys are prefixed
# (``ip:``/``email:``) so one limiter counts both the per-IP and per-email budgets without
# collisions; shared by the JSON endpoint and the server-rendered form, and reset in tests.
application_submissions_limiter = SlidingWindowRateLimiter()

# Module-global limiter for the password sign-in fallback (Issue #101). Unlike OTP login,
# password login authenticates on a single request, so it is brute-forceable without one; keys
# are prefixed (``ip:``/``email:``) so the one limiter counts both budgets. Reset in tests.
password_login_limiter = SlidingWindowRateLimiter()

# Module-global limiter for the OTP request surface (Issue #179 moved it off ``otp_store``'s own
# hand-rolled dictionaries onto this shared mechanism, so OTP shares the backend — and therefore
# the cross-worker window — with every other limiter). Keys are ``ip:``/``email:`` prefixed.
otp_request_limiter = SlidingWindowRateLimiter()

# Module-global limiter for signed download-link minting (Issue #179). Every private document,
# photo, statement and attachment leaves the system through one of these; the routes are
# authenticated, so this is not an outsider control but a blast-radius control on a *stolen*
# session — see ``src.core.rate_limit_deps`` for the reasoning. Keys are ``link:<ip>``.
signed_link_limiter = SlidingWindowRateLimiter()

# Module-global limiter for address geocoding on the property form (Issue #129). Unlike the
# limiters above it is not abuse protection — the endpoint is RBAC-gated — but *policy*
# compliance: the free Nominatim provider caps usage, and a server-side throttle is the only
# place that cap can be honoured across all callers. Keys are per signed-in caller (falling back
# to IP), so one busy user cannot spend everyone's budget. Reset in tests.
geocoding_limiter = SlidingWindowRateLimiter()

# Module-global limiter for the public discovery search (Issue 38): the web list, its fragments and
# the JSON API. Not a bot wall: a patient refining a search never reaches it, and a script walking
# the country tile by tile to copy the directory does. Keys are ``ip:`` and ``session:`` prefixed.
discovery_search_limiter = SlidingWindowRateLimiter()

# Module-global limiter for joining a queue (Issue 40): the abuse guards on the one join service.
# Keys are ``phone:`` (every remote channel), ``ip:`` (the web path) and ``site:<id>:<day>`` (a
# clinic's daily cap on remote joins), so one limiter counts all three budgets without collisions.
queue_join_limiter = SlidingWindowRateLimiter()

#: Every module-global limiter, so a test (or a reconfiguration) can reach them all without
#: naming each one and silently missing the next one added.
ALL_LIMITERS: tuple[SlidingWindowRateLimiter, ...] = (
    public_listings_limiter,
    application_submissions_limiter,
    password_login_limiter,
    otp_request_limiter,
    signed_link_limiter,
    geocoding_limiter,
    discovery_search_limiter,
    queue_join_limiter,
)


def reset_all_limiters() -> None:
    """Clear every module-global limiter's state (test-isolation helper)."""
    for limiter in ALL_LIMITERS:
        limiter.reset()
