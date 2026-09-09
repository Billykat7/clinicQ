"""Where a sliding window lives: in this process, or in a store every process shares (Issue #179).

Pen-test finding **F-02**: the limiters were process-local `SlidingWindowRateLimiter` instances, so
each Uvicorn worker kept its own window and the effective budget was multiplied by the worker count.
This module is the seam that fixes it without changing a single call site — a limiter still asks one
question, *"is this hit within the budget?"*, and only the answer's storage moves.

## The two rules that shape this design

**A limiter must never be the reason the site is down.** A shared store is a dependency on the hot
path of every sign-in, and an outage of it is far more likely than the abuse it prevents. So
:class:`SharedRateLimitBackend` degrades: any failure talking to Redis falls through to the
in-process window — weaker, but available — and is logged **once per outage**, not once per request,
because a limiter that logs per request during a Redis blip is its own incident.

**Configuration must not change behaviour by accident.** ``memory`` is the default and is byte-for-
byte what the app did before this module existed, so a single-worker development run needs no Redis,
no new service and no new environment variable.

## Why a Redis sorted set

A sliding window needs "how many hits in the last N seconds", which is a range query over
timestamps. A sorted set scored by timestamp gives exactly that, and the whole check is one
round-trip as a pipeline:

    ZREMRANGEBYSCORE key -inf (now - window)   # prune what left the window
    ZADD             key now member            # record this hit
    ZCARD            key                       # how many remain
    EXPIRE           key window                # reclaim an idle key

The member is unique per hit (a counter plus the timestamp) so two hits in the same millisecond are
two members rather than one overwrite — the mistake that would silently under-count a burst, which
is the only traffic shape a rate limiter exists to see.

The pipeline is **not** a transaction. Two workers interleaving here can each observe a count one
higher or lower than a strict serialisation would give; that is an acceptable error of ±1 on a
budget of 10–30, and paying for MULTI/EXEC on every sign-in to remove it is not.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from typing import Protocol

logger = logging.getLogger(__name__)

#: Prefix for every key this application writes, so a shared Redis can host other tenants safely
#: and an operator can see at a glance which keys are ours.
KEY_NAMESPACE = "clinicq:rl:"


class RateLimitBackend(Protocol):
    """One question: record a hit and say how many are in the window.

    Returning the *count* rather than a boolean keeps the budget comparison in one place
    (:class:`~src.core.rate_limit.SlidingWindowRateLimiter`), so a backend can never disagree with
    another about what "over the limit" means.
    """

    def hit(self, key: str, *, window_seconds: float) -> int:
        """Record one hit against ``key`` and return the number of hits now inside the window."""
        ...

    def count(self, key: str, *, window_seconds: float) -> int:
        """Return the hits currently inside ``key``'s window **without recording one**.

        The OTP surface checks its budget before sending the mail and records only after the send
        succeeds, so a provider failure does not spend the caller's allowance. That two-phase shape
        predates this module and is worth keeping, so the protocol carries a read-only peek rather
        than forcing every caller into record-then-decide.
        """
        ...

    def reset(self) -> None:
        """Forget every key (test-isolation helper)."""
        ...


class InProcessRateLimitBackend:
    """Process-local sliding windows — the pre-M30 behaviour, kept verbatim as the default.

    Thread-safe. Timestamps outside the window are pruned on every hit, so an idle key's memory is
    reclaimed the next time that key is seen. ``time.monotonic`` is deliberate: a wall-clock jump
    (NTP, a DST-adjacent container) must not widen or collapse a window.
    """

    def __init__(self) -> None:
        """Initialise an empty backend (no keys tracked yet)."""
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, *, window_seconds: float) -> int:
        """Record a hit and return the window's current occupancy for ``key``."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            recent = [t for t in self._hits.get(key, []) if t > cutoff]
            recent.append(now)
            self._hits[key] = recent
            return len(recent)

    def count(self, key: str, *, window_seconds: float) -> int:
        """Return ``key``'s window occupancy without recording a hit."""
        cutoff = time.monotonic() - window_seconds
        with self._lock:
            recent = [t for t in self._hits.get(key, []) if t > cutoff]
            self._hits[key] = recent
            return len(recent)

    def reset(self) -> None:
        """Clear all tracked keys."""
        with self._lock:
            self._hits.clear()


