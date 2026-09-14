"""The waiting-room board through load-shedding, a dead router and a restart, in Chromium (Issue 62).

A kiosk box here is a paired browser context on the real server and PostgreSQL. Its network fails the way a
clinic's does: through :class:`~tests.e2e.conftest.Router`, a relay that goes silent both ways with no error
(a dead router), or with no network at all when it boots (Chromium's offline switch). A slow link is the
DevTools network profile, and the box's "power" is the browser itself: a persistent profile closed and
opened again, as the box's disk keeps it.

* **Offline, it shows the last known board and says how old it is.** The numbers stay, and a banner
  replaces the health notice: "Not up to date. Last updated at HH:MM", in the clinic's time.
* **Back within 30 seconds.** After the network has been gone long enough for the page's reconnection to
  back off to its ceiling, the board is live again, with a call made during the outage, within 30 seconds
  of the network returning, and nobody touches it.
* **A server restart** is weathered the same way, with no reload.
* **A slow link** (1.5 s each way, 200 kbit/s) still gets each call onto the board and stays live.
* **A power cut with no network at boot.** The box's browser is closed and started again from its profile
  with no network: the board page comes from the service worker's cache, with the last numbers and the
  banner. When the network returns, it is live on its own.
* **A removed box forgets.** Once a box shows its pairing code, a reboot with no network shows no board.
* **A day offline** on the page's clock leaves the heap, the listeners and the layout where they were:
  reconnection attempts and polls every few seconds do not pile up. An hour in CI;
  ``BOARD_OFFLINE_SOAK_HOURS=8`` runs the eight hours.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import update

from src.commons.time import now_sast
from src.database.models import DisplayDevice
from src.web import display_stream
from tests.e2e.conftest import router, serve

pytestmark = pytest.mark.postgres

#: The criterion: live again within this many seconds of the network returning.
RECOVERY_BUDGET_SECONDS = 30
#: How long the offline soak runs on the page's clock. An hour in CI, where a failed request every few
#: seconds costs real time; ``BOARD_OFFLINE_SOAK_HOURS=8`` runs the whole day (about eight minutes).
OFFLINE_SOAK_HOURS = float(os.environ.get("BOARD_OFFLINE_SOAK_HOURS", "1"))
#: A folder to save the pull request's screenshots in, when set.
SHOTS = os.environ.get("BOARD_RESILIENCE_SHOTS")
SAST = ZoneInfo("Africa/Johannesburg")
_LIVE = (
    "() => window.ClinicQBoardLive && window.ClinicQBoardLive.state().state === 'live'"
)
_STALE = "() => document.querySelector('.kiosk').getAttribute('data-stale')"
_BANNER = "() => { const b = document.querySelector('[data-board-stale]'); return b.hidden ? null : b.textContent; }"
_SERVING = "() => [...document.querySelectorAll('.serving-number')].map((el) => el.textContent)"
_CURRENT = "() => !document.querySelector('.kiosk').hasAttribute('data-stale')"
_SHOWS = "(number) => [...document.querySelectorAll('.serving-number')].some((el) => el.textContent === number)"


def _until(
    page: Any, predicate: str, argument: Any = None, timeout: float = 60.0
) -> float:
    """Wait until ``predicate`` is true in the page; seconds it took."""
    started = time.monotonic()
    while True:
        try:
            if page.evaluate(predicate, argument):
                return time.monotonic() - started
        except Exception:  # the page is navigating: ask again
            pass
        if time.monotonic() - started > timeout:
            raise AssertionError(f"timed out waiting for {predicate} ({argument!r})")
        time.sleep(0.1)


def _live_board(day: SimpleNamespace, page: Any | None = None) -> Any:
    """A paired box's board, live, with its service worker in charge of the page."""
    page = page or day.new_page(1280, 720)
    page.goto("/display")
    page.wait_for_selector(".panel")
    _until(page, _LIVE)
    _until(
        page, "() => !!navigator.serviceWorker.controller || (location.reload(), false)"
    )
    page.wait_for_selector(".panel")
    _until(page, _LIVE)
    return page


def _shot(page: Any, name: str) -> None:
    """Save a screenshot for the pull request when ``BOARD_RESILIENCE_SHOTS`` names a folder."""
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def _clinic_time(epoch_ms: float) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, SAST).strftime("%H:%M")


