# PR: A live front desk that says when it is not live: SSE, a polling fallback and stuck queues (Issue 49 / M7-49)

**Milestone:** [Milestone 7: Clinic Dashboard](https://github.com/Billykat7/clinicQ/milestone/7) ·
**Issue:** [#49](https://github.com/Billykat7/clinicQ/issues/49) · **Builds on:** #48 (the shell, PR
#170), #40 (the join service, PR #162) and #47's queue contract, all merged

The front desk now updates itself. A change made anywhere (a walk-in, a join from a phone, a call from
a room) reached a second browser in **308–381 ms** in five trials. The board never looks current when it
is not: the line above it always says **Live**, **Reconnecting, data from HH:MM** or **Updating every 5
seconds, data from HH:MM**. A queue where someone has waited past the clinic's threshold turns red
without anyone having to read a number.

There was no server-sent events (SSE) code in the kernel, and Issue 57 needs the same for the
waiting-room board. So the stream is built once, in `src/core/live_events.py`, and both use it:

- **one envelope:** `id`, `event`, and a JSON `data` that always has `type`, `site_id` and `at`;
- **one vocabulary:** `LiveEventType`, which is `ticket.called`, `queue.updated`,
  `board.config_changed` and `heartbeat`;
- **one per-clinic broker**, with a heartbeat, connection limits and clean-up.

**An event says what changed, never the new state**, so it carries no patient data. The page reads the
cards again through its own gate.

## Summary

- **Every queue write announces itself, after the commit.** `on_queue_changed()`, the hook every join,
  status change, transfer and priority override already calls, now queues a `QueueChanged` domain event
  with `publish_after_commit`. A call carries its ticket number (`transition_ticket` passes it for
  `called` and `recalled`). A saved display-settings change publishes `BoardSettingsChanged`. A
  subscriber turns each into a `LiveEvent` for the broker. A write that rolls back announces nothing.
- **`src/core/live_events.py`** (new):
  - `format_event()`;
  - `SiteEventBroker`: thread-safe `publish()` from the synchronous routes onto each stream's loop;
  - `stream()`: an opening heartbeat, then events, a heartbeat every 15 s, and an end when the client
    disconnects, loses access, stops reading or the clinic is at 100 streams.
- **`GET /dashboard/sites/{id}/board/stream`**: the stream, gated like the board (signed out `401`,
  another clinic `404`, no front desk `403`). The gate runs again at every heartbeat, in a worker thread,
  on a short-lived session. The stream holds no request session, so an all-day connection does not hold
  a pooled database connection.
- **`GET /dashboard/sites/{id}/board/cards`**: the cards alone, from the same partial the page renders,
  `Cache-Control: no-store`, `401` rather than a redirect when signed out.
- **`src/web/dashboard/board.py`** (new), `read_board()`: one query for the clinic's day, folded per
  queue into:
  - waiting, and with staff;
  - the **average wait today** (joined to called, over today's calls);
  - the **longest wait now**, and **stuck** once it passes `DASHBOARD_STUCK_WAIT_MINUTES` (default 45);
  - the tickets with staff, each with its status badge and minutes since called.

  Each waiting ticket in Issue 52's line also shows its minutes waited.
- **`src/static/js/dashboard-live.js`** (new):
  - An `EventSource`; any change event fetches the cards (debounced) and swaps them in.
  - After three failures, or with no `EventSource`, it polls the cards every 5 s **without a reload**
    and retries the stream every minute. Two missed heartbeats (35 s) count as a lost connection.
  - Even while live, it fetches the cards again every minute as a safety net.
- **Updates do not steal focus.**
  - A card is not replaced while someone is typing in it or dragging in its line; nothing is replaced
    while a dialog is open. A deferred update applies when focus leaves or the dialog closes.
  - Open waiting lines and their scroll positions carry over, and a focused "Move forward" button or
    line toggle is focused again in the new card.
  - `dashboard-reorder.js` now listens on the document, so its drag and tap still work on replaced
    cards. A saved move asks the live board to refresh instead of reloading the page.
- **Templates and CSS:**
  - `dashboard/_board_cards.html` (new) is used by the page and the fragment, and `board.html` carries
    the connection line;
  - the stuck card has a thick red edge, a tinted card and "Someone has waited N min";
  - the connection line is green only when live, amber with its data age otherwise.
- **Settings:** `DASHBOARD_STUCK_WAIT_MINUTES` (5–480), in `.env.example`.
- **Tidy-up:** Issue 52's reorder view model used its own copy of the channel labels. It now uses
  `TICKET_SOURCE_LABELS` from `src/web/components.py`.
- **Docs:** the dashboard product doc, the M7 Status row, the README Status block, sprint 6's row and the
  generated bars.

## Design notes

**Events name the change; the cards come through the gate.** Pushing rendered cards or ticket data down
the stream would put a second copy of the board's gates (the grant at the clinic, priority badges only
for those who may read them) into the stream code. A `queue.updated` with a queue id costs the page one
small fetch of a fragment the server has already gated. Measured below: a median 98 ms for ten queues
and 200 tickets. The same shape suits Issue 57: the public board's events can add the privacy
projection without changing the envelope.

**One hook, so nothing can change a queue silently.** `on_queue_changed()` is already called by every
queue write, and Issue 36's snapshot depends on that. Publishing from there covers joins on every
channel, calls, finishes, no-shows, cancellations, transfers and overrides without editing each path.
Publishing after the commit means a screen never re-reads a change that is not visible yet, and never
hears about one that rolled back. The rollback test proves both halves.

**The broker is in-process, on purpose, and said so.** The image runs one uvicorn process per instance,
so one broker hears every write on that instance. Several instances behind a balancer would each hear
only their own. That is Issue 57's Redis fan-out, behind the same `publish()`. Until then the minute-long
safety refresh bounds how stale a missed event can make a card, and the connection line never claims
more than the page knows.

**Honest states.** The line is green only after the stream has opened. On an error, or two missed
heartbeats, it goes amber and says how old the cards are. When the page polls, it says so and still
shows the age. A failed fetch keeps the old cards and says "Reconnecting, data from HH:MM" rather than
blanking the board.

**What Issue 55 adds.** Exponential backoff with jitter, an explicit offline state with a count of
attempts, and actions queued while offline all build on this file. The fixed intervals here are enough
for this issue's criteria and are named constants at the top of the file.

**Deviations from the issue's file list:** `src/web/dashboard/board.py` holds the read; the routes stay
in `routes.py` beside the page they serve.

## Changes

- **New:** `src/core/live_events.py`, `src/web/dashboard/board.py`, `src/templates/dashboard/_board_cards.html`,
  `src/static/js/dashboard-live.js`, `tests/unit/platform/test_live_events.py`,
  `tests/integration/dashboard/test_board_live.py`.
- **`src/commons/enums.py`:** `LiveEventType`. **`src/core/domain_events.py`:** `QueueChanged`,
  `BoardSettingsChanged`.
- **`src/modules/queue/snapshot.py`** (`on_queue_changed` publishes), **`src/modules/queue/lifecycle.py`**
  (the called number), **`src/modules/sites/router.py`** (display settings publish).
- **`src/web/dashboard/routes.py`:** the board, its cards and its stream; the room reads `read_board`.
- **`src/web/dashboard/reorder.py`**, **`src/templates/dashboard/_reorder.html`**,
  **`src/static/js/dashboard-reorder.js`:** minutes waited, shared labels, delegated listeners, refresh
  after a saved move.
- **`src/templates/dashboard/board.html`**, **`src/static/css/dashboard.css`**, **`src/core/config.py`**,
  **`.env.example`**.
- **Docs:** `docs/PRODUCT/05-clinic-dashboard.md`, `docs/GITHUB/MILESTONES/M7_clinic_dashboard.md`,
  `README.md`, `docs/GITHUB/README.md`, `docs/TEAM/WORKLOAD_SPLIT.md`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (252 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL and Redis:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  1915 passed, 1 skipped, 9 xfailed, 39 warnings in 178.78s
  ```

- [x] The new tests:

  ```text
  tests/integration/dashboard/test_board_live.py::test_a_committed_join_and_call_announce_themselves_to_the_clinics_streams
  tests/integration/dashboard/test_board_live.py::test_a_queue_write_that_rolls_back_announces_nothing
  tests/integration/dashboard/test_board_live.py::test_the_cards_show_the_day_and_mark_a_stuck_queue
  tests/integration/dashboard/test_board_live.py::test_the_cards_and_the_stream_are_gated_like_the_board
  tests/integration/dashboard/test_board_live.py::test_ten_queues_and_two_hundred_waiting_tickets_render_inside_the_budget
  tests/unit/platform/test_live_events.py::test_an_event_is_one_sse_message_with_the_shared_envelope
  tests/unit/platform/test_live_events.py::test_a_clinics_events_reach_its_streams_and_no_other_clinics
  tests/unit/platform/test_live_events.py::test_publishing_from_another_thread_reaches_the_stream
  tests/unit/platform/test_live_events.py::test_a_silent_stream_sends_a_heartbeat_and_ends_when_the_client_goes
  tests/unit/platform/test_live_events.py::test_a_stream_whose_caller_lost_access_ends_at_the_next_beat
  tests/unit/platform/test_live_events.py::test_a_clinic_cannot_open_more_streams_than_the_limit
  tests/unit/platform/test_live_events.py::test_a_stream_that_stopped_reading_is_dropped_not_kept
  12 passed
  ```

  The stuck-queue test reads the board at a fixed 10:00 on today's service day, so it cannot straddle
  midnight in a CI run.
- [x] **In a browser** (Playwright's Chromium, 1366×768, two separate browser contexts as two devices,
  against a local PostgreSQL database):

  ```text
  Device A (receptionist) opens the front desk -> "Live"
  Device B (manager, another browser) presses Call next on Triage, 5 times;
    time from B's request to A's Triage card changing:   381, 315, 308, 329, 312 ms
  Focus: A opens the reason prompt and types "Looks very pale"; B calls next in that queue; 2.5 s later
    the prompt is still open, focus is still in the note, the text is intact, the card behind has not
    moved; A cancels -> the card updates within a second
  A focuses a "Move forward" button; B calls next; 2 s later the same ticket's button has focus again
  A never reloaded (performance.timeOrigin unchanged)
  Network off on A -> "Reconnecting, data from 15:22"; network on -> "Live" again, on its own
  Stream blocked on a third browser -> "Updating every 5 seconds, data from 15:22";
    B calls next -> the change arrived at the next poll (5015 ms), and the page had not reloaded
  Ten queues, 200 waiting, 20 refreshes and scrolling up and down:
    cards fetch median 98 ms, max 120 ms; long tasks (> 50 ms): none; no horizontal scroll
  ```

### Screenshots (1366×768)

Live, with a stuck queue (someone has waited 54 minutes) and its open waiting line:

![Live front desk with a stuck queue](https://github.com/Billykat7/clinicQ/blob/8901f8f0874b1108ca11b7b53c1b7ecc461a775d/docs/GITHUB/PR/M7/assets/pr49/board-live.png?raw=true)

The network gone: the board says so, and how old its data is:

![Reconnecting, data from 15:22](https://github.com/Billykat7/clinicQ/blob/8901f8f0874b1108ca11b7b53c1b7ecc461a775d/docs/GITHUB/PR/M7/assets/pr49/board-reconnecting.png?raw=true)

The stream unavailable: polling, and saying so:

![Updating every 5 seconds](https://github.com/Billykat7/clinicQ/blob/8901f8f0874b1108ca11b7b53c1b7ecc461a775d/docs/GITHUB/PR/M7/assets/pr49/board-polling.png?raw=true)

Ten queues and 200 waiting patients:

![Ten queues, 200 tickets](https://github.com/Billykat7/clinicQ/blob/8901f8f0874b1108ca11b7b53c1b7ecc461a775d/docs/GITHUB/PR/M7/assets/pr49/board-10-queues.png?raw=true)

## Acceptance criteria

- [x] The board reflects a change made on another device within 2 seconds: 308–381 ms in five trials
      (browser); every committed queue write is announced, and only after its commit (tests)
- [x] SSE failure falls back to polling automatically, without a page reload: stream blocked ->
      "Updating every 5 seconds", the change arrived at the next poll, same page (browser)
- [x] A stuck ticket is visually obvious without reading numbers: red edge, tinted card and a warning
      line past the threshold (test and screenshot)
- [x] Losing the network shows "reconnecting, data from HH:MM", never a silently frozen board (browser
      and screenshot; a failed fetch keeps the cards and says their age)
- [x] The board handles 10 queues and 200 waiting tickets without noticeable lag: one read inside a
      budget (test), a 98 ms median refresh and no long tasks while refreshing and scrolling (browser)
- [x] Updates do not steal focus from a form a receptionist is typing into: the prompt kept its focus
      and text through a change, and a focused button kept focus across its card's replacement (browser)

## Risk and rollback

No migration and no API change: the stream and the cards are two page-level routes under the board, and
the domain event is published from a hook every queue write already runs. The broker drops a stream that
stops reading and refuses one beyond 100 per clinic (`503` with `Retry-After`). A proxy must not buffer
`text/event-stream`: the response sends `X-Accel-Buffering: no` and `Cache-Control: no-transform`.
Rollback is a revert of this PR; the page then falls back to what Issues 48 and 52 rendered.

**Known limits:** the broker is per process, so a multi-instance deployment relies on the one-minute
safety refresh until Issue 57 adds Redis fan-out. Backoff with jitter, the offline state and queued
actions are Issue 55's. The heartbeat-miss path (35 s) is exercised by the unit tests, not by the browser
run, where turning the network off fails the stream at once.

Closes #49
