"""Rate limits that hold across workers, and never take the site down (Issue #179, M30).

Pen-test findings **F-02** (per-worker windows) and **F-04** (per-IP budget keyed on the reverse
proxy) are the same bug seen from two sides: a budget that does not mean what it says. This suite
asserts the three properties that make it mean something.

1. **The shared window is genuinely shared.** Two `SlidingWindowRateLimiter` instances — the stand-in
   for two Uvicorn workers, since each worker holds its own module-global object — pointed at one
   store spend one budget between them. A fake Redis is used rather than a live server: the property
   under test is *"both limiters count into the same place"*, which a real server would demonstrate
   no more clearly while making the suite need a container.

2. **An unreachable store degrades, it does not deny.** Driven with a backend that raises on every
   call. A limiter that answers 429 because Redis blinked has converted a dependency outage into a
   sign-in outage, which is a worse security outcome than the abuse it was preventing. The
   degradation is logged **once**, not once per request.

3. **A single-worker run with no shared store behaves exactly as it does today.** Asserted against
   `Settings` defaults, so the "nothing changes unless you configure it" claim is a test rather than
   a sentence in a PR description.

The proxy half — that two clients arriving through one proxy land in different buckets — is asserted
end-to-end against the real password-login route, because that is the route whose per-IP budget was
a single bucket for the entire internet.
"""

# ── kernel half ──────────────────────────────────────────────────────────────────────────
# The business-logic tests this file carried in the source project are gone: they asserted
# against records the kernel does not have. What is left tests the kernel's own behaviour.

import logging
from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette import status

from src.commons.enums import AppEnvironment, RateLimitBackendKind, UserRole
from src.core import security
from src.core.config import Settings, get_settings
from src.core.rate_limit import SlidingWindowRateLimiter, _build_backend
from src.core.rate_limit_backend import (
    InProcessRateLimitBackend,
    SharedRateLimitBackend,
)
from src.core.security import hash_password
from src.database.models import Base, User
from src.database.schema import sqlite_schema_translate_map
from src.database.session import get_db
from src.main import create_app

_TEST_JWT_SECRET = "issue-179-rate-limit-backend-secret-32ch"


_PASSWORD = "Correct-Horse-Battery-9"


_USER_EMAIL = "limited.user@example.com"


class _FakePipeline:
    """Collects the four sorted-set commands and applies them on ``execute``."""

    def __init__(self, store: dict[str, dict[str, float]]) -> None:
        self._store = store
        self._ops: list[tuple[str, tuple[object, ...]]] = []

    def zremrangebyscore(self, key: str, _min: object, maximum: float) -> None:
        self._ops.append(("prune", (key, maximum)))

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self._ops.append(("add", (key, mapping)))

    def zcard(self, key: str) -> None:
        self._ops.append(("card", (key,)))

    def expire(self, key: str, seconds: int) -> None:
        self._ops.append(("expire", (key, seconds)))

    def execute(self) -> list[object]:
        """Apply the queued commands in order and return one result per command."""
        results: list[object] = []
        for op, args in self._ops:
            match op:
                case "prune":
                    key, maximum = args
                    members = self._store.setdefault(str(key), {})
                    for member, score in list(members.items()):
                        if score <= float(maximum):  # type: ignore[arg-type]
                            del members[member]
                    results.append(0)
                case "add":
                    key, mapping = args
                    self._store.setdefault(str(key), {}).update(mapping)  # type: ignore[arg-type]
                    results.append(1)
                case "card":
                    results.append(len(self._store.get(str(args[0]), {})))
                case _:
                    results.append(True)
        return results


