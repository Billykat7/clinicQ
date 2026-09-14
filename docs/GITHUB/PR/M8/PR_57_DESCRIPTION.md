# PR: A live board that shows a call within 2 seconds and heals itself (Issue 57 / M8-57)

**Milestone:** [Milestone 8: Waiting-room Display Monitor](https://github.com/Billykat7/clinicQ/milestone/8) ·
**Issue:** [#57](https://github.com/Billykat7/clinicQ/issues/57) · **Builds on:** #41 (the lifecycle, M6),
#49 (the live-events kernel, PR #173), #58 (the projection, PR #178) and #56 (the board page, PR #179),
all merged · **Unblocks:** #60 (announcements), #62 (resilience)

When staff press *Call next*, every board at the clinic shows the number **33–57 ms** later. A board that
loses its connection, or whose server restarts, notices, says so on screen, reconnects with backoff, and
catches up with a full resync, with no reload and nobody at the clinic.

- **One stream, the projection only.** `GET /display/{site_id}/stream` is read-only and unauthenticated.
  Every event carries the board as #58's projection allows, and nothing else. A guard test fails if the
  route formats anything unchecked, and the privacy sweep now searches the stream too.
- **`board.state` first on every connection**, so every reconnection is a full resync.
- **A heartbeat every 15 seconds.** Two missed beats (30 s) show "⟳ Reconnecting to the clinic…" and open
  a new connection.
- **Connections stay flat.**
  - The per-site limit: 503 with `Retry-After`.
  - A client that is gone is noticed at the next beat, and `clinicq_live_streams_open` is on `/metrics`.
  - A simulated eight-hour day, and 48 rounds of real HTTP streams vanishing without closing, both end
    with no stream left behind.
- **Across instances.** With `REDIS_URL`, events fan out over Redis pub/sub, so a call made through one
  worker reaches boards connected to another.

## Summary

- **The stream** (`src/web/display_stream.py`, new; `src/modules/display/board_state.py`, new):
  - It opens a subscription, projects the board, and sends `board.state`. After that it sends each
    `ticket.called`, `queue.updated` or `board.config_changed` with the board after the change, and a bare
    `heartbeat` every `BOARD_HEARTBEAT_SECONDS` (15).
  - The viewer is resolved as on the page and `/state`: numbers only for an address anyone can type, the
    site's mode for its signed-in staff (#58).
  - A clinic with no public board gets 404; a full clinic gets 503 and `Retry-After: 30`. A stream whose
    clinic stops having a board (checked every 60 s) ends, and the screen reconnects to the 404.
  - There is no request-scoped session: every read opens a short one off the event loop
    (`short_session`, moved to `src/web/context.py` and shared with the dashboard's stream).
  - `board_event()` rebuilds each event from its envelope plus `board` only, dropping the called number
    the dashboard's event carries. It runs `ensure_projected` over the whole event before it can be sent.
  - `BoardProjections` keeps the last board per clinic and viewer. A stream answering an event reuses a
    board projected after that event reached the process, so ten screens and a burst of joins cost one
    projection.
- **The kernel** (`src/core/live_events.py`):
  - `SiteEventBroker.events()` yields event objects, and `stream()` formats them, so the dashboard's
    stream is unchanged. `LiveEvent` gains a monotonic `stamp` (not sent) and `from_payload()`.
  - `publish_live()` delivers locally, then through `RedisFanout`, which publishes on
    `clinicq:live-events` with an `origin` id. Every instance's daemon listener relays other instances'
    messages to its broker and skips its own.
  - A Redis failure is logged once per outage and never raised. The listener re-subscribes after 5 s.
  - `start_fanout()` and `stop_fanout()` run in the application's lifespan.
  - The `clinicq_live_streams_open` gauge is added.
- **Consent reaches the screen** (`src/modules/patients/consent.py`): recording a `DISPLAY_NAME` or
  `DISPLAY_COMMENT` answer publishes, after the commit, a `QueueChanged` for each queue the patient is
  still in today. The board projects again and reads consent afresh, and nothing about the patient
  travels.
- **The client** (`src/static/js/board-live.js`, new):
  - An `EventSource` whose board events go to `window.ClinicQBoard.apply`.
  - A watchdog: nothing heard for two beats means reconnect.
  - Backoff of `min(30 s, 1 s × 2^(n−1))`, half of it random; `online` retries at once.
  - After three failures it also polls `/state` every 10 s, and stops once live.
  - A reload only when the clinic's language changed.
  - The state is announced as `board:connection`, and `window.ClinicQBoardLive.state()` answers it
    (#62's stale banner builds on it).
  - `board.js` no longer polls. The template carries `data-stream-url`, `data-heartbeat-seconds` and
    the connection line.
- **Settings:** `LIVE_EVENTS_FANOUT` (default on, and it does nothing without `REDIS_URL`), in
  `.env.example`.

## Design notes

**Who builds what, settled.** The spec names A and the sprint plan D. The stream is A's, because it
carries the guard-tested projection A owns from #58. The client is D's, and only draws what the stream
brings. Recorded in the spec's note.

**The board rides with the event.** The dashboard's events say *what* changed and its page re-reads its
cards through its gate. A board is different: it is public, and #62 needs it to work from what it last
received. So a board event carries the projection, and a kiosk never makes a second request to learn what
a call changed. The envelope is the dashboard's (`type`, `site_id`, `at`, `queue_id`), which is what #49's
"reuse this event format" asks. `LiveEventType.BOARD_STATE` is the only new type.

**A resync without a gap.** The stream subscribes first and projects second. A change committed between
the two is either already in the first board or arrives as an event after it, never neither.

**Why the fan-out delivers locally first.** Sending every event only through Redis would make this
instance's own boards depend on Redis. Instead the writing instance delivers at once and publishes for
the others, and each instance drops its own echo by `origin`. An outage costs other instances' boards
freshness until their next reconnection or event, never this instance's.

**Honest watchdog tests on a fake clock.** Playwright's clock runs the page's timers, while the server's
beats arrive in real time. The e2e server beats every half second and the page still expects 15 s, so the
silent-stream test can turn a real heartbeat off (3,600 s) and step the page's clock through exactly
29 s (still live) and 31 s ("Reconnecting").

The eight-hour memory test from #56 now receives its calls over one live stream. It tells the page to
expect a beat once a day, because eight fast-forwarded hours with real beats would otherwise reconnect on
every step.

**Out of scope:** the offline cache, stale banner with its time and service worker (#62), announcements
(#60), and device tokens (#61). The patient's phone half of M7's two-second criterion is #68.

## Changes

- **New:**
  - `src/web/display_stream.py`, `src/modules/display/board_state.py`, `src/static/js/board-live.js`;
  - `tests/integration/display/test_display_sse.py`, `tests/e2e/display/test_board_live.py`;
  - `docs/GITHUB/PR/M8/assets/pr57/*.png`.
- **`src/core/live_events.py`:** `events()`, `stamp`, `from_payload()`, `RedisFanout`, `publish_live()`,
  `start_fanout()`/`stop_fanout()`, the gauge. **`src/commons/enums.py`:** `LiveEventType.BOARD_STATE`.
- **`src/main.py`:** the stream router; the fan-out in the lifespan. **`src/core/config.py`**,
  **`.env.example`:** `LIVE_EVENTS_FANOUT`.
- **`src/modules/patients/consent.py`:** board consent answers announce `QueueChanged`.
- **`src/web/context.py`:** `short_session()` (from `src/web/dashboard/routes.py`, which now imports it).
- **`src/web/display.py`**, **`src/templates/display/board.html`**, **`src/static/js/board.js`**,
  **`src/static/css/board.css`:** the stream URL, the heartbeat, the connection line; polling moved out
  of `board.js`.
- **Tests updated:**
  - `tests/unit/display/test_board_privacy.py`: the stream door and the source guard;
  - `tests/integration/display/test_board_privacy_endpoints.py`: the stream is swept;
  - `tests/e2e/display/conftest.py`: the server beats every 0.5 s, with a restartable app;
  - `tests/e2e/display/test_board_page.py`: calls arrive over the stream;
  - `tests/e2e/conftest.py`: `serve(app, port=…)` restarts a server where it was.
- **Docs:**
  - `docs/PRODUCT/04-display-monitor.md` (the events table), `docs/CICD/PIPELINES.md`,
    `infra/monitoring/README.md` (the gauge);
  - the spec's owner note;
  - `M7_clinic_dashboard.md`: the two-second criterion is met for the board, and waits only for #68;
  - M8: the status and the first exit criterion;
  - the progress: `docs/GITHUB/README.md`, `README.md`, `docs/TEAM/WORKLOAD_SPLIT.md` (sprint 9).

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (269 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL, Redis and Chromium:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  2001 passed, 1 skipped, 9 xfailed in 218.23s
  ```

- [x] The new tests (16 passed in 49.62 s, with the Redis and browser ones required):

  ```text
  tests/integration/display/test_display_sse.py::test_a_stream_opens_with_the_whole_board_and_sends_each_change_with_the_board
  tests/integration/display/test_display_sse.py::test_a_stream_is_refused_for_a_clinic_with_no_board_and_when_the_clinic_is_full
  tests/integration/display/test_display_sse.py::test_a_board_consent_answer_is_announced_to_the_patients_queues_after_the_commit
  tests/integration/display/test_display_sse.py::test_every_screen_of_a_clinic_shares_one_projection_per_change
  tests/integration/display/test_display_sse.py::test_a_simulated_eight_hour_day_of_boards_dropping_and_reconnecting_leaves_no_stream_behind
  tests/integration/display/test_display_sse.py::test_a_change_on_one_instance_reaches_the_streams_of_another_through_redis
  tests/integration/display/test_display_sse.py::test_a_fanout_that_cannot_reach_redis_never_raises_and_keeps_local_screens_current
  tests/e2e/display/test_board_live.py::test_a_call_reaches_the_board_within_two_seconds
  tests/e2e/display/test_board_live.py::test_a_restarted_server_is_rejoined_with_a_full_resync_and_no_reload
  tests/e2e/display/test_board_live.py::test_a_silent_connection_is_noticed_within_thirty_seconds_shown_and_healed
  tests/e2e/display/test_board_live.py::test_a_ten_minute_outage_recovers_with_nobody_touching_the_board
  tests/e2e/display/test_board_live.py::test_connections_stay_flat_over_a_simulated_day_of_boards_that_vanish_without_closing
  tests/unit/display/test_board_privacy.py::test_a_board_stream_handed_raw_data_refuses_before_sending
  tests/unit/display/test_board_privacy.py::test_the_board_stream_sends_only_projected_events_and_heartbeats
  tests/integration/display/test_board_privacy_endpoints.py::test_under_number_only_no_board_endpoint_carries_a_name_or_a_comment_key_for_anyone
  tests/e2e/display/test_board_page.py::test_eight_hours_of_calls_leave_the_heap_the_dom_and_the_layout_where_they_started
  ```

  What the browser tests printed:

  ```text
  call to screen: ['57 ms', '46 ms', '38 ms', '43 ms', '33 ms']
  noticed the restart in 0.00 s, live again 0.58 s after it returned
  31 failed attempts in ten minutes, then live and current
  open streams after each half hour: [2, 4, 4, 4, …, 4] (48 half-hours); at the end: 0
  8 h, 160 calls over one stream: attached 158 -> 158, heap 1,971,508 -> 2,053,416 bytes (+4.2%), nodes 578 -> 579, listeners 42 -> 42
  ```

  The silent-stream test asserts "live" at 29 s and "Reconnecting to the clinic…" visible at 31 s of
  silence on the page's clock, then live again after the retry.

- [x] **Across two processes, through Redis, on the dev database.** A board stream was open on the local
  server. A separate Python process called the next patient in General consultation, and the stream
  received it:

  ```text
  $ curl -sN http://127.0.0.1:8019/display/…/stream &   then, in another process: python call_one.py 1
  id: 3
  event: board.state
  id: 4
  event: ticket.called
  "type":"ticket.called","site_id":"01a0a0e2-d771-76cd-bd6c-bd7290e1491c","at":"2026-09-14T20:10:05.762303+02:00"
  ```

- [x] **A real restart under a real board.** A board was open in Chromium on the local server, and the
  server process was stopped. A patient was called from another process while it was down, then the server
  was started again:

  ```text
  live
  reconnecting            (the line "⟳ Reconnecting to the clinic…" is shown)
  live again, not reloaded: True  {"state": "live", "attempts": 0}   A004 on the board, called during the outage
  ```

### Screenshots (1280×720, from the seeded dev database)

The server stopped: the board keeps its numbers and says it is reconnecting:

![Reconnecting](https://github.com/Billykat7/clinicQ/blob/e44da313fae059961f81c86f7fdcb91ec97bafbc/docs/GITHUB/PR/M8/assets/pr57/board-reconnecting-1280x720.png?raw=true)

The server back: live, with the call made during the outage (A004) highlighted, and no reload:

![Live again](https://github.com/Billykat7/clinicQ/blob/e44da313fae059961f81c86f7fdcb91ec97bafbc/docs/GITHUB/PR/M8/assets/pr57/board-live-again-1280x720.png?raw=true)

## Acceptance criteria

- [x] A call-next reaches the board within 2 seconds: 33–57 ms from the commit to the number on the screen,
      five times (browser test)
- [x] Killing the connection triggers reconnection and a full resync: the server stopped under an open
      board, a call made meanwhile, the server restarted. Live again in 0.58 s with the call on screen and
      no reload (browser test, and by hand)
- [x] A missed heartbeat is detected within 30 seconds and shown in the UI: live at 29 s of silence,
      "Reconnecting to the clinic…" visible at 31 s (browser test)
- [x] Dead connections are cleaned up and do not accumulate over an 8-hour day. Tested three ways: a
      simulated day of silent drops, 480 minutes for 12 boards; 48 rounds of real HTTP streams abandoned
      mid-stream (the count stays at the streams still open, and 0 at the end); and one stream for eight
      hours on the page's clock (tests)
- [x] The stream is read-only and requires no authentication, exposing no personal data beyond the display
      mode: GET only, no session needed; the privacy sweep searches its events; a guard test fails on any
      unprojected send (tests)
- [x] A 10-minute outage recovers automatically with no manual intervention: 31 refused attempts over ten
      minutes on the page's clock, then live and current once the server returned (browser test)

## Risk and rollback

No migration. `LIVE_EVENTS_FANOUT` defaults on, but it does nothing without `REDIS_URL`. With Redis, each
instance holds one pub/sub connection and publishes one small message per queue change.

A board holds one long-lived HTTP connection. Behind a proxy, the stream sends
`X-Accel-Buffering: no`, and a proxy idle timeout must exceed 15 s, as for the dashboard's 5-second beat.

Dashboards are unchanged on the wire. Their stream is now `events()` formatted, and the existing tests pass
untouched.

Rollback is a revert of this PR. The board page then shows its first board and no updates until #56's
polling is restored, so revert #56 with it, or keep this.

**Known limits:**

- The eight-hour and ten-minute runs use the page's clock with a real server. A wall-clock run of the
  board is under way and reports in #62.
- The per-site limit of 100 streams is shared with the dashboards on each instance.
- A board keeps showing its last numbers while reconnecting. #62 adds the age of that data and the
  offline shell.

Closes #57
