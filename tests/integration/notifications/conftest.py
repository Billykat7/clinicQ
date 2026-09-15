"""Fixtures for the notification integration tests.

``desk`` is the queue engine's fixture (two clinics, their queues and receptionists), reused so a
patient notification is exercised against the real queue moves that cause it (Issue 63).

``no_provider_is_reached`` (Issue 71) runs for every test here: an HTTP request from the application to any
host but this machine fails the test, naming the host. Every gateway and push service is answered by a
fake (``httpx.MockTransport``, a local server on 127.0.0.1, ``NoopTransport``), so the suite needs no
provider credentials and cannot spend money or message anyone, and a test that forgets its fake says so
instead of quietly calling a real sandbox.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from tests.integration.queue.conftest import desk

__all__ = ["desk", "no_provider_is_reached"]

#: Hosts a test may reach: this machine, where the local push service and test servers listen.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@pytest.fixture(autouse=True)
def no_provider_is_reached(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail a test whose code sends a real HTTP request anywhere but this machine."""
    real = httpx.HTTPTransport.handle_request

    def guarded(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        if request.url.host not in _LOCAL_HOSTS:
            raise AssertionError(
                f"a notification test tried to reach {request.url.host}: give it a fake gateway "
                "(httpx.MockTransport, use_transports) instead of a real provider"
            )
        return real(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded)
    yield
