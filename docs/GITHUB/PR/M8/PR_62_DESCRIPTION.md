# PR: A board that keeps its last numbers, says how old they are, and recovers by itself (Issue 62 / M8-62)

**Milestone:** [Milestone 8: Waiting-room Display Monitor](https://github.com/Billykat7/clinicQ/milestone/8) ·
**Issue:** [#62](https://github.com/Billykat7/clinicQ/issues/62) · **Builds on:** #57 (the live stream, PR #180),
#61 (kiosk devices, PR #182) and #55 (the Playwright tooling, PR #177), all merged · **Closes M8**, with the
[`v0.8.0` release note](../../RELEASES/RELEASE_v0_8_0.md)

Load-shedding, clinic Wi-Fi and a restarting server are routine. With this PR, a waiting room never stares
at a frozen or blank screen without being told:

- **When the router dies**, the board keeps its numbers. After 20 seconds a banner takes the health
  notice's place: *"⚠ Not up to date. Last updated at 23:46."*, in the clinic's time.
- **When the network returns**, the board is live and current again in **3.3 s** after a 94-second
  outage, with no reload.
- **After a power cut with no network at boot**, a service worker serves the board from the box's own
  cache in **1.1 s**, with the last numbers and the banner. It is current **1.1–1.4 s** after the network
  returns.
- **The resilience suite** covers a dead router, a server restart, a slow link, a power cut, a removed box
  and a day offline.
- **An eight-hour offline run** leaves the DOM, the listeners and the layout where they were. The page's
  heap grew 10.7 %, which heap snapshots trace to Chromium's DevTools instrumentation, not to the board
  (see *The soaks*).

**Two real gaps were found by testing it the hard way, and are fixed here.** Chromium's offline switch
leaves an open event stream flowing, so it cannot imitate a dead router. The suite relays traffic through
a small `Router` that goes silent both ways with no error. Through it:

- a reconnection attempt that neither opened nor failed was never given up;
- one hung poll blocked every later poll.

## Summary

- **The service worker** (`src/static/board-sw.js`, served by `GET /display/board-sw.js` in
  `src/web/display.py` with this release's version written in, `Service-Worker-Allowed: /display`,
  scope `/display`). It is separate from the patient app's worker of #69: its own scope, its own
  `clinicq-board-*` caches, and it never touches another worker's caches.
  - **Navigations** under `/display` go to the network first, with an 8 s limit. A board page that
    arrives is kept, as itself and as "the last board". With no network, the kept page is served for its
    address, or the last board for `/display`, the address a box boots to. It is marked
    `data-from-cache`.
  - **Forgetting:** a start page that arrives as the pairing screen means the box is not paired, and every
    kept page is dropped.
  - **Assets:** the page sends its own address and the files it loaded. The page is fetched and kept at
    once, because a box's first navigation happens before the worker is in charge. On CI's Linux runner
    that timing left nothing cached for the offline boot until this was added. The files are served from
    the cache and refreshed behind it. The board's JSON, stream, heartbeat and pairing check are never intercepted.
- **Keeping the last board** (`src/static/js/board-offline.js`, new). It runs **only for a paired box**:
  the server gives a staff preview no worker URL, so a manager's browser keeps nothing.
  - **Keeping:** every board from the clinic is saved in `localStorage` with the time it arrived.
    Heartbeats and polls keep "last heard" current.
  - **Coming back:** a page from the cache draws the kept board, if newer, as an old board.
  - **The banner** replaces the ticker after 20 s without news, or at once on a cached boot.
  - **Four hours:** a kept board older than that is hidden and deleted, and the banner says since when.
- **An old board is honest** (`board.js`): `apply(payload, { stale: true })` does not take the board's
  time as the clinic's clock. It shows none of its calls as "Called now" and announces none of them again
  (#60). Every board drawn is announced as `board:applied`.
- **Reconnection that survives a dead router** (`board-live.js`):
  - an attempt silent for 30 s is given up and retried on the backoff;
  - a poll or access check is aborted after 8 s;
  - a poll that succeeds after a failed one starts the stream at once;
  - `state()` gains `lastNews`, the last time the server itself answered.
- **A removed box forgets** (`board-pair.js`): the pairing screen clears every kept board and tells the
  worker to drop its copies.
- **The banner** (`board.html`, `board.css`): `role="status"`, in the alert colour with a border and a ⚠
  shape. It is no taller than the footer, so the panels never move. When a board is too old, the panels
  are hidden with `visibility`.
- **Tests:**
  - `tests/e2e/display/test_board_resilience.py` (new, 7 tests);
  - `tests.e2e.conftest.Router` and `router()` (new), a relay that can go silent and come back, for any
    browser suite;
  - the worker route and who gets it (`tests/integration/display/test_board_page.py`);
  - the privacy sweep covers `/display/board-sw.js`;
  - #59's as-drawn contrast test now measures the banner in every theme;
  - #60's clips test blocks the worker, whose own requests a test's routes cannot see.
- **Two queue tests fixed for the small hours.** `test_recall_timers` and `test_transfer` put their
  tickets "an hour ago" and "two hours ago" and then acted "now". Between 00:00 and 02:00 in Johannesburg
  that crosses into a new service day. They failed on `main` at 00:10 and would have turned CI red for
  anyone pushing then. They now use a fixed hour of one day.
- **Docs:**
  - `docs/PRODUCT/04-display-monitor.md` (*When the clinic cannot be reached*);
  - `docs/OPS/KIOSK_SETUP.md` (outages, and two troubleshooting rows);
  - `docs/COMPLIANCE/ACCESSIBILITY_BOARD_EVIDENCE.md` (the banner's contrast and shape).
- **Closing M8:**
  - the M8 status, and the offline and two privacy exit criteria ticked with their evidence;
  - the gantt;
  - the README status and MVP checkbox, `docs/GITHUB/README.md`, `docs/TEAM/WORKLOAD_SPLIT.md`;
  - `docs/GITHUB/RELEASES/RELEASE_v0_8_0.md`.

## Design notes

**Why a relay and not the browser's offline switch.** A router that loses its uplink does not send errors.
Packets vanish, and a connection that was open stays open with nothing on it. `context.set_offline(True)`
refuses new requests but let the board's stream keep beating (probed: the board stayed live for 40
seconds offline). The `Router` drops bytes both ways and accepts connections without answering them.
On `restore()` it resets what it held, as a router coming back does. Power cuts still use the offline
switch, because a box booting with no network does get fast errors.

**What "last updated" means.** The last time the server answered at all: a board, a heartbeat, or a poll.
It is not the board's `as_of`, which only changes on a call. A quiet queue with a healthy connection is up
to date. The time is shown in Africa/Johannesburg, like the board's clock, whatever the box's zone.

**Why four hours, and why only boxes keep anything.** Under a name mode, a kept board holds names patients
agreed to show on that screen. Keeping it on the box that already showed it is no wider an audience. It
should not outlive the day or the pairing: after four hours the numbers mislead more than they help, and a
removed box drops it at once. A staff member's browser is not a waiting-room screen, so a preview keeps
nothing.

## The soaks

**Eight hours offline, on the page's clock** (`BOARD_OFFLINE_SOAK_HOURS=8`; CI runs one hour). The router
was dead the whole time: 580 reconnection attempts and a poll every ten seconds. The first and last hour
were each sampled six times, because a single sample can land mid-attempt, when a stream has six listeners:

| | First hour (min–max) | Last hour (min–max) |
|---|---|---|
| JS heap | 2,158,084–2,208,640 B | 2,389,028–2,417,388 B |
| DOM nodes | 565 | 565 |
| Listeners | 39–45 | 39–45 |
| Elements in the page | 138 | 138 |
| Panel rectangles, numbers | unchanged | unchanged |

The heap grew **+231 KB (+10.7 %)**, inside the test's budget (10 % + 512 KB) but steady. To find out what,
two heap snapshots were taken three hours apart on the same dead-router run, and every object type
compared. Of +624 KB:

- **285 KB** was `blink::NetworkResourcesData::ResourceData`, the DevTools network buffer that exists
  because Playwright attaches DevTools;
- **14 KB** was console messages kept for DevTools;
- **14 KB** was resource-timing entries, which the browser caps at 250;
- about 30 fetch loaders held by the dead router's sockets.

The board's own closures and promises grew by **about 6 KB** in three hours. A kiosk has no DevTools
attached, so this growth should not happen on a box, but that has not been measured on one.

**Eight hours online, on the wall clock.** Started 14 September at 19:53 against the dev database, on #56's
board, before this branch existed. That board polled, with no stream, announcements or offline keeping: a
headless Chromium at 1920×1080, a call every three minutes, measured every 15 minutes after a
half-hour warm-up.

**Still running when this was written** (it ends at about 03:53 on 15 September). This section is updated with the final measurement before the pull request merges. The measurements so far:

| When | Calls | JS heap (B) | DOM nodes | Elements | Listeners | Panels moved |
|---|---|---|---|---|---|---|
| 2026-09-14 20:23 | 11 | 1,841,784 | 465 | 126 | 35 | no |
| 2026-09-14 21:23 | 31 | 1,887,784 | 465 | 126 | 35 | no |
| 2026-09-14 22:23 | 51 | 1,890,652 | 465 | 126 | 35 | no |
| 2026-09-14 23:23 | 71 | 1,891,932 | 465 | 126 | 35 | no |
| 2026-09-15 00:08 | 86 | 1,881,800 | 369 | 85 | 35 | no |

16 measurements so far; heap 1,841,784–1,892,456 B, first 1,841,784 → last 1,881,800 B (+2.2 %); listeners [35]; panel rectangles identical in every measurement.

The drop in DOM nodes at midnight is the new service day: numbers restart at 001, and the waiting lines are shorter.

## Changes

- **New:**
  - `src/static/board-sw.js`, `src/static/js/board-offline.js`;
  - `tests/e2e/display/test_board_resilience.py`;
  - `docs/GITHUB/RELEASES/RELEASE_v0_8_0.md`;
  - `docs/GITHUB/PR/M8/assets/pr62/*.png`.
- **Board:** `src/web/display.py` (the worker route; `STALE_AFTER_SECONDS` 20, `STALE_LIMIT_SECONDS` 4 h;
  the worker URL for boxes only), `board.html`, `board.css`, `board.js`, `board-live.js`, `board-pair.js`.
- **Tests:** `tests/e2e/conftest.py` (`Router`, `router`), `tests/e2e/display/conftest.py` (`pair_context`
  exported), `test_board_accessibility.py`, `test_board_announce.py`, `tests/a11y/display/test_board_contrast.py`,
  `tests/integration/display/test_board_page.py`, `test_board_privacy_endpoints.py`,
  `tests/integration/queue/test_recall_timers.py`, `test_transfer.py`.
- **Docs:** the product, kiosk and accessibility docs above.
- **Closing M8:** `M8_display_monitor.md`, `IMPLEMENTATION_PLAN.md`, `README.md`, `docs/GITHUB/README.md`,
  `WORKLOAD_SPLIT.md`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (274 files)
- [x] `make milestone-progress-check ARGS='--assume-closed 62'`: 14 milestones up to date (M8 7/7, 62/109)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL, Redis and Chromium, at 00:25 in Johannesburg,
  after the two queue tests were fixed:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest -q --no-cov -n auto --dist loadscope
  2070 passed, 1 skipped, 9 xfailed in 445.18s
  ```

- [x] The resilience suite, with the eight-hour offline run and the screenshots
  (`BOARD_OFFLINE_SOAK_HOURS=8 BOARD_RESILIENCE_SHOTS=…`):

  ```text
  test_an_offline_board_keeps_its_numbers_and_says_when_it_was_last_updated PASSED
    router dead: banner 'Not up to date. Last updated at 23:46.' after 21.0 s, numbers still ['T001', 'A001']
  test_the_board_is_live_again_within_30_seconds_of_the_network_returning PASSED
    router back after 94 s dead: live after 3.3 s, the outage's call T001 shown and the banner gone after 3.3 s
  test_a_server_restart_is_weathered_with_the_banner_and_no_reload PASSED
    server back: current again after 0.8 s
  test_a_slow_link_still_brings_every_call_and_stays_live PASSED
    slow link: board loaded and live in 18.3 s; calls on screen after 0.1, 0.2, 0.2 s
  test_after_a_power_cut_with_no_network_the_box_shows_its_last_board_and_recovers PASSED
    power cut, booted offline: board drawn from cache in 1.1 s with 'Not up to date. Last updated at 00:02.';
    network back: current after 1.4 s
  test_a_removed_box_forgets_its_board_so_an_offline_reboot_shows_none PASSED
  test_a_day_offline_leaves_the_heap_the_listeners_and_the_layout_where_they_were PASSED
    8 h with a dead router, first -> last 60 minutes (min, max): heap ([2158084, 2208640], [2389028, 2417388]),
    nodes ([565, 565], [565, 565]), listeners ([39, 45], [39, 45]), attached ([138, 138], [138, 138]);
    attempts 580, banner 'expired'
  ```

  The slow-link profile is 1.5 s of latency each way and 200 kbit/s.

- [x] The whole browser suite under `-n auto`: 45 passed. #59's contrast test with the banner: 3 themes
  passed.
- [ ] **A real kiosk box through a real outage** (a Raspberry Pi unplugged, a router switched off): not
  done. Everything above is Playwright's Chromium on a Mac.

### Screenshots

The router has died: the numbers stay, and the banner replaces the health notice (1280×720):

![Router dead](https://github.com/Billykat7/clinicQ/blob/148bce91022e4f2878a3b807e6c4eef71f9b1c10/docs/GITHUB/PR/M8/assets/pr62/board-router-dead-stale-banner.png?raw=true)

A power cut, and the box boots with no network. Its board comes from its own cache with the last numbers.
The call made just before the cut is not shown as "Called now":

![Booted offline](https://github.com/Billykat7/clinicQ/blob/148bce91022e4f2878a3b807e6c4eef71f9b1c10/docs/GITHUB/PR/M8/assets/pr62/board-booted-offline-from-cache.png?raw=true)

Eight and a half hours without the clinic: past the four-hour limit the numbers are hidden, and the banner
says since when:

![Expired](https://github.com/Billykat7/clinicQ/blob/148bce91022e4f2878a3b807e6c4eef71f9b1c10/docs/GITHUB/PR/M8/assets/pr62/board-offline-8h-expired.png?raw=true)

## Acceptance criteria

- [x] An offline board shows the last known state plus a clear stale banner with a timestamp (browser tests:
      a dead router and an offline boot; screenshots)
- [x] The board recovers automatically within 30 seconds of the network returning: 3.3 s after a 94-second
      dead router; 0.8 s after a server restart; 1.4 s after an offline boot
- [x] A power cut and reboot returns to a working board with no human action: the box's browser closed and
      reopened from its profile, with and without network (this PR and #61). **Not tried on a real box**
- [x] The shell loads from cache when the box boots with no network: the board page, its styles and scripts,
      from the worker's cache, in 1.1 s
- [x] The resilience suite covers offline, server restart and a slow-link profile, plus a power cut, a
      removed box and a day offline
- [x] An 8-hour soak run shows no memory growth or visual drift. Offline on the page's clock: DOM, listeners
      and layout flat; the heap's +10.7 % traced to DevTools instrumentation, with the board's own objects
      +6 KB in three hours. Online on the wall clock: see *The soaks*. Neither ran on a real box

## Risk and rollback

No migration and no setting. What changes on a box:

- **A service worker** now controls `/display` on paired boxes. It needs a secure origin (HTTPS or
  `localhost`); staging over plain HTTP gets no offline shell, and everything else works.
- **Kept data:** the last board, in the box's browser storage for up to four hours, including names under a
  name mode. It is dropped when the box shows its pairing code.

Rollback is a revert of this PR, **plus clearing the worker from boxes**. A revert does not unregister a
worker a box already has. It keeps serving the files it cached first, refreshing them behind, so a box
would draw the old scripts once before the new ones. Clear each box's site data, or ship a replacement
\`board-sw.js\` that unregisters itself, before relying on the revert.

**Known limits:**

- A box that never showed its board online, or was removed since, cannot start offline.
- Removal still takes up to 30 s to reach an open board (the stream's access check, #61).
- The day-offline test runs one hour in CI to keep the browser shard short; the eight hours were run
  locally.

Closes #62
