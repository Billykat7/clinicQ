"""The installable patient app in a real browser: offline, updates, the bounded cache and the install offer
(Issue 69).

Chromium against the real server and a migrated database, with the page's own scripts and the real service
worker. What is shown:

* **with no network, the last known place in line shows with its age**: the router between the phone and the
  server dies, the patient opens their ticket (and launches the app from the home screen), and the offline
  page shows the number, the place in line the phone last saw, when that was and how long ago, counting up;
* **a new deploy is picked up on the next launch**: the server's version changes, the patient opens the app
  again, and the new worker is in charge with the new release's shell, the old shell gone, the board's caches
  and the kept tickets untouched;
* **the cache is bounded**: the worker keeps exactly its shell, and however many tickets a phone follows it
  keeps at most five;
* **installation is offered only after a successful join**: the browser's own offer is always held back, the
  ticket's own patient sees "Add to home screen", and a shared link or the start page never does;
* **Chromium finds nothing stopping installation** (its own installability check, the one behind the
  install menu), and the offline page fits a 320 px screen.

Set ``TICKET_PAGE_SHOTS`` to a folder to save the screenshots quoted in the pull request.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.web import ticket as ticket_routes
from tests.e2e.conftest import router

pytestmark = pytest.mark.postgres

SHOTS = os.environ.get("TICKET_PAGE_SHOTS", "")

_CONTROLLED = "() => !!navigator.serviceWorker.controller"
_VERSION = """() => new Promise((resolve) => {
    const channel = new MessageChannel();
    channel.port1.onmessage = (event) => resolve(event.data.version);
    navigator.serviceWorker.controller.postMessage({ type: 'version' }, [channel.port2]);
    setTimeout(() => resolve(null), 2000);
})"""
_CACHES = "async () => (await caches.keys()).sort()"
_KEPT = """async () => {
    const cache = await caches.open('clinicq-patient-tickets');
    const rows = await Promise.all((await cache.keys()).map(async (r) => (await cache.match(r)).json()));
    return rows.map((row) => ({ path: row.path, position: row.state.position, headline: row.state.headline }));
}"""
_SYNTHETIC_OFFER = """() => {
    window.__prompted = 0;
    const offer = new Event('beforeinstallprompt', { cancelable: true });
    offer.prompt = () => { window.__prompted += 1; };
    offer.userChoice = Promise.resolve({ outcome: 'accepted' });
    window.dispatchEvent(offer);
    return offer.defaultPrevented;
}"""


def _until(page: Any, predicate: str, timeout: float = 20.0) -> Any:
    started = time.monotonic()
    while True:
        try:
            value = page.evaluate(predicate)
            if value:
                return value
        except Exception:  # the page is navigating: ask again
            pass
        assert time.monotonic() - started < timeout, (
            f"timed out waiting for {predicate}"
        )
        time.sleep(0.1)


def _shot(page: Any, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def _open(page: Any, path: str) -> None:
    page.goto(path)
    _until(page, "() => document.getElementById('tk-live').textContent === 'Live'")
    _until(page, _CONTROLLED)


def test_with_no_network_the_last_known_place_shows_with_its_age(
    patient_day: SimpleNamespace,
) -> None:
    """How to verify, step 2: airplane mode shows the last known position with its age."""
    mine = patient_day.ticket(ahead=3)
    with router(patient_day.clinic.base_url) as link:
        page = patient_day.owner_page(mine, width=320, base_url=link.base_url)
        _open(page, mine.path)
        patient_day.call()
        _until(
            page,
            "() => document.querySelector('[data-fill=\"position\"]').textContent === '3'",
        )
        _until(
            page, f"async () => (await ({_KEPT})()).some((row) => row.position === 3)"
        )

        link.cut()
        page.reload(
            timeout=40_000
        )  # the worker gives the network 10 s, then shows what it kept
        _until(page, "() => !document.getElementById('off-ticket').hidden")
        shown = page.evaluate(
            """() => ({
                number: document.getElementById('off-number').textContent,
                position: document.querySelector('#off [data-fill="position"]').textContent,
                updated: document.getElementById('off-updated').innerText.replace(/\\s+/g, ' '),
                age: document.getElementById('off-age').textContent,
                live: document.querySelector('#off .tk-live').textContent,
                wide: document.documentElement.scrollWidth,
            })"""
        )
        assert shown["number"] == mine.number and shown["position"] == "3"
        assert shown["live"] == "Offline"
        assert shown["updated"].startswith("Last updated ") and shown[
            "updated"
        ].endswith(" ago")
        assert shown["wide"] <= 320, "the offline page fits a 320 px screen"
        before = shown["age"]
        time.sleep(2.2)
        assert (
            page.evaluate("() => document.getElementById('off-age').textContent")
            != before
        ), "the age counts up"
        _shot(page, "offline-last-known")
        summary = f"offline: {shown['number']} position {shown['position']}, {shown['updated']!r}"
        print("\n" + summary)  # noqa: T201

        # Launching the installed app with no network opens the same last known ticket.
        page.goto("/t/?source=home-screen", timeout=40_000)
        _until(page, "() => !document.getElementById('off-ticket').hidden")
        assert (
            page.evaluate("() => document.getElementById('off-number').textContent")
            == mine.number
        )

        link.restore()
        # Connections held while the router was down are reset, so the first request after it may fail
        # too; the offline page keeps checking by itself until the live page loads.
        page.locator("#off-retry").click()
        _until(
            page,
            "() => document.getElementById('tk-live')?.textContent === 'Live'",
            timeout=75,
        )
        assert page.url.endswith(mine.path), (
            "back online, the app opens the live ticket"
        )


def test_a_new_deploy_is_picked_up_on_the_next_launch(
    patient_day: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """How to verify, step 3, with the release version changed under a running server."""
    mine = patient_day.ticket(ahead=1)
    page = patient_day.follower_page()
    _open(page, mine.path)
    first = _until(page, _VERSION)
    assert first == ticket_routes.worker_version()
    # A board's cache in the same browser, which the patient worker must never touch (Issue 62).
    page.evaluate(
        "async () => { await (await caches.open('clinicq-board-e2e')).put('/display/x', new Response('x')); }"
    )
    before = page.evaluate(_CACHES)
    assert f"clinicq-patient-shell-{first}" in before

    monkeypatch.setattr(ticket_routes, "worker_version", lambda: "9.9.9-next-deploy")
    page.goto(
        mine.path
    )  # the next launch: the browser checks the worker as it navigates
    _until(
        page, f"async () => (await ({_VERSION})()) === '9.9.9-next-deploy'", timeout=30
    )
    # The new worker is in charge as soon as it activates; it deletes the old shell as it does.
    _until(
        page,
        f"async () => !(await caches.keys()).includes('clinicq-patient-shell-{first}')",
        timeout=10,
    )
    after = page.evaluate(_CACHES)
    assert "clinicq-patient-shell-9.9.9-next-deploy" in after
    assert f"clinicq-patient-shell-{first}" not in after, (
        "the old release's shell is deleted"
    )
    assert "clinicq-board-e2e" in after and "clinicq-patient-tickets" in after
    print(f"\nworker {first!r} -> '9.9.9-next-deploy'; caches {before} -> {after}")  # noqa: T201


def test_the_shell_is_a_fixed_list_and_at_most_five_tickets_are_kept(
    patient_day: SimpleNamespace,
) -> None:
    mine = patient_day.ticket(ahead=1)
    page = patient_day.follower_page()
    _open(page, mine.path)
    version = _until(page, _VERSION)
    shell = _until(
        page,
        f"async () => (await (await caches.open('clinicq-patient-shell-{version}')).keys()).length",
    )
    assert shell == len(ticket_routes.PATIENT_SHELL)

    kept = page.evaluate(
        """async () => {
            const state = JSON.parse(document.getElementById('tk-state').textContent);
            for (let i = 0; i < 12; i++) {
                const path = '/t/' + String(i).padStart(2, '0').repeat(22).slice(0, 43);
                await window.BKPTickets.save(path, { ...state, number: 'X' + i });
            }
            const cache = await caches.open('clinicq-patient-tickets');
            return { entries: (await cache.keys()).length, newest: (await window.BKPTickets.latest()).state.number };
        }"""
    )
    assert kept == {"entries": 5, "newest": "X11"}


def test_installation_is_offered_only_after_a_join_and_never_by_the_browser_on_load(
    patient_day: SimpleNamespace,
) -> None:
    """Criterion: the install prompt appears only after a successful join."""
    mine = patient_day.ticket(ahead=2)

    for label, page, path in (
        ("shared link", patient_day.follower_page(), mine.path),
        ("start page", patient_day.follower_page(), "/t/"),
    ):
        page.goto(path)
        _until(page, "() => document.readyState === 'complete'")
        page.wait_for_timeout(500)
        assert page.evaluate(_SYNTHETIC_OFFER) is True, (
            f"{label}: the browser's own offer was not held"
        )
        assert page.locator("#tk-install").count() == 0, (
            f"{label}: offered installation"
        )
        assert page.evaluate("() => window.__prompted") == 0

    owner = patient_day.owner_page(mine, height=1400)
    _open(owner, mine.path)
    assert owner.locator("#tk-install").is_hidden(), (
        "nothing shows until the browser can install"
    )
    errors = owner.context.new_cdp_session(owner).send("Page.getInstallabilityErrors")
    assert errors["installabilityErrors"] == [], errors
    assert owner.evaluate(_SYNTHETIC_OFFER) is True
    assert owner.locator("#tk-install").is_visible()
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        owner.locator("#tk-install").screenshot(
            path=str(Path(SHOTS) / "install-offer.png")
        )
    owner.locator("#tk-install-yes").click()
    _until(owner, "() => window.__prompted === 1")
    _until(owner, "() => document.getElementById('tk-install').hidden")
