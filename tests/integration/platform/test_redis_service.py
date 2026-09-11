"""Redis as a running server, not a fake (Issue 9).

CI starts a real Redis 8 beside the tests, the image the dev stack pins. Everything else in the
suite talks to a stand-in, on purpose, so these are the checks that only mean something against the
real thing:

1. readiness's Redis probe gets its ``PONG`` from a server (the other probe tests fake the client);
2. two rate limiters, the stand-in for two workers, spend **one** budget in the real store; the
   property ``test_rate_limit_backends.py`` proves against a fake, here on the wire, in the real
   sorted-set commands.

Marked ``redis``: skipped without ``TEST_REDIS_URL``, and a failure with ``REQUIRE_REDIS_TESTS=1``.
"""

from collections.abc import Generator

import pytest
import redis

from src.commons.enums import AppEnvironment, DependencyStatus, RateLimitBackendKind
from src.commons.ids import new_id
from src.core import health
from src.core.config import Settings
from src.core.rate_limit import SlidingWindowRateLimiter, _build_backend
from src.core.rate_limit_backend import KEY_NAMESPACE, SharedRateLimitBackend

pytestmark = pytest.mark.redis

#: A budget small enough to spend in a few calls.
BUDGET = 3

#: Long enough that no hit in a test can age out of the window mid-test.
WINDOW_SECONDS = 60.0


def _settings(url: str) -> Settings:
    """Settings pointing readiness and the rate limiter at ``url``, isolated from ``.env``."""
    return Settings(
        _env_file=None,
        environment=AppEnvironment.DEVELOPMENT,
        redis_url=url,
        rate_limit_backend=RateLimitBackendKind.REDIS,
        rate_limit_redis_url=url,
    )


@pytest.fixture
def bucket(redis_server_url: str) -> Generator[str]:
    """A rate-limit key no other test uses, deleted from the server afterwards."""
    key = f"issue-9:{new_id()}"
    yield key
    client = redis.Redis.from_url(redis_server_url)
    try:
        client.delete(f"{KEY_NAMESPACE}{key}")
    finally:
        client.close()


def test_the_readiness_probe_gets_pong_from_a_real_server(
    redis_server_url: str,
) -> None:
    """``/health/ready`` reports Redis ``ok`` only because a server answered."""
    assert health.probe_redis(_settings(redis_server_url)) is DependencyStatus.OK


def test_two_workers_spend_one_budget_in_the_real_store(
    redis_server_url: str, bucket: str
) -> None:
    """Hits from two limiters land in one server-side window; the next hit from either is refused."""
    settings = _settings(redis_server_url)
    workers = [
        SlidingWindowRateLimiter(lambda: _build_backend(settings)) for _ in range(2)
    ]
    assert all(isinstance(w.backend, SharedRateLimitBackend) for w in workers)

    allowed = [
        workers[i % 2].check_and_record(
            bucket, limit=BUDGET, window_seconds=WINDOW_SECONDS
        )
        for i in range(BUDGET)
    ]
    assert allowed == [True] * BUDGET
    assert not workers[0].check_and_record(
        bucket, limit=BUDGET, window_seconds=WINDOW_SECONDS
    )
    assert not workers[1].within_limit(
        bucket, limit=BUDGET, window_seconds=WINDOW_SECONDS
    )
    assert not any(w.backend.is_degraded for w in workers)  # type: ignore[attr-defined]
