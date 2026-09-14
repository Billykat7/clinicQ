# PR: The waiting-room kiosk board, legible at five metres in every layout (Issue 56 / M8-56)

**Milestone:** [Milestone 8: Waiting-room Display Monitor](https://github.com/Billykat7/clinicQ/milestone/8) ·
**Issue:** [#56](https://github.com/Billykat7/clinicQ/issues/56) · **Builds on:** #27 (display settings,
M4), #41 (the ticket lifecycle, M6) and #58 (the privacy projection, PR #178), all merged ·
**Unblocks:** #57 (the live stream), #59 (accessibility), #61 (kiosk devices)

The TV in the waiting room. `GET /display/{site_id}` shows every open queue's **now serving** and **up
next** numbers, a clock and the clinic's name, sized for a room of people reading from five metres.

- **It only ever draws the privacy projection.** #58 is now this issue's declared dependency and merged
  first, so the board uses the real payload rather than a fixture. The page renders through the checked
  renderer, and the privacy sweep now searches the page too.
- **Five layouts, measured:** one, two, three, four, and five or more queues (four panels a page), at 1080p
  and 720p. On a 32-inch screen a served number is **≥ 49.6 mm** tall in every layout (ADA §703.5.5 asks
  49 mm at 5 m), and an up-next number **≥ 22.1 mm** (a 6/18 letter at 5 m is 21.8 mm).
- **A new call is unmistakable without animation:** inverted colours, a heavy border and "▶ Called now".
  The pulse is the only thing reduced motion removes.
- **A day on the page's clock:** 160 calls over eight hours leave the heap, the DOM, the listeners and the
  layout where they started. A version with a deliberate leak fails the same test.

## Summary

- **The page** (`src/web/display.py`, `src/templates/display/board.html`, `src/static/css/board.css`,
  `src/static/js/board.js`):
  - `board_page()` projects the board once, for the viewer (#58), and hands the template the `BoardState`,
    its JSON payload (`data-initial`), the `/state` URL and the timings. It renders through
    `board_template_response()`, which refuses raw data. The frame's `settings` and `app_name` are added
    after that check.
  - `board.js` draws the panels from the payload at once, then polls `/state` every 10 s (#57 adds the
    stream). A panel's markup lives in `<template>` elements and its words in data attributes, so the
    script holds no copy of either.
  - **A panel:** the queue and its room; the newest call large, with its status ("Please come in", "Called
    again", "Being seen"); "Also called: T002 · T001" for earlier calls still in rooms; up to five numbers
    up next (four in the four-panel layout); and "7 waiting".
  - **Layouts** (`data-layout`): 1 fills the screen, 2 and 3 stand side by side, 4 is two by two. More than
    four queues stay in layout 4 and turn a page every 15 s, with "Page 1 of 2" in the corner.
  - **New calls:** a call less than 20 s old on the server's clock is highlighted. A call on a page that is
    not showing brings its page forward, and the page does not turn while it lasts. Each call is also said
    once through an assertive live region.
  - **Kiosk:** no pointer, no scrollbars, `100dvh` with overflow hidden. The layout already set `cursor:
    none`. Browser chrome goes with kiosk mode (#61's provisioning guide).
  - **Branding slot and ticker:** the clinic's initials and name in the header, and one general health
    notice at a time in the footer. Notices change every 12 s with a fade, never scroll, and are written in
    `src/modules/display/messages.py` (English until #77 translates them). `BOARD_HEALTH_TICKER=false`
    switches them off.
  - `display/unavailable.html` is shown for a clinic that is unknown, draft or suspended, with the same 404.
- **Type scale** (`board.css`): every size is a multiple of `--u`, 1% of the screen's height (or of a 16:9
  frame inside a narrower screen). Physical size therefore depends on the screen, not the resolution.
  Numbers use Roboto (`--font-ui`), whose zero is not a letter O, as the ticket stub does.
- **Tests** (see *Testing*):
  - `tests/e2e/display/` has 8 browser tests on PostgreSQL;
  - `tests/integration/display/test_board_page.py` checks the route;
  - the #58 privacy sweep now reads the page as well as the JSON.
- **Docs:**
  - `docs/OPS/BOARD_LEGIBILITY.md` (new) gives the thresholds, how the test measures them, and a
    step-by-step physical check with a tape measure and three readers;
  - `docs/PRODUCT/04-display-monitor.md` describes the page;
  - `docs/CICD/PIPELINES.md` gives the browser shard's new time;
  - the specs record #58 as a dependency of #56;
  - `.env.example` has `BOARD_HEALTH_TICKER`.

## Design notes

**Sized in physical units, then measured.** A board is judged from a distance, not in pixels, so `board.css`
sizes everything by the screen's height and the test converts what Chromium draws into millimetres on a
32-inch 16:9 panel (398.5 mm tall). Cap height is measured with `measureText('H')` in the element's
computed font, not assumed from the font size. The thresholds are published ones, chosen before tuning:

- **ADA 2010 §703.5.5** for the served number (5/8 in at 6 ft plus 1/8 in for every foot beyond, which is
  1.925 in = 49 mm at 5 m);
- **a 6/18 letter** for up next (15 minutes of arc, 21.8 mm at 5 m).

The four-panel layout was tuned against these twice. The first try spilled the highlight into the up-next
column and clipped two waiting numbers, which the measurement caught. The up-next column is now exactly two
numbers wide (4.9 em of the up-next size) and now serving takes the rest.

**Why four panels, then pages, rather than a denser grid.** Six or nine panels would push the served
number below 49 mm on a 32-inch screen. A clinic with five or more queues keeps full-size numbers and turns
pages. A new call jumps to its page and holds it, so a called number is never waiting behind a page turn.

**One renderer.** The page carries the payload, so `board.js` draws the first screen and every later one
the same way, and the live stream (#57) will hand it payloads through `window.ClinicQBoard.apply`. Panels
are kept and updated rather than rebuilt, and lists are replaced with `replaceChildren`. There is at most
one poll in flight, no listener is added after start-up, and the only timers are the poll, a one-second
tick and the notice change.

**Measuring memory honestly.** The first run of the day test showed the DOM growing by 13 nodes a call. The
growth came from Playwright: its locators keep matched elements reachable from the injected script. With
waits done through `evaluate`, the board's own node count is flat. The test explains this in `_until`, so
the next person does not "fix" the board for a leak that is not there. The mutation below shows the test
still catches a real one.

**Out of scope:** the live stream (#57), contrast checks and a high-contrast theme (#59), audio (#60),
pairing a box (#61) and the offline cache (#62). The health notices are not configurable per clinic yet:
the text is fixed in code, so nothing a clinic types reaches a public screen unreviewed.

## Changes

- **New:**
  - `src/modules/display/messages.py`;
  - `src/templates/display/board.html`, `unavailable.html`;
  - `src/static/css/board.css`, `src/static/js/board.js`;
  - `tests/e2e/display/conftest.py`, `test_board_page.py`;
  - `tests/integration/display/test_board_page.py`;
  - `docs/OPS/BOARD_LEGIBILITY.md`;
  - `docs/GITHUB/PR/M8/assets/pr56/*.png`.
- **`src/web/display.py`:** `board_page()`; `board_template_response()` adds the frame's configuration
  and a status code; the timing constants.
- **`src/core/config.py`**, **`.env.example`:** `BOARD_HEALTH_TICKER` (default on).
- **`tests/integration/display/test_board_privacy_endpoints.py`:** the page is read and searched too.
- **`tests/e2e/conftest.py`:** `browser` is module-scoped. Playwright's sync API keeps an asyncio loop
  running, and with a second browser module a local `pytest -n auto` failed a later `asyncio.run()` test on
  the same worker.
- **Docs:**
  - `docs/PRODUCT/04-display-monitor.md`, `docs/CICD/PIPELINES.md`;
  - the specs: `ISSUE_56` (depends on #58, note settled), `ISSUE_58` (unblocks #56), `docs/GITHUB/ISSUES/README.md`;
  - `M8_display_monitor.md` (the table, the diagram, *Start here*, the status);
  - the progress: `docs/GITHUB/README.md`, `README.md`, `docs/TEAM/WORKLOAD_SPLIT.md`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (267 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL, Redis and Chromium:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  1987 passed, 1 skipped, 9 xfailed in 224.23s
  ```

- [x] The new tests:

  ```text
  tests/e2e/display/test_board_page.py::test_the_board_lays_out_one_two_three_and_five_queues_legibly_at_1080p_and_720p[one-queue]
  tests/e2e/display/test_board_page.py::test_the_board_lays_out_one_two_three_and_five_queues_legibly_at_1080p_and_720p[two-queues]
  tests/e2e/display/test_board_page.py::test_the_board_lays_out_one_two_three_and_five_queues_legibly_at_1080p_and_720p[three-queues]
  tests/e2e/display/test_board_page.py::test_the_board_lays_out_one_two_three_and_five_queues_legibly_at_1080p_and_720p[five-queues]
  tests/e2e/display/test_board_page.py::test_a_new_call_is_unmistakable_with_motion_and_just_as_clear_without_it
  tests/e2e/display/test_board_page.py::test_the_highlight_ends_and_the_board_picks_up_the_next_call_on_its_own
  tests/e2e/display/test_board_page.py::test_more_queues_than_fit_turn_pages_and_a_new_call_brings_its_page_forward
  tests/e2e/display/test_board_page.py::test_eight_hours_of_calls_leave_the_heap_the_dom_and_the_layout_where_they_started
  tests/integration/display/test_board_page.py::test_the_page_carries_exactly_what_the_json_answers_and_the_timings_it_runs_on
  tests/integration/display/test_board_page.py::test_the_health_notices_can_be_switched_off_for_the_platform
  tests/integration/display/test_board_page.py::test_a_board_that_cannot_be_shown_says_so_with_the_same_404_as_an_unknown_id
  tests/integration/display/test_board_privacy_endpoints.py::test_under_number_only_no_board_endpoint_carries_a_name_or_a_comment_key_for_anyone
  12 passed in 49.95s
  ```

- [x] **Legibility and fit, measured in Chromium** against the seeded dev clinic. Each queue has seven
  waiting and a patient just called; millimetres are on a 32-inch 16:9 panel:

  ```text
  queues  viewport    layout  panels  pager         served cap  up-next cap  up next shown  spilling  scrolls  cursor
  1       1920x1080   1       1       —             96.3 mm     34.0 mm      5              0         no       none
  1       1280x720    1       1       —             96.3 mm     34.0 mm      5              0         no       none
  2       1920x1080   2       2       —             62.3 mm     25.5 mm      10             0         no       none
  2       1280x720    2       2       —             62.3 mm     25.5 mm      10             0         no       none
  3       1920x1080   3       3       —             53.8 mm     24.1 mm      15             0         no       none
  3       1280x720    3       3       —             53.8 mm     24.1 mm      15             0         no       none
  4       1920x1080   4       4       —             49.6 mm     22.1 mm      16             0         no       none
  4       1280x720    4       4       —             49.6 mm     22.1 mm      16             0         no       none
  5       1920x1080   4       1       Page 2 of 2   49.6 mm     22.1 mm      4              0         no       none
  5       1280x720    4       1       Page 2 of 2   49.6 mm     22.1 mm      4              0         no       none
  ```

  With five queues the page opened on page 2, because the newest call was in the fifth queue. That is the
  "a new call brings its page forward" rule working.

- [x] **The day's run, and the same test against a leaking board.** Eight hours on the page's clock, a call
  every three minutes, measured after forced garbage collection:

  ```text
  8 h, 160 calls: attached 156 -> 156, JS heap 1,988,720 -> 2,077,392 bytes (+4.5%), nodes 725 -> 725, listeners 35 -> 35, panel rectangles identical

  Mutation (reverted): board.js keeps every drawn ticket in window.__leak
  AssertionError: nodes 1433 -> 16797
  1 failed
  ```

- [x] **A real-time run started.** The same board is open in headless Chromium against a local server on the
  dev database, with a call every three minutes and a measurement every 15 minutes, since 19:53 on
  2026-09-14. It finishes after this PR merges, so its result is reported in #62's PR, which carries the
  same criterion.

### Screenshots (from the seeded dev database)

One queue, just called (1920×1080):

![One queue](https://github.com/Billykat7/clinicQ/blob/296e1710144e7053ee70692f480519ddeeca9e63/docs/GITHUB/PR/M8/assets/pr56/board-1-queue-1920x1080.png?raw=true)

Two queues:

![Two queues](https://github.com/Billykat7/clinicQ/blob/296e1710144e7053ee70692f480519ddeeca9e63/docs/GITHUB/PR/M8/assets/pr56/board-2-queues-1920x1080.png?raw=true)

Three queues:

![Three queues](https://github.com/Billykat7/clinicQ/blob/296e1710144e7053ee70692f480519ddeeca9e63/docs/GITHUB/PR/M8/assets/pr56/board-3-queues-1920x1080.png?raw=true)

Five queues, page 1 of 2, with a second call in Triage ("Also called"):

![Five queues, page 1](https://github.com/Billykat7/clinicQ/blob/296e1710144e7053ee70692f480519ddeeca9e63/docs/GITHUB/PR/M8/assets/pr56/board-5-queues-page-1-1920x1080.png?raw=true)

The same at 1280×720, with the same proportions:

![Five queues at 720p](https://github.com/Billykat7/clinicQ/blob/296e1710144e7053ee70692f480519ddeeca9e63/docs/GITHUB/PR/M8/assets/pr56/board-5-queues-page-1-1280x720.png?raw=true)

A call in the fifth queue brings page 2 forward:

![Five queues, page 2](https://github.com/Billykat7/clinicQ/blob/296e1710144e7053ee70692f480519ddeeca9e63/docs/GITHUB/PR/M8/assets/pr56/board-5-queues-page-2-1920x1080.png?raw=true)

A new call under `prefers-reduced-motion: reduce` (no pulse; colours, border and words unchanged):

![Reduced motion](https://github.com/Billykat7/clinicQ/blob/296e1710144e7053ee70692f480519ddeeca9e63/docs/GITHUB/PR/M8/assets/pr56/board-new-call-reduced-motion.png?raw=true)

## Acceptance criteria

- [ ] Ticket numbers are legible at 5 metres on a 32-inch screen. **Partly:** every number's size is
      measured in Chromium against published 5-metre thresholds, in every layout and at both resolutions
      (served ≥ 49.6 mm, up next ≥ 22.1 mm). **Nobody has yet stood 5 m from a physical 32-inch screen.** No
      screen or readers were available to this work, and no result is invented here. The procedure is
      `docs/OPS/BOARD_LEGIBILITY.md`, and #59 records the physical check.
- [x] The layout adapts correctly for 1, 2, 3 and 4 or more active queues: 1, 2, 3, 4 and 5 queues measured
      at 1080p and 720p (tests, screenshots)
- [x] A newly called ticket is unmistakable without animation, for reduced-motion users: the pulse is gone
      and the background, border, colour and "Called now" are identical (test, screenshot)
- [ ] The board runs for 8 hours in a browser without a memory leak or visual drift. **Partly:** eight
      hours on the page's own clock with 160 calls leave the heap (+4.5%), nodes, listeners and panel
      positions unchanged, and a leaking version fails (test). A wall-clock eight-hour run is under way and
      reports in #62.
- [x] The page renders correctly at 1080p and 720p: same proportions, nothing spills or scrolls (tests)
- [x] No scrollbars, cursor or browser chrome are visible in kiosk mode: no scroll and `cursor: none`,
      measured (tests). Browser chrome is the kiosk flags in #61's provisioning guide.

## Risk and rollback

No migration. One new setting, `BOARD_HEALTH_TICKER`, defaults on. The page is public and read-only, and
shows only what #58's projection allows. For an anonymous address, until #61, that is numbers only. Each
open board polls `/state` every 10 seconds: two small indexed queries per poll, and more only under a name
mode.

Rollback is a revert of this PR. `/display/{site_id}/state` (#58) keeps working.

**Known limits:**

- The physical 5-metre check has not been done (above).
- On screens smaller than 32 inches the same layout is proportionally smaller. `BOARD_LEGIBILITY.md` gives
  the 24-inch figures.
- Health notices are English only until #77.
- Until #57 the board learns of a call within 10 seconds, not 2.

Closes #56
