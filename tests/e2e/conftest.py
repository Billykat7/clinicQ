"""Browser tests: a real application server and a real Chromium, driven by Playwright (Issue 55).

Everything above this folder tests the server through ``TestClient`` and reads contexts, never HTML.
What only a browser can show lives here: that a button press changes the page at once, that a page
goes offline and back without a reload, that a keyboard alone can do a job. These fixtures are shared
with every browser suite (the waiting-room board's, Issue 62, reuses them):

* :func:`browser` is one headless Chromium per test module. Without Playwright or its Chromium the
  tests skip with the command that installs them, unless ``REQUIRE_BROWSER_TESTS=1`` (the CI
  ``browser`` shard sets it) turns the skip into a failure, so a missing browser cannot pass as green;
* :func:`serve` runs an application in a background thread on a free local port for as long as a
  ``with`` block lasts, and stops it, open event streams included, when the block ends;
* :func:`router` puts a relay in front of a server that can go silent like a clinic's dead router and
  come back (Issue 62).

A suite builds its own world and app (see ``dashboard/conftest.py``) and hands the app to
:func:`serve`.
"""

from __future__ import annotations

import contextlib
import os
import socket
import struct
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import uvicorn
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

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


#: How many times :func:`empty_tables` tries before giving up on a deadlock.
_TRUNCATE_ATTEMPTS = 5


def empty_tables(session_factory: Any, tables: Iterable[str]) -> None:
    """``TRUNCATE`` a day's tables between browser tests, trying again if PostgreSQL reports a deadlock.

    ``TRUNCATE`` takes an exclusive lock. A stream the previous test's page left open may still be reading
    those tables for a moment after its browser closed, and PostgreSQL can resolve that by cancelling the
    ``TRUNCATE`` as a deadlock. That is a test-ordering race, not a failure, so it is retried after a
    short pause.
    """
    statement = text(
        "TRUNCATE " + ", ".join(f"clinicq.{name}" for name in tables) + " CASCADE"
    )
    for attempt in range(1, _TRUNCATE_ATTEMPTS + 1):
        with session_factory() as db:
            try:
                db.execute(statement)
                db.commit()
                return
            except OperationalError as exc:
                db.rollback()
                if "deadlock" not in str(exc).lower() or attempt == _TRUNCATE_ATTEMPTS:
                    raise
        time.sleep(0.2 * attempt)


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


class Router:
    """A local TCP relay between the browser and a test server that can fail like a clinic's router (Issue 62).

    Chromium's offline switch refuses new requests but leaves an open event stream flowing, which is not
    what a dead router does. Here :meth:`cut` makes the link silent both ways: bytes on open connections
    are dropped, new connections are accepted and never answered, and nothing reports an error, which is
    the hardest case for a page to notice. :meth:`restore` forwards again and resets every connection that
    was held during the cut, as a router coming back does to connections it forgot.
    """

    def __init__(self, target: str) -> None:
        host, port = target.removeprefix("http://").split(":")
        self._target = (host, int(port))
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.base_url = f"http://127.0.0.1:{self._listener.getsockname()[1]}"
        self._up = threading.Event()
        self._up.set()
        self._lock = threading.Lock()
        self._held: list[socket.socket] = []
        self._closed = False
        threading.Thread(target=self._accept, name="e2e-router", daemon=True).start()

    def cut(self) -> None:
        """Drop everything, answer nothing."""
        self._up.clear()

    def restore(self) -> None:
        """Forward again, and reset whatever was held while the link was down."""
        with self._lock:
            held, self._held = self._held, []
        for sock in held:
            with _quietly():
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
                sock.close()
        self._up.set()

    def close(self) -> None:
        """Stop relaying."""
        self._closed = True
        self._up.set()
        with _quietly():
            self._listener.close()

    def _accept(self) -> None:
        while not self._closed:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            if not self._up.is_set():
                with self._lock:
                    self._held.append(client)
                continue
            try:
                upstream = socket.create_connection(self._target)
            except OSError:
                client.close()
                continue
            with self._lock:
                self._held.extend([client, upstream])
            for source, sink in ((client, upstream), (upstream, client)):
                threading.Thread(
                    target=self._pump, args=(source, sink), daemon=True
                ).start()

    def _pump(self, source: socket.socket, sink: socket.socket) -> None:
        while True:
            try:
                data = source.recv(65536)
            except OSError:
                break
            if not data:
                break
            if not self._up.is_set():
                continue  # the router has gone: the bytes vanish
            try:
                sink.sendall(data)
            except OSError:
                break
        for sock in (source, sink):
            with _quietly():
                sock.close()
            with self._lock:
                if sock in self._held:
                    self._held.remove(sock)


def _quietly() -> contextlib.suppress:
    return contextlib.suppress(OSError)


@contextmanager
def router(target: str) -> Iterator[Router]:
    """A :class:`Router` in front of ``target`` for as long as the ``with`` block lasts."""
    link = Router(target)
    try:
        yield link
    finally:
        link.close()