def test_an_offline_board_keeps_its_numbers_and_says_when_it_was_last_updated(
    board_day: SimpleNamespace,
) -> None:
    """The router dies: the same numbers stay, and a banner gives the time of the last update, the clinic's."""
    triage, consult = board_day.open_queues(2)
    board_day.issue(triage, 3)
    board_day.issue(consult, 3)
    board_day.call(triage)
    board_day.call(consult)
    with router(board_day.clinic.base_url) as link:
        page = _live_board(
            board_day, board_day.new_page(1280, 720, base_url=link.base_url)
        )
        _until(page, "() => document.querySelectorAll('.serving-number').length === 2")
        before = page.evaluate(_SERVING)
        assert page.evaluate(_BANNER) is None

        link.cut()
        went = time.monotonic()
        _until(page, _STALE, timeout=60)
        noticed = time.monotonic() - went

        last_good = page.evaluate("() => window.ClinicQBoardOffline.state().lastGood")
        banner = page.evaluate(_BANNER)
        assert banner == f"Not up to date. Last updated at {_clinic_time(last_good)}."
        assert page.evaluate(_SERVING) == before
        assert page.locator("[data-board-ticker]").is_hidden()
        _shot(page, "board-router-dead-stale-banner")
        message = f"router dead: banner {banner!r} after {noticed:.1f} s, numbers still {before}"
        print("\n" + message)  # noqa: T201 — quoted in the PR


def test_the_board_is_live_again_within_30_seconds_of_the_network_returning(
    board_day: SimpleNamespace,
) -> None:
    """Dead long enough for the backoff to reach its ceiling; a call made meanwhile shows after it returns."""
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 4)
    with router(board_day.clinic.base_url) as link:
        page = _live_board(
            board_day, board_day.new_page(1280, 720, base_url=link.base_url)
        )

        link.cut()
        cut_at = time.monotonic()
        _until(page, _STALE)
        _until(page, "() => window.ClinicQBoardLive.state().attempts >= 3", timeout=240)
        outage = time.monotonic() - cut_at
        during = board_day.call(triage)
        page.evaluate("() => { window.__notReloaded = true; }")

        link.restore()
        returned = time.monotonic()
        _until(page, _LIVE, timeout=RECOVERY_BUDGET_SECONDS + 5)
        live_after = time.monotonic() - returned
        _until(page, _SHOWS, during, timeout=5)
        _until(page, _CURRENT, timeout=5)
        current_after = time.monotonic() - returned

        assert live_after <= RECOVERY_BUDGET_SECONDS
        assert current_after <= RECOVERY_BUDGET_SECONDS
        assert page.evaluate("() => window.__notReloaded") is True
        message = (
            f"router back after {outage:.0f} s dead: live after {live_after:.1f} s, the outage's call "
            f"{during} shown and the banner gone after {current_after:.1f} s"
        )
        print("\n" + message)  # noqa: T201 — quoted in the PR


