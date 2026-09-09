"""Phase 0 benchmark: quantify the event-loop stall a blocking call causes (Issue #81).

The whole point of Issue #81 is that synchronous, blocking work performed *inside* an ``async def``
handler holds the single asyncio event loop for the entire round-trip, so every other in-flight
request on that worker — including a cheap health probe — is stalled until it returns. This benchmark
makes that provable rather than assumed: it saturates one endpoint with concurrent slow requests and,
while it is saturated, samples the latency of a cheap ``/health/live`` probe.

Two handler shapes are compared under identical load:

* ``/_bench/blocking`` — an ``async def`` that sleeps **inline** (``time.sleep``). This is the
  anti-pattern: the sleep stands in for a synchronous DB/S3/SMTP round-trip run on the loop.
* ``/_bench/offloaded`` — the same wait moved off the loop with ``asyncio.to_thread`` — the shape the
  async DB session (``await AsyncSession.execute``) and the existing ``asyncio.to_thread`` external-I/O
  wraps both give the request path.

The assertions encode the acceptance criterion: while the slow endpoint is saturated, the blocking
shape stalls the probe for roughly the slow handler's duration, while the offloaded shape keeps the
probe well under it. The measured numbers are printed so the before/after is a record, not folklore.

Kept deliberately self-contained (its own tiny routes, no DB) so it measures loop behaviour alone and
runs fast and deterministically in CI.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from httpx import ASGITransport
from starlette import status

from src.main import create_app

# The slow handler's duration, the concurrent load, and how many probes we sample while saturated.
_SLOW_SECONDS = 0.4
_CONCURRENCY = 8
_PROBE_SAMPLES = 15
# A probe slower than this fraction of the slow handler counts as "the loop was stalled".
_STALL_FRACTION = 0.5


def _percentile(samples: list[float], pct: float) -> float:
    """Return the ``pct`` (0-100) percentile of ``samples`` by nearest-rank on a sorted copy."""
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    rank = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered)) - 1))
    return ordered[rank]


def _app_with_bench_routes():
    """Return an app instance with a blocking and an offloaded slow route added for the benchmark."""
    app = create_app()

    async def blocking() -> dict[str, str]:
        """Anti-pattern: block the event loop inline for the whole wait."""
        time.sleep(_SLOW_SECONDS)  # noqa: ASYNC251 — the anti-pattern this benchmark measures
        return {"mode": "blocking"}

    async def offloaded() -> dict[str, str]:
        """Fix: move the wait off the loop, exactly as ``await AsyncSession.execute`` does."""
        await asyncio.to_thread(time.sleep, _SLOW_SECONDS)
        return {"mode": "offloaded"}

    app.add_api_route("/_bench/blocking", blocking, methods=["GET"])
    app.add_api_route("/_bench/offloaded", offloaded, methods=["GET"])
    return app


async def _probe_latencies_under_load(app, slow_path: str) -> list[float]:
    """Saturate ``slow_path`` with concurrent requests and return /health/live probe latencies.

    The probes are launched *concurrently* with the load and each latency is measured from a single
    ``t0`` captured before anything is scheduled — so a probe that the frozen loop cannot even begin
    to serve records the full wait, not just its own fast round-trip once the loop frees.
    """
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bench") as ac:

        async def timed_probe() -> float:
            resp = await ac.get("/health/live")
            assert resp.status_code == status.HTTP_200_OK
            return time.perf_counter() - t0

        t0 = time.perf_counter()
        slow = [asyncio.create_task(ac.get(slow_path)) for _ in range(_CONCURRENCY)]
        probes = [asyncio.create_task(timed_probe()) for _ in range(_PROBE_SAMPLES)]

        latencies = list(await asyncio.gather(*probes))
        await asyncio.gather(*slow)
        return latencies


def test_blocking_handler_stalls_the_health_probe() -> None:
    """The *before* case: an inline-blocking async handler stalls the cheap health probe."""
    app = _app_with_bench_routes()
    latencies = asyncio.run(_probe_latencies_under_load(app, "/_bench/blocking"))

    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    print(  # noqa: T201
        f"\n[stall benchmark] BLOCKING  p95={p95 * 1000:.1f}ms p99={p99 * 1000:.1f}ms "
        f"(slow handler={_SLOW_SECONDS * 1000:.0f}ms, concurrency={_CONCURRENCY})"
    )

    # With the loop frozen by inline sleeps, the probe cannot be served promptly.
    assert p99 >= _SLOW_SECONDS * _STALL_FRACTION, (
        "expected the blocking handler to stall the health probe, but the probe stayed fast"
    )


@pytest.mark.parametrize("_run", range(1))
def test_offload_is_dramatically_faster_than_blocking(_run: int) -> None:
    """Head-to-head: the offloaded probe p99 is a small fraction of the blocking probe p99."""
    app = _app_with_bench_routes()
    blocking = asyncio.run(_probe_latencies_under_load(app, "/_bench/blocking"))
    offloaded = asyncio.run(_probe_latencies_under_load(app, "/_bench/offloaded"))

    blocking_p99 = _percentile(blocking, 99)
    offloaded_p99 = _percentile(offloaded, 99)
    print(  # noqa: T201
        f"\n[stall benchmark] improvement: blocking p99={blocking_p99 * 1000:.1f}ms -> "
        f"offloaded p99={offloaded_p99 * 1000:.1f}ms"
    )

    assert offloaded_p99 < blocking_p99 / 2, (
        "offloading the slow work should cut the health-probe tail latency by at least half"
    )