class SharedRateLimitBackend:
    """A Redis-backed sliding window, degrading to an in-process one when the store is unreachable.

    The fallback is the whole point and is not a fallback *policy* choice made lightly: an
    unreachable store means "count locally and carry on", never "refuse the request". The
    alternative — failing closed — converts a dependency outage into a total sign-in outage, which
    is a strictly worse security outcome than a temporarily per-worker budget.

    The degraded state is logged **once** on entry and once again on recovery, so an operator sees
    the transition in the log rather than one line per request for the duration.
    """

    def __init__(
        self,
        client: object,
        *,
        fallback: InProcessRateLimitBackend | None = None,
    ) -> None:
        """Wrap a Redis client (anything with the four commands used below).

        Args:
            client: A ``redis.Redis``-shaped object. Typed as ``object`` so this module imports
                with no hard dependency on the driver: a deployment that never selects the shared
                backend never needs the package installed.
            fallback: The in-process backend used while the store is unreachable. A fresh one is
                created when omitted.
        """
        self._client = client
        self._fallback = (
            fallback if fallback is not None else InProcessRateLimitBackend()
        )
        self._counter = itertools.count()
        self._degraded = False
        self._lock = threading.Lock()

    @property
    def is_degraded(self) -> bool:
        """Whether the backend is currently counting locally because the store is unreachable."""
        return self._degraded

    def _enter_degraded(self, exc: Exception) -> None:
        """Record the transition into degraded mode, logging only on the edge."""
        with self._lock:
            if self._degraded:
                return
            self._degraded = True
        logger.warning(
            "Rate-limit store unreachable (%s: %s); counting in-process until it recovers. "
            "Budgets are per-worker while degraded.",
            type(exc).__name__,
            exc,
        )

    def _leave_degraded(self) -> None:
        """Record recovery, logging only on the edge."""
        with self._lock:
            if not self._degraded:
                return
            self._degraded = False
        logger.info("Rate-limit store reachable again; counting in the shared window.")

    def hit(self, key: str, *, window_seconds: float) -> int:
        """Record a hit in the shared window, or in the local one if the store is unreachable."""
        namespaced = f"{KEY_NAMESPACE}{key}"
        now = (
            time.time()
        )  # a shared window needs a shared clock, so wall time, not monotonic
        member = f"{now:.6f}:{next(self._counter)}"
        try:
            pipe = self._client.pipeline(transaction=False)  # type: ignore[attr-defined]
            pipe.zremrangebyscore(namespaced, "-inf", now - window_seconds)
            pipe.zadd(namespaced, {member: now})
            pipe.zcard(namespaced)
            # Round up: EXPIRE takes whole seconds and a sub-second window must still outlive its
            # own members, or a key would vanish mid-window and reset the budget.
            pipe.expire(namespaced, max(1, int(window_seconds) + 1))
            results = pipe.execute()
        except Exception as exc:  # any driver/transport failure degrades the same way
            self._enter_degraded(exc)
            return self._fallback.hit(key, window_seconds=window_seconds)
        self._leave_degraded()
        return int(results[2])

    def count(self, key: str, *, window_seconds: float) -> int:
        """Return the shared window's occupancy for ``key`` without recording a hit."""
        namespaced = f"{KEY_NAMESPACE}{key}"
        now = time.time()
        try:
            pipe = self._client.pipeline(transaction=False)  # type: ignore[attr-defined]
            pipe.zremrangebyscore(namespaced, "-inf", now - window_seconds)
            pipe.zcard(namespaced)
            results = pipe.execute()
        except Exception as exc:  # degrade exactly as ``hit`` does
            self._enter_degraded(exc)
            return self._fallback.count(key, window_seconds=window_seconds)
        self._leave_degraded()
        return int(results[1])

    def reset(self) -> None:
        """Clear this application's keys from the store, and the local fallback with them."""
        self._fallback.reset()
        try:
            keys = list(self._client.scan_iter(match=f"{KEY_NAMESPACE}*"))  # type: ignore[attr-defined]
            if keys:
                self._client.delete(*keys)  # type: ignore[attr-defined]
        except Exception:  # best-effort: a dead store has nothing to clear
            logger.debug("Rate-limit store reset skipped: store unreachable.")
