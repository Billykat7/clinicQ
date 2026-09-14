# PR: A dashboard that is honest on a bad connection, never loses a press, and is tested in a browser (Issue 55 / M7-55)

**Milestone:** [Milestone 7: Clinic Dashboard](https://github.com/Billykat7/clinicQ/milestone/7) ·
**Issue:** [#55](https://github.com/Billykat7/clinicQ/issues/55) · **Builds on:** #49 (the live board,
PR #173), #50 (request keys and the actions, PR #175) and #51 (walk-ins, PR #176), all merged ·
**Closes M7** with the [`v0.7.0` release note](../../RELEASES/RELEASE_v0_7_0.md)

Clinic internet drops, and load-shedding is a scheduled fact of life. The dashboard now handles that
honestly:

- **It says so.** The line above the cards reads *Live*, *Reconnecting (attempt N)*, *Updating every
  5 seconds* or *Offline*, with the data's age. *Offline* appears within 10 seconds: at once when the
  browser knows, and in 4.8–8.9 s when the server simply stopped answering.
- **It recovers on its own**, with exponential backoff and jitter, and never needs a reload.
- **It never silently drops a press.** A press made while offline is held with its request key and
  sent once on return. If it waited too long it is reported as not sent, and nothing is changed.
- **An expired session asks for a sign-in in place**, then finishes what was pressed.

Playwright is now in `requirements.txt`, and a fourth CI shard runs eight browser tests against a real
server on PostgreSQL in about 30 seconds.

This is M7's last issue, so the PR also closes the milestone and writes the release note (see *Closing
M7*).

## Summary

- **Connection states and backoff** (`src/static/js/dashboard-live.js`):
  - **States:** `live`, `reconnecting` (with the attempt count), `polling` (with the data's time),
    `offline` (with the data's time and, past a minute, its age), `connecting`. Each change is
    announced as `board:connection`, and `window.ClinicQLive` answers the current state.
  - **Offline within 10 seconds.**
    - The staff streams now beat every **5 seconds**: `STAFF_HEARTBEAT_SECONDS`, passed by the
      dashboard routes. The broker's default stays at 15 for Issue 57's board, whose spec asks for 15.
    - A new `access_check_seconds` keeps the database re-check at every 15 seconds rather than every
      beat.
    - When nothing has arrived for 7 seconds, the page asks `/health` with a 2-second limit. No answer
      means *Offline*. A stream silent for 12 seconds while `/health` answers is reopened.
    - A browser `offline` event goes *Offline* at once, and `online` reconnects at once.
  - **Backoff:** each attempt waits `min(30 s, 1 s × 2^(n−1))`, half fixed and half random (equal
    jitter), so fifty PCs returning after load-shedding do not knock in lockstep. After three failures
    the page polls every 5 seconds and keeps retrying the stream on the same backoff. While offline,
    it probes on the backoff.
  - **Never reloads on 401.** It announces `session:expired` instead, so nothing the person was doing
    is lost; `session:renewed` reconnects.
  - `SiteEventBroker.stream()` times the heartbeat from the last thing sent, so a filtered room stream
    still beats on time.
- **The outbox** (`src/static/js/dashboard-outbox.js`, new; `outbox()` in `_queue_card.html`):
  - A press the clinic cannot receive (offline, an answer that never came, or a session that ended) is
    put back on the card and held with its `Idempotency-Key`. It is listed above the cards as *Waiting
    for the connection: Call next in Triage (pressed at 17:57)*.
  - When the page is live again, held actions are sent in the order pressed. Each ends as:
    - **Done**, with what it did ("Call next in Triage (T001), sent when the connection came back"),
      fading after 15 s;
    - **Not done**, with the clinic's reason, until dismissed;
    - **Not sent**, when it waited longer than `DASHBOARD_OUTBOX_EXPIRY_SECONDS` (120), until
      dismissed. A Call next from ten minutes ago is not what anyone wants now.
  - Sending twice is safe: the key makes the server answer a repeat with the first result. Pressing
    the same button again while its action waits adds nothing.
  - Held actions survive a reload (session storage), and closing the tab while one waits asks first.
  - Each outcome is also said on its own card's status line.
- **Sign in again, in place** (`login-modal.js`, `session-refresh.js`):
  - `window.BKPAuth.reauthenticate(message)` opens the sign-in modal on the page with the reason ("Your
    session ended. Sign in again to finish: Start for T001."), resolves once signed in, and does not
    navigate.
  - `window.BKP.reviveSession()` lets the silent refresh work again for the new session.
  - The outbox sends the held action after sign-in. The walk-in form sends the same walk-in with the
    same key.
- **Browser tests** (`tests/e2e`, new):
  - `tests/e2e/conftest.py` holds `browser` (one headless Chromium per worker) and `serve(app)` (the
    app in a background thread on a free port, stopped with its streams). The waiting-room board's
    suite (Issue 62) reuses them.
  - Without Chromium the tests skip with the install command. `REQUIRE_BROWSER_TESTS=1` turns that
    into a failure.
  - `tests/e2e/dashboard/conftest.py` gives each module a PostgreSQL database migrated by the real
    migrations, one clinic with its people, and its own server. Each test starts from an empty day.
    `DASHBOARD_OUTBOX_EXPIRY_SECONDS` is 8 there.
  - `test_dashboard.py` has eight tests: call-next, walk-in, reorder, offline, dead stream, the outbox
    sent once, the outbox not sent, and session expiry. They wait with Playwright's `expect`, not fixed
    sleeps, except where the wait *is* the test.
- **CI and dependencies:**
  - `playwright==1.62.0` joins the test section of `requirements.txt`.
  - `ci.yml` adds the `browser` shard (`tests/e2e`). It caches `~/.cache/ms-playwright`, runs
    `python -m playwright install --with-deps chromium`, and then the same pytest command as every
    shard.
  - The `flows` shard now ignores `tests/e2e`.
  - The image build removes `playwright` and `pyee` with the other test tooling, so the 1,000 MB image
    ceiling is not touched.
  - `docs/CICD/PIPELINES.md` documents the shard and its time budget.
- **Setting:** `DASHBOARD_OUTBOX_EXPIRY_SECONDS` (default 120, 1–900), in `.env.example`.

## Design notes

**Why a 5-second beat and a probe, not a shorter timeout.** A page can only tell a quiet stream from a
dead one by the heartbeat. At 15 seconds, two missed beats are 30 seconds, three times the criterion.
Beating every 5 seconds costs a few bytes per screen and no database read, because access is re-checked
on its own 15-second clock. The probe then turns suspicion into an answer: when the server stops
answering, `/health` times out after 2 seconds, so *Offline* follows 7–9 seconds of silence. In normal
operation no probe is ever sent, because the beat arrives first.

**Why the outbox expires actions.** "Succeeds on reconnect or reports a clear failure" leaves a choice
for an action that waited half an hour. Sending it would call whoever is next *now* on behalf of a
decision made about a queue that no longer exists. So an action that waited longer than two minutes is
reported *Not sent*, with the time it was pressed, and nothing is changed. The person decides again
with the current board in front of them.

**Why re-authentication does not leave the page.** The kernel's sign-in modal navigated to `next` on
success, which would lose the queued action and anything half-typed. A promise-returning
`reauthenticate()` keeps the page and its state, and the replay goes out with the same key as the
original press, so a press that did reach the server before the session ended is not done twice.

**A test of the offline path that does not lie.** `context.set_offline(True)` fires the browser's
`offline` event, which makes *Offline* instant. That is correct, but it would pass even without the
probe. So the PR's browser run also freezes the server process (`SIGSTOP`): the network interface
stays up and connections stay open, but nothing answers, which is what a hung upstream looks like. Three
freezes at different points between beats were reported *Offline* in 8.89, 5.85 and 4.83 s. The CI
suite keeps the deterministic `set_offline` and blocked-stream tests.

## Closing M7

- `make milestone-progress ARGS='--assume-closed 55'` puts M7 at 8/8, the project at 55/109, and
  7 of 14 milestones done.
- **M7 Status** says done with the merge of #177. Five exit criteria are ticked with their evidence.
  **The sixth stays unticked, with its reason:** "Call Next updates the patient's phone and the
  waiting-room board within 2 seconds" is met for the staff screens (320–369 ms, #175). The board is
  Issue 57 and the ticket page Issue 68, and neither exists yet.
- The gantt marks M7 done. The README Status block ticks the dashboard, with `v0.7.0` to cut.
  WORKLOAD_SPLIT's sprint 11 row records Issue 55. The sprint summary says sprints 6–9 and 11 are under
  way, not done, because their other lanes have not started.
- **[`RELEASE_v0_7_0.md`](../../RELEASES/RELEASE_v0_7_0.md)** records:
  - what #170–#177 shipped;
  - **migrations `0025`–`0027`** and what each downgrade does (`0026`'s drops every note);
  - the six settings, two scheduler jobs, the RBAC sync, the room-scoping behaviour change for `own`
    grants and the new `called → waiting` move;
  - the known issues, honestly: nothing outside the staff screens sees a call yet, the event broker is
    per process, no physical printer or tablet was used, no messages are sent, held actions live in
    one tab, the notification bell answers 403 for clinic roles, and only `v0.2.0` has ever been
    tagged.

## Changes

- **New:**
  - `src/static/js/dashboard-outbox.js`;
  - `tests/e2e/conftest.py`, `tests/e2e/dashboard/conftest.py`, `tests/e2e/dashboard/test_dashboard.py`;
  - `docs/GITHUB/RELEASES/RELEASE_v0_7_0.md`.
- **`src/static/js/dashboard-live.js`:** the states, backoff, probe, watchdog and session events.
  **`src/static/js/dashboard-actions.js`:** offline and 401 hand-off to the outbox.
  **`src/static/js/dashboard-walkin.js`:** sign in again, then resend.
  **`src/static/js/login-modal.js`:** `reauthenticate()`. **`src/static/js/session-refresh.js`:**
  `reviveSession()`.
- **`src/core/live_events.py`:** `STAFF_HEARTBEAT_SECONDS`, `ACCESS_CHECK_SECONDS`,
  `access_check_seconds`. **`src/web/dashboard/routes.py`:** the staff heartbeat and the outbox expiry
  in the context. **`src/core/config.py`**, **`.env.example`:** `DASHBOARD_OUTBOX_EXPIRY_SECONDS`.
- **Templates and CSS:** `_queue_card.html` (`outbox`), `board.html` and `room.html` (the outbox and
  its script), `dashboard.css` (the outbox, and a louder *Offline*).
- **CI and build:** `.github/workflows/ci.yml` (the `browser` shard), `requirements.txt` (Playwright),
  `infra/docker/Dockerfile` (removed from the image), `docs/CICD/PIPELINES.md`.
- **Tests updated:** `tests/unit/platform/test_live_events.py` (the access check on its own clock).
- **Docs (closing M7):** `docs/GITHUB/MILESTONES/M7_clinic_dashboard.md`, `docs/GITHUB/README.md`,
  `README.md`, `docs/PLAN/IMPLEMENTATION_PLAN.md`, `docs/TEAM/WORKLOAD_SPLIT.md`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (262 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL, Redis and Chromium:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  1956 passed, 1 skipped, 9 xfailed, 58 warnings in 184.26s
  ```

- [x] The browser suite, three runs in a row under `-n auto --dist loadscope`:

  ```text
  tests/e2e/dashboard/test_dashboard.py::test_call_next_answers_at_once_and_a_double_click_calls_one_patient
  tests/e2e/dashboard/test_dashboard.py::test_a_walk_in_by_keyboard_alone_takes_the_next_number_well_inside_ten_seconds
  tests/e2e/dashboard/test_dashboard.py::test_moving_a_patient_forward_needs_a_reason_and_leaves_a_priority_badge
  tests/e2e/dashboard/test_dashboard.py::test_losing_the_network_shows_offline_within_ten_seconds_and_comes_back_live_without_a_reload
  tests/e2e/dashboard/test_dashboard.py::test_a_dead_stream_is_retried_with_a_growing_wait_then_polled_and_goes_live_when_it_returns
  tests/e2e/dashboard/test_dashboard.py::test_call_next_pressed_while_offline_is_sent_when_the_network_returns_exactly_once
  tests/e2e/dashboard/test_dashboard.py::test_an_action_that_waited_too_long_is_reported_not_sent_and_changes_nothing
  tests/e2e/dashboard/test_dashboard.py::test_an_expired_session_asks_to_sign_in_again_and_then_finishes_the_action
  8 passed in 29.35s · 8 passed in 28.83s · 8 passed in 28.41s   (22.45s in one process)
  ```

- [x] New unit tests: `test_a_fast_beating_stream_checks_access_on_its_own_slower_clock` and
  `test_a_filtered_stream_skips_other_queues_and_still_beats_on_time`.
- [x] **In a browser** against a local PostgreSQL database (Playwright's Chromium at 1366×768):

  ```text
  Network off (the browser knows):     "Offline, data from 17:57" at once
  Server frozen (SIGSTOP), 3 times:    Offline after 8.89, 5.85 and 4.83 s; Live again 0.79–0.80 s after it resumed
  Offline, Call next in Triage and in General consultation:
    "Waiting for the connection: Call next in Triage (pressed at 17:57)" and the same for General consultation
  Network back: both sent, once each, 0.28 s after the network returned:
    "Done: Call next in Triage (T001), sent when the connection came back."
    "Done: Call next in General consultation (A001), sent when the connection came back."
    General consultation: with staff 0 -> 1
  Offline for longer than the outbox allows (8 s here), then back:
    "Not sent: Call next in Triage was pressed at 17:59 and waited too long for the connection, so it was
     not sent and nothing was changed. Press it again if it is still needed."
  The stream blocked: "Reconnecting (attempt 1), data from 17:58" -> "Updating every 5 seconds, data from
    17:58" -> Live again once unblocked, with no reload
  Session ended (access and refresh cookies removed), Start pressed on T001:
    the sign-in modal opens on the page: "Your session ended. Sign in again to finish: Start for T001."
    signed in -> "Done: Start for T001, sent after you signed in again."; the page never navigated
  ```

### Screenshots (1366×768)

Offline, with two presses held:

![Offline with actions held](https://github.com/Billykat7/clinicQ/blob/62bea1de26a1d2b4002c20bb87812aae6d06c600/docs/GITHUB/PR/M7/assets/pr55/offline-queued.png?raw=true)

Back online: both sent once, and the cards show them:

![Held actions done](https://github.com/Billykat7/clinicQ/blob/62bea1de26a1d2b4002c20bb87812aae6d06c600/docs/GITHUB/PR/M7/assets/pr55/back-online-done.png?raw=true)

An action that waited too long, reported and not sent:

![Not sent](https://github.com/Billykat7/clinicQ/blob/62bea1de26a1d2b4002c20bb87812aae6d06c600/docs/GITHUB/PR/M7/assets/pr55/not-sent.png?raw=true)

A server that stopped answering, reported Offline without any browser event:

![Offline, silent drop](https://github.com/Billykat7/clinicQ/blob/62bea1de26a1d2b4002c20bb87812aae6d06c600/docs/GITHUB/PR/M7/assets/pr55/offline-silent.png?raw=true)

A dead stream, reconnecting with its attempt:

![Reconnecting, attempt 1](https://github.com/Billykat7/clinicQ/blob/62bea1de26a1d2b4002c20bb87812aae6d06c600/docs/GITHUB/PR/M7/assets/pr55/status-reconnecting.png?raw=true)

The session ended: sign in again on the page, then the action is finished:

![Sign in to finish](https://github.com/Billykat7/clinicQ/blob/62bea1de26a1d2b4002c20bb87812aae6d06c600/docs/GITHUB/PR/M7/assets/pr55/session-sign-in.png?raw=true)

![Finished after signing in](https://github.com/Billykat7/clinicQ/blob/62bea1de26a1d2b4002c20bb87812aae6d06c600/docs/GITHUB/PR/M7/assets/pr55/session-finished.png?raw=true)

## Acceptance criteria

- [x] Disconnecting the network shows the offline state within 10 seconds: at once with the browser's
      offline event (test), and 4.8–8.9 s for a server that stopped answering (browser)
- [x] Reconnecting restores live updates without a manual refresh: a change made elsewhere arrives
      after reconnecting, on the same page (test); live again 0.8 s after a frozen server resumed
      (browser)
- [x] An action taken while offline either succeeds on reconnect or reports a clear failure, never
      silently vanishes: sent once on return, or *Not sent* after the expiry with nothing changed
      (tests, browser)
- [x] An expired session prompts re-authentication and then completes the pending action (test,
      browser)
- [x] Interaction tests cover call-next, walk-in, reorder and the offline path (eight browser tests)
- [x] The suite runs in CI within the time budget: about 30 s of tests plus a minute to install
      Chromium, inside the shard's 15-minute timeout (`docs/CICD/PIPELINES.md`)

## Risk and rollback

No migration. Staff streams beat every 5 seconds instead of 15, which is more bytes and no more
database reads. A proxy with an idle timeout under 5 seconds would cut them. The outbox only resends
requests the person made, with the key that makes a repeat harmless. `playwright` is test-time only and
removed from the image.

Rollback is a revert of this PR. The dashboard then reloads on an expired session and reports a lost
answer as before. The release note would be reverted with it.

**Known limits:**

- Held actions live in one browser tab, and are lost if the tab is closed anyway or the device loses
  power.
- The probe reads `/health` on the same host, so a proxy answering `/health` for a dead application
  would delay *Offline* to the 12-second stream silence.
- The browser suite runs Chromium only.

Closes #55
