"""Browser tests: a real application server and a real Chromium, driven by Playwright (Issue 55).

Everything above this folder tests the server through ``TestClient`` and reads contexts, never HTML.
What only a browser can show lives here: that a button press changes the page at once, that a page
goes offline and back without a reload, that a keyboard alone can do a job. These fixtures are shared
with every browser suite (the waiting-room board's, Issue 62, reuses them):

* :func:`browser` is one headless Chromium per test module. Without Playwright or its Chromium the
  tests skip with the command that installs them, unless ``REQUIRE_BROWSER_TESTS=1`` (the CI
  ``browser`` shard sets it) turns the skip into a failure, so a missing browser cannot pass as green;
* :func:`serve` runs an application in a background thread on a free local port for as long as a
  ``with`` block lasts, and stops it, open event streams included, when the block ends.

A suite builds its own world and app (see ``dashboard/conftest.py``) and hands the app to
:func:`serve`.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import uvicorn

#: How long a server may take to accept connections before the fixture gives up.
_STARTUP_SECONDS = 20


def _browsers_unavailable(reason: str) -> None:
    """Skip, or fail when the run requires browser tests."""
    if os.environ.get("REQUIRE_BROWSER_TESTS") == "1":
        pytest.fail(f"Browser tests are required here: {reason}")
    pytest.skip(f"{reason}. Install it with: python -m playwright install chromium")


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    """One headless Chromium for a test module, closed when the module ends.

    Module-scoped, not session-scoped, on purpose. Playwright's synchronous API keeps an asyncio loop
    running for as long as it is open, and ``pytest -n auto`` hands the same worker other folders'
    tests after a browser module. A test there that calls ``asyncio.run()`` would then fail with "cannot
    be called from a running event loop". Closing Playwright with its module costs about a second per
    module.
    """
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:  # pragma: no cover - playwright is in requirements.txt
        _browsers_unavailable("Playwright is not installed")
    with sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Error as exc:
            _browsers_unavailable(
                f"Chromium for Playwright is not installed ({exc.message.splitlines()[0]})"
            )
        yield chromium
        chromium.close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def serve(app: Any, port: int | None = None) -> Iterator[str]:
    """Run ``app`` on ``127.0.0.1`` in a background thread; yield its base URL; stop it afterwards.

    Server-sent event streams stay open until the browser goes, so shutdown waits at most a second for
    them rather than for the client. ``port`` reopens a server where an earlier one was, which is how a
    test restarts the server under a page that is still open (Issue 57).
    """
    port = port or _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=1,
        )
    )
    thread = threading.Thread(target=server.run, name=f"e2e-server-{port}", daemon=True)
    thread.start()
    deadline = time.monotonic() + _STARTUP_SECONDS
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError(f"the test server on port {port} did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