class FakeRedis:
    """The four commands ``SharedRateLimitBackend`` uses, over a plain dict."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, float]] = {}

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        """Return a pipeline over the shared dict."""
        return _FakePipeline(self.store)

    def scan_iter(self, match: str) -> list[str]:
        """Return every key (the prefix is the only pattern this app uses)."""
        prefix = match.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def delete(self, *keys: str) -> None:
        """Drop the named keys."""
        for key in keys:
            self.store.pop(key, None)


class StoreUnreachableError(RuntimeError):
    """What a dead Redis connection raises, in the shape the backend must survive."""


class BrokenRedis:
    """A store that is reachable in configuration and unreachable in fact."""

    def pipeline(self, transaction: bool = True) -> object:
        """Fail the way a dead connection fails: at command time."""
        raise StoreUnreachableError("connection refused")

    def scan_iter(self, match: str) -> list[str]:
        """Fail the same way."""
        raise StoreUnreachableError("connection refused")


def _limiter_on(backend: object) -> SlidingWindowRateLimiter:
    """A limiter bound to a specific backend, standing in for one worker process."""
    limiter = SlidingWindowRateLimiter()
    limiter.set_backend(backend)  # type: ignore[arg-type]
    return limiter


def test_two_workers_share_one_budget() -> None:
    """The F-02 property: the budget is not multiplied by the worker count.

    Two limiter objects — what two Uvicorn workers each hold — pointed at one store. A budget of 3
    must be spent by *three* hits in total, not three per worker.
    """
    store = FakeRedis()
    worker_a = _limiter_on(SharedRateLimitBackend(store))
    worker_b = _limiter_on(SharedRateLimitBackend(store))

    assert worker_a.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)
    assert worker_b.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)
    assert worker_a.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)
    # Fourth hit overall — refused on whichever worker happens to receive it.
    assert not worker_b.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)


def test_two_in_process_limiters_do_not_share_a_budget() -> None:
    """The bug, stated as a test, so the fix above is measured against something.

    This is not a regression guard — it is the documented weakness of the ``memory`` backend, and
    the reason a multi-worker deployment must configure the shared one.
    """
    worker_a = _limiter_on(InProcessRateLimitBackend())
    worker_b = _limiter_on(InProcessRateLimitBackend())
    for _ in range(3):
        worker_a.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)
    # Worker B's window is empty, so the same caller gets a whole second budget.
    assert worker_b.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)


def test_distinct_keys_get_distinct_budgets_in_the_shared_store() -> None:
    """Isolation is the point of a per-IP budget: one attacker must not spend everyone's."""
    store = FakeRedis()
    limiter = _limiter_on(SharedRateLimitBackend(store))
    for _ in range(3):
        limiter.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)
    assert not limiter.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)
    assert limiter.check_and_record("ip:198.51.100.4", limit=3, window_seconds=60)


def test_hits_in_the_same_instant_are_counted_separately() -> None:
    """A burst is the only traffic shape a limiter exists to see; it must not collapse to one hit."""
    store = FakeRedis()
    limiter = _limiter_on(SharedRateLimitBackend(store))
    for _ in range(3):
        limiter.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)
    key = next(iter(store.store))
    assert len(store.store[key]) == 3


def test_the_peek_does_not_spend_the_budget() -> None:
    """The OTP surface checks before sending and records after — a failed send costs nothing."""
    store = FakeRedis()
    limiter = _limiter_on(SharedRateLimitBackend(store))
    for _ in range(10):
        assert limiter.within_limit("otp:email:a@b.com", limit=1, window_seconds=60)
    limiter.record("otp:email:a@b.com", window_seconds=60)
    assert not limiter.within_limit("otp:email:a@b.com", limit=1, window_seconds=60)


def test_an_unreachable_store_still_allows_traffic() -> None:
    """The rule this design turns on: a limiter must never be the reason the site is down."""
    backend = SharedRateLimitBackend(BrokenRedis())
    limiter = _limiter_on(backend)
    assert limiter.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)
    assert backend.is_degraded


def test_the_in_process_window_still_limits_while_degraded() -> None:
    """Degraded is weaker, not absent: the fallback keeps counting, per worker."""
    limiter = _limiter_on(SharedRateLimitBackend(BrokenRedis()))
    for _ in range(3):
        assert limiter.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)
    assert not limiter.check_and_record("ip:203.0.113.7", limit=3, window_seconds=60)