def test_a_server_restart_is_weathered_with_the_banner_and_no_reload(
    board_day: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server of its own, stopped for longer than the stale time and started again on the same port."""
    monkeypatch.setattr(display_stream, "BOARD_HEARTBEAT_SECONDS", 0.5)
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 3)
    with serve(board_day.clinic.app) as base_url:
        port = int(base_url.rsplit(":", 1)[1])
        page = _live_board(board_day, board_day.new_page(1280, 720, base_url=base_url))
        page.evaluate("() => { window.__notReloaded = true; }")
    # Stopped.
    _until(page, _STALE, timeout=60)
    during = board_day.call(triage)
    with serve(board_day.clinic.app, port=port):
        restarted = time.monotonic()
        _until(page, _SHOWS, during, timeout=RECOVERY_BUDGET_SECONDS + 5)
        _until(
            page,
            "() => !document.querySelector('.kiosk').hasAttribute('data-stale')",
            timeout=5,
        )
        took = time.monotonic() - restarted
        assert took <= RECOVERY_BUDGET_SECONDS
        assert page.evaluate("() => window.__notReloaded") is True
    print(f"\nserver back: current again after {took:.1f} s")  # noqa: T201 — quoted in the PR


def test_a_slow_link_still_brings_every_call_and_stays_live(
    board_day: SimpleNamespace,
) -> None:
    """1.5 s of latency each way and 200 kbit/s: a board loads, stays live, and shows each of three calls."""
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 4)
    page = board_day.new_page(1280, 720)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send(
        "Network.emulateNetworkConditions",
        {
            "offline": False,
            "latency": 1500,
            "downloadThroughput": 25_000,
            "uploadThroughput": 25_000,
        },
    )
    started = time.monotonic()
    page.goto("/display", timeout=60_000)
    page.wait_for_selector(".panel", timeout=60_000)
    _until(page, _LIVE)
    loaded = time.monotonic() - started

    timings = []
    for _ in range(3):
        called = time.monotonic()
        number = board_day.call(triage, finish_previous=True)
        _until(page, _SHOWS, number, timeout=20)
        timings.append(time.monotonic() - called)
        time.sleep(1)
    assert page.evaluate(_LIVE)
    assert page.evaluate(_BANNER) is None
    assert max(timings) < 10
    print(  # noqa: T201 — quoted in the PR
        f"\nslow link: board loaded and live in {loaded:.1f} s; calls on screen after "
        + ", ".join(f"{t:.1f}" for t in timings)
        + " s"
    )


@pytest.fixture
def box_profile(tmp_path: Path) -> Iterator[Path]:
    """A kiosk box's browser profile on disk: cookies, storage and the service worker's cache."""
    yield tmp_path / "box"


def _boot(day: SimpleNamespace, profile: Path, *, offline: bool) -> Any:
    """Start the box's browser from its profile, as after a power cut, and open the start address."""
    context = day.clinic.browser.browser_type.launch_persistent_context(
        str(profile),
        base_url=day.clinic.base_url,
        viewport={"width": 1280, "height": 720},
        offline=offline,
    )
    day.track(context)
    return context


def test_after_a_power_cut_with_no_network_the_box_shows_its_last_board_and_recovers(
    board_day: SimpleNamespace, box_profile: Path
) -> None:
    """Boot offline: the shell and the last numbers from the box's own cache, the banner; then live on its own."""
    triage, *_ = board_day.open_queues(1)
    board_day.issue(triage, 4)
    first = _boot(board_day, box_profile, offline=False)
    board_day.pair_context(first, board_day.clinic.base_url)
    page = _live_board(board_day, first.pages[0] if first.pages else first.new_page())
    shown = board_day.call(triage)
    _until(page, _SHOWS, shown)
    _until(page, "() => window.ClinicQBoardOffline.state().kept")
    # The worker has what the page needs.
    _until(
        page,
        "async () => (await caches.keys()).some((n) => n.startsWith('clinicq-board-'))"
        " && !!(await caches.match('/static/js/board.js'))"
        " && !!(await caches.match('/display/__last-board__'))",
    )
    heard = page.evaluate("() => window.ClinicQBoardOffline.state().lastGood")
    first.close()  # the power goes

    box = _boot(board_day, box_profile, offline=True)
    page = box.pages[0] if box.pages else box.new_page()
    booted = time.monotonic()
    page.goto("/display")
    page.wait_for_selector(".panel", timeout=15_000)
    drawn = time.monotonic() - booted
    assert page.evaluate(
        "() => document.querySelector('.kiosk').hasAttribute('data-from-cache')"
    )
    assert page.evaluate(_SHOWS, shown)
    banner = page.evaluate(_BANNER)
    assert banner == f"Not up to date. Last updated at {_clinic_time(heard)}."
    # Its styles and scripts came from the cache too: the board is laid out and drawn by board.js.
    assert (
        page.evaluate(
            "() => parseFloat(getComputedStyle(document.querySelector('.serving-number')).fontSize)"
        )
        > 40
    )
    assert page.evaluate("() => !!window.ClinicQBoard && !!window.ClinicQBoardLive")
    _shot(page, "board-booted-offline-from-cache")
    # The call made just before the power went is not "Called now" on an old board, nor said again.
    assert page.locator(".serving.is-new").count() == 0
    assert page.evaluate("() => window.ClinicQBoardAnnounce.log()") == []

    during = board_day.call(triage, finish_previous=True)
    box.set_offline(False)
    returned = time.monotonic()
    _until(page, _SHOWS, during, timeout=RECOVERY_BUDGET_SECONDS + 5)
    _until(
        page,
        "() => !document.querySelector('.kiosk').hasAttribute('data-stale')",
        timeout=5,
    )
    took = time.monotonic() - returned
    assert took <= RECOVERY_BUDGET_SECONDS
    print(  # noqa: T201 — quoted in the PR
        f"\npower cut, booted offline: board drawn from cache in {drawn:.1f} s with {banner!r}; "
        f"network back: current after {took:.1f} s"
    )


def test_a_removed_box_forgets_its_board_so_an_offline_reboot_shows_none(
    board_day: SimpleNamespace, box_profile: Path
) -> None:
    """Removed, the box goes to its pairing code, which drops what it kept; offline after that, no board."""
    board_day.open_queues(1)
    first = _boot(board_day, box_profile, offline=False)
    board_day.pair_context(first, board_day.clinic.base_url)
    page = _live_board(board_day, first.pages[0] if first.pages else first.new_page())
    _until(page, "() => window.ClinicQBoardOffline.state().kept")

    # The manager removes it (the dashboard's Remove, as the device registry records it).
    with board_day.clinic.session() as db:
        db.execute(update(DisplayDevice).values(revoked_at=now_sast()))
        db.commit()
    page.wait_for_selector(".pair-code", timeout=60_000)
    _until(
        page,
        "async () => !(await caches.keys()).some((n) => n.startsWith('clinicq-board-'))"
        " && !Object.keys(localStorage).some((k) => k.startsWith('clinicq.board.'))",
    )
    first.close()

    box = _boot(board_day, box_profile, offline=True)
    page = box.pages[0] if box.pages else box.new_page()
    with pytest.raises(
        Exception, match=re.compile("ERR_INTERNET_DISCONNECTED|net::", re.I)
    ):
        page.goto("/display", timeout=15_000)
    assert page.locator(".panel").count() == 0


def test_a_day_offline_leaves_the_heap_the_listeners_and_the_layout_where_they_were(
    board_day: SimpleNamespace,
) -> None:
    """A dead router all day on the page's clock: an attempt or a poll every few seconds, and nothing piles up."""
    queues = board_day.open_queues(4)
    for queue_id in queues:
        board_day.issue(queue_id, 3)
        board_day.call(queue_id)
    with router(board_day.clinic.base_url) as link:
        page = board_day.new_page(1280, 720, base_url=link.base_url)
        # Before the page's scripts run, so all of their timers follow the page's clock.
        page.clock.install()
        page = _live_board(board_day, page)
        cdp = page.context.new_cdp_session(page)
        cdp.send("Performance.enable")

        def snapshot() -> dict[str, Any]:
            cdp.send("HeapProfiler.collectGarbage")
            cdp.send("HeapProfiler.collectGarbage")
            metrics = {
                m["name"]: m["value"]
                for m in cdp.send("Performance.getMetrics")["metrics"]
            }
            return {
                "heap": metrics["JSHeapUsedSize"],
                "nodes": metrics["Nodes"],
                "listeners": metrics["JSEventListeners"],
                "attached": page.evaluate(
                    "() => document.getElementsByTagName('*').length"
                ),
                "rects": page.evaluate(
                    "() => [...document.querySelectorAll('.panel')].map((p) => {"
                    " const r = p.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })"
                ),
                "numbers": page.evaluate(_SERVING),
            }

        def run_minutes(minutes: int) -> None:
            for _ in range(minutes // 10):
                page.clock.run_for(600_000)
                time.sleep(0.05)  # let given-up requests report back

        window = 6 if OFFLINE_SOAK_HOURS >= 3 else 3

        def samples() -> list[dict[str, Any]]:
            """Snapshots ten minutes apart: one alone could land mid-attempt, with a stream's listeners."""
            taken = []
            for _ in range(window):
                run_minutes(10)
                taken.append(snapshot())
            return taken

        link.cut()
        run_minutes(30)  # warm up: the backoff reaches its ceiling and polling starts
        first = samples()
        run_minutes(max(0, int(OFFLINE_SOAK_HOURS * 60) - 2 * window * 10))
        last = samples()
        stale = page.evaluate(_STALE)
        _shot(page, f"board-offline-{OFFLINE_SOAK_HOURS:g}h-{stale}")
        attempts = page.evaluate("() => window.ClinicQBoardLive.state().attempts")
        summary = {
            key: (
                [min(x[key] for x in first), max(x[key] for x in first)],
                [min(x[key] for x in last), max(x[key] for x in last)],
            )
            for key in ("heap", "nodes", "listeners", "attached")
        }
        message = (
            f"{OFFLINE_SOAK_HOURS:g} h with a dead router, first -> last {window * 10} minutes (min, max): "
            f"{summary}; attempts {attempts}, banner {stale!r}"
        )
        print("\n" + message)  # noqa: T201 — quoted in the PR

    def lowest(samples: list[dict[str, Any]], key: str) -> float:
        return min(sample[key] for sample in samples)

    def highest(samples: list[dict[str, Any]], key: str) -> float:
        return max(sample[key] for sample in samples)

    assert lowest(last, "heap") <= lowest(first, "heap") * 1.10 + 512 * 1024, summary
    assert highest(last, "nodes") <= highest(first, "nodes") * 1.05, summary
    assert highest(last, "listeners") <= highest(first, "listeners"), summary
    assert {x["attached"] for x in last} == {x["attached"] for x in first}, summary
    assert {str(x["rects"]) for x in first + last} == {str(first[0]["rects"])}
    assert {str(x["numbers"]) for x in first + last} == {str(first[0]["numbers"])}
    assert attempts > OFFLINE_SOAK_HOURS * 60, (
        "the page kept trying, about once a minute"
    )
    # Past the four-hour limit the numbers are hidden; before it, they are shown as old.
    offline_minutes = 30 + OFFLINE_SOAK_HOURS * 60
    assert stale == ("expired" if offline_minutes >= 4 * 60 else "stale")