def test_the_outage_is_logged_once_not_once_per_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A limiter that logs per request during a Redis blip is its own incident."""
    limiter = _limiter_on(SharedRateLimitBackend(BrokenRedis()))
    with caplog.at_level(logging.WARNING, logger="src.core.rate_limit_backend"):
        for _ in range(25):
            limiter.check_and_record("ip:203.0.113.7", limit=100, window_seconds=60)
    degraded_lines = [r for r in caplog.records if "unreachable" in r.getMessage()]
    assert len(degraded_lines) == 1


def test_recovery_is_logged_once_and_the_shared_window_resumes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The transition back is visible too, so an operator can see the outage's end."""
    store = FakeRedis()
    backend = SharedRateLimitBackend(BrokenRedis())
    limiter = _limiter_on(backend)
    limiter.check_and_record("ip:203.0.113.7", limit=100, window_seconds=60)
    assert backend.is_degraded

    backend._client = store
    with caplog.at_level(logging.INFO, logger="src.core.rate_limit_backend"):
        limiter.check_and_record("ip:203.0.113.7", limit=100, window_seconds=60)
    assert not backend.is_degraded
    assert sum("reachable again" in r.getMessage() for r in caplog.records) == 1


def _settings(**overrides: object) -> Settings:
    """Isolated settings (no ``.env``) with the rate-limit knobs set explicitly."""
    return Settings(  # type: ignore[arg-type]
        _env_file=None,
        environment=AppEnvironment.DEVELOPMENT,
        jwt_secret=_TEST_JWT_SECRET,
        auth_enabled=True,
        auth_password_login_enabled=True,
        smtp_host="",
        **overrides,
    )


def test_the_default_backend_is_in_process() -> None:
    """A single-worker dev run needs no store, no service and no new environment variable."""
    assert _settings().rate_limit_backend is RateLimitBackendKind.MEMORY
    assert isinstance(_build_backend(_settings()), InProcessRateLimitBackend)


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("no url configured", {"rate_limit_backend": RateLimitBackendKind.REDIS}),
        (
            "unparseable url",
            {
                "rate_limit_backend": RateLimitBackendKind.REDIS,
                "rate_limit_redis_url": "not-a-url",
            },
        ),
    ],
)
def test_a_misconfigured_shared_store_downgrades_instead_of_refusing_to_boot(
    label: str, overrides: dict[str, object]
) -> None:
    """The limiter is a safety belt; refusing to start because its preferred storage is missing
    trades a small loss of strength for a total loss of service."""
    assert isinstance(
        _build_backend(_settings(**overrides)), InProcessRateLimitBackend
    ), label


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    """A ``TestClient`` with proxy trust ON and a per-IP password budget of one attempt."""
    settings = _settings(
        trust_proxy_headers=True,
        trusted_proxy_hops=1,
        password_login_rate_limit_per_ip=1,
        password_login_rate_limit_per_email=50,
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    ).execution_options(schema_translate_map=sqlite_schema_translate_map())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def _override_get_db() -> Generator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    with factory() as db:
        db.add(
            User(
                email=_USER_EMAIL,
                password=hash_password(_PASSWORD),
                role=UserRole.USER.value,
                is_verified=True,
            )
        )
        db.commit()

    monkeypatch.setattr(security, "get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_settings] = lambda: settings
    yield SimpleNamespace(client=TestClient(app), settings=settings)
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _login(ctx: SimpleNamespace, forwarded: str) -> int:
    """Attempt a password sign-in as a client arriving through the proxy from ``forwarded``."""
    response = ctx.client.post(
        "/api/v1/auth/password/login",
        json={"email": _USER_EMAIL, "password": "wrong-password"},
        headers={"X-Forwarded-For": forwarded},
    )
    return response.status_code


def test_a_forged_forwarded_prefix_cannot_mint_a_fresh_bucket(
    ctx: SimpleNamespace,
) -> None:
    """Reading the header's leftmost element would give an attacker unlimited buckets.

    Each request below forges a different left-hand value while arriving from one real client. The
    limiter must key on what the proxy appended, so the budget still fills.
    """
    assert _login(ctx, "attacker-bucket-1, 203.0.113.7") == status.HTTP_401_UNAUTHORIZED
    assert (
        _login(ctx, "attacker-bucket-2, 203.0.113.7")
        == status.HTTP_429_TOO_MANY_REQUESTS
    )
