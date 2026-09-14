# PR: Call next that answers at once, never calls twice, and undoes a mistake (Issue 50 / M7-50)

**Milestone:** [Milestone 7: Clinic Dashboard](https://github.com/Billykat7/clinicQ/milestone/7) ·
**Issue:** [#50](https://github.com/Billykat7/clinicQ/issues/50) · **Builds on:** #41 (the lifecycle),
#49 (the live board, PR #173) and #53 (the room and its room-scoped access, PR #174), all merged

Every front-desk card and every room now leads with a large **Call next** showing the number it will
call. Next to each patient with staff are the buttons their status allows (**Start**, **Recall**,
**No-show**, **Done**) and how long they have been at that step, counting live. A fresh call also has
**Undo call** with a countdown.

A press changes the card at once. If the server refuses, the card goes back as it was and explains why.
In a browser, a double tap sent **one** request and called **one** patient. On the server, the same
request key sent twice at the same instant called one patient; a PostgreSQL race test repeats that ten
times. A call on one device reached a second device's front desk in **320–369 ms**.

No page changes a status itself. Every button is a request to the queue API, and the API goes through
`transition_ticket()`.

## Summary

- **One press, one action: `Idempotency-Key`** (`src/modules/queue/request_keys.py`, new; migration
  `0027`):
  - *Call next*, the transitions route and the new undo route accept an optional `Idempotency-Key`
    header. `run_once()` inserts the key's row (`queue_request_key`, unique per user and key) **before**
    the action and commits both together.
  - The same key again, from the same person, for the same action, moves nothing and answers with the
    first request's ticket.
  - The same key used for a different action (another queue, another status, another clinic) gets
    `409 ticket.request.key_reused`.
  - A press that failed (nobody waiting, a stale screen) rolls its key back with it, so trying again is
    a real attempt.
  - Two requests with one key at the same instant cannot both act. On PostgreSQL the second insert
    waits on the unique constraint for the first transaction, then replays its answer.
  - Keys are read through the site guard. An hourly job (`queue_request_key_sweep`, advisory lock 554)
    deletes keys older than a day.
- **Undo call, a dedicated lifecycle move** (`src/modules/queue/lifecycle.py`):
  - `called → waiting` joins the table. It is also in `DEDICATED_MOVES`, so the transitions route
    refuses it (`409 ticket.transition.dedicated_route`) and only `undo_call()` makes it.
  - `undo_call()` requires the ticket to still be `called` (otherwise `409 ticket.transition.stale`)
    and the call to be within `QUEUE_CALL_UNDO_SECONDS` (default 30, 5–120; otherwise
    `409 ticket.undo.window_closed`).
  - The move clears `called_at`. The ticket keeps its order key and sequence, so the patient is back
    **in the same place**.
  - The audit trail has two rows, `waiting → called (call next)` and `called → waiting (undo call)`.
  - `POST /api/v1/sites/{site_id}/tickets/{ticket_id}/undo-call` is gated on `queues.call` `update`,
    like *Call next*, so it is room-scoped for a nurse.
- **The buttons, decided by the lifecycle** (`src/web/dashboard/actions.py`, new):
  - `ticket_actions()` offers **Start**, **Done**, **Recall** and **No-show** only where `is_legal()`
    allows them. It offers **Undo call** only while `undo_window_ends()` is in the future.
  - Only **No-show** asks first (see the design notes).
  - `Elapsed` is the time at the current step: *Being seen* since it started, *Called again* since the
    recall, *Called* since the call.
  - Each front-desk card (`read_board()`) now carries every with-staff ticket's id, status, time at
    step, buttons and undo deadline, and the **number Call next would call**.
  - The room reuses the card's tickets instead of querying them a second time.
- **One set of controls for both screens** (`dashboard/_queue_card.html`, new): the Call next button,
  the with-staff panel, the per-ticket buttons, a status line per card and the confirmation dialog.
  - Buttons are **disabled, not hidden**, with the reason, for a role without `queues.call` or
    `queues.tickets` `update`. The template decides no rule.
- **Optimistic, and honest about failure** (`src/static/js/dashboard-actions.js`, new):
  - A press marks the card busy (further taps are ignored), sends a fresh key, and changes the card
    at once: the waiting count drops, "Calling A001…" appears, or a badge reads "Being seen".
  - A refusal puts back the card exactly as it was, with focus on the same button. The status line
    says why: the server's sentence for a `409`, and a plain sentence for `404`, `403` and `422`.
  - A request whose answer never came **keeps its key for two minutes**. Pressing again asks about
    the same action instead of making a second one, and the message says so.
  - Messages survive the live refresh that follows.
  - The minutes at each step and the undo seconds count down for display, against the server's clock
    as estimated from the cards' read time. The undo button hides when its time is up; the server
    enforces the window regardless.
- **The room is live** (`routes.py`, `dashboard/_room_cards.html`, new):
  - `/room/cards` and `/room/stream` are gated like the room. The stream is filtered to the
    clinician's own queues, re-read at each heartbeat.
  - `SiteEventBroker.stream()` gains `accept`, and a filtered stream still sends its heartbeat on
    time, however busy other queues are.
  - `dashboard-live.js` serves both pages. It no longer replaces a card with a request in flight. It
    keeps open disclosures, **half-written notes and chosen transfer destinations** across a refresh,
    and announces each replaced card.
  - Notes and transfers in the room refresh the cards instead of reloading the page.
- **Contract:** `contracts/queue.yaml` documents the undo route (`200`/`401`/`403`/`404`/`409`/`422`),
  the `IdempotencyKey` header parameter on all three operations, the new codes and the extended
  transition table.
- **Docs:** the lifecycle diagram and its note in `docs/PRODUCT/03-booking-and-queue.md` (checked by
  the state-machine test), the dashboard product doc, the M7 Status row, the README Status block,
  sprint 7's row and the generated bars. `.env.example` is regenerated.

## Design notes

**Why a server-side key, when the page already ignores a second tap.** Disabling the button prevents a
double tap only on a page whose script is running and whose request reached the server once. It does
nothing for:

- a browser or proxy retrying a request whose answer was lost;
- a tablet on a clinic's Wi-Fi that timed out and was pressed again;
- the queued actions Issue 55 will replay after a reconnect.

Only the server can promise "exactly once", and "proven by a test" needs a promise the server makes. So
both halves are here:

- the page's busy flag, which saves the round trip;
- the key, which makes the promise, recorded in the same transaction as the move so the two can never
  disagree.

The key names its target, so a stale key replayed against another queue is refused rather than
answered with an unrelated patient.

**Why `called → waiting` is in the table, not a side door.** Putting a patient back is a status change.
Non-negotiable 2 says status changes go through `transition_ticket()`, and the state-machine, HTTP-pairs
and property tests read the table. So the move is in the table, the diagram and the tests' independent
`SPEC`. The window and the "still called" check live in `undo_call()`, and the transitions route
refuses the move as dedicated, the same pattern Issue 47 set for cancel and transfer.

The property tests (Issue 47) now also undo calls inside and after the window. They still hold every
invariant, including "a waiting ticket has no call time".

**Confirmation only for No-show.** The issue asks for confirmation "only where an action is destructive
or hard to reverse". No-show and Done both end a ticket, but they differ in practice:

- **No-show** is used rarely, and hitting it by mistake removes a patient who may be walking down the
  corridor. So it asks, in words that say what happens.
- **Done** is the room's everyday last step. Asking every time would teach people to click through the
  question, which makes the one question that matters worthless.
- **Undo call** needs no question: it is itself the correction.

**The refusal message names the likely cause, not the rule.** A nurse moved off a room while their page
was open gets `404`, the same answer as another clinic's queue, so it confirms nothing. The page turns
that into "This queue or patient is no longer yours to act on (a room change, or they moved on), so
nothing was changed." It does not guess further, and it never shows a success it did not get.

**Counting time without trusting the tablet's clock.** Each set of cards says when the server read
them. The page keeps the largest server-minus-device gap it has seen: each observation underestimates
the gap by the time the answer took. It counts from there. The first version counted from when the page
first saw the cards, so a consultation 12 minutes and 1 second old showed "11 min". The browser run
caught this, and it is fixed.

**Deviations from the issue's file list:** the controls are macros in `dashboard/_queue_card.html`,
used by the card partial (`_board_cards.html`) and the room partial (`_room_cards.html`), rather than
a whole-card template. The front desk's card also carries Issue 49's times and Issue 52's line.

## Changes

- **New:**
  - `src/modules/queue/request_keys.py`, `src/database/models/queue_request_key.py`,
    `alembic/versions/0027_queue_request_keys.py`;
  - `src/web/dashboard/actions.py`;
  - `src/templates/dashboard/_queue_card.html`, `src/templates/dashboard/_room_cards.html`;
  - `src/static/js/dashboard-actions.js`;
  - `tests/integration/dashboard/test_dashboard_actions.py`.
- **`src/modules/queue/lifecycle.py`:** `called → waiting`, `DEDICATED_MOVES` entry,
  `UndoWindowClosedError`, `undo_window_ends()`, `undo_call()`, `called_at` cleared on the move back.
- **`src/modules/queue/router.py`:** the `Idempotency-Key` header on call-next and transitions, and
  the undo-call route.
- **`src/core/config.py`**, **`.env.example`:** `QUEUE_CALL_UNDO_SECONDS`.
  **`src/core/scheduler.py`:** the request key sweep. **`src/core/live_events.py`:** `accept`, and a
  heartbeat timed from the last thing sent.
- **Web:**
  - `src/web/dashboard/board.py` (ticket id, status, elapsed, buttons and undo deadline; next number);
  - `src/web/dashboard/room.py` (reuses the card's tickets);
  - `src/web/dashboard/routes.py` (action grants, room cards and stream, one stream helper);
  - `board.html`, `room.html`, `_board_cards.html`;
  - `dashboard-live.js`, `dashboard-api.js`, `dashboard.css`.
- **`contracts/queue.yaml`**, **`docs/PRODUCT/03-booking-and-queue.md`**,
  **`docs/PRODUCT/05-clinic-dashboard.md`**.
- **Tests updated:**
  - `tests/unit/queue/test_ticket_state_machine.py` (13 legal moves; back to waiting only from
    called);
  - `tests/integration/queue/test_ticket_lifecycle.py` (33 refused, 24 dedicated; a done ticket asked
    for waiting is dedicated);
  - `tests/unit/queue/test_ticket_states_property.py` (the undo rule);
  - `tests/integration/queue/test_queue_concurrency.py` (the same-key race);
  - `tests/unit/platform/test_live_events.py` (the filtered stream);
  - `tests/integration/security/test_cross_tenant.py` (`queuerequestkey` pending, with its reason).
- **Docs:** `docs/GITHUB/MILESTONES/M7_clinic_dashboard.md`, `README.md`, `docs/GITHUB/README.md`,
  `docs/TEAM/WORKLOAD_SPLIT.md`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (260 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL and Redis:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  1940 passed, 1 skipped, 9 xfailed, 58 warnings in 202.36s
  ```

- [x] The new tests:

  ```text
  tests/integration/dashboard/test_dashboard_actions.py::test_a_double_tap_on_call_next_calls_exactly_one_patient
  tests/integration/dashboard/test_dashboard_actions.py::test_a_key_reused_for_another_action_is_refused_and_moves_nothing
  tests/integration/dashboard/test_dashboard_actions.py::test_a_press_that_failed_keeps_no_key_so_pressing_again_really_tries
  tests/integration/dashboard/test_dashboard_actions.py::test_a_double_tap_on_a_status_button_moves_the_ticket_once
  tests/integration/dashboard/test_dashboard_actions.py::test_an_undone_call_puts_the_patient_back_in_their_place_with_both_steps_audited
  tests/integration/dashboard/test_dashboard_actions.py::test_an_undo_after_the_window_or_once_the_patient_is_seen_is_refused_and_changes_nothing
  tests/integration/dashboard/test_dashboard_actions.py::test_a_nurse_calls_and_undoes_only_in_their_own_room
  tests/integration/dashboard/test_dashboard_actions.py::test_the_cards_offer_the_lifecycles_buttons_with_time_at_each_step_and_the_undo_window
  tests/integration/dashboard/test_dashboard_actions.py::test_the_buttons_are_offered_to_everyone_and_enabled_only_with_the_grant
  tests/integration/dashboard/test_dashboard_actions.py::test_the_room_is_live_on_its_own_cards_and_stream
  tests/integration/dashboard/test_dashboard_actions.py::test_request_keys_older_than_a_day_are_swept
  tests/integration/queue/test_queue_concurrency.py::test_one_press_sent_twice_at_the_same_instant_calls_one_patient   (PostgreSQL)
  tests/unit/platform/test_live_events.py::test_a_filtered_stream_skips_other_queues_and_still_beats_on_time
  13 passed
  ```

  The PostgreSQL race sends one key from four threads released together, ten rounds. Each round every
  thread gets the same ticket, and the queue ends with ten called tickets and ten key rows.
- [x] **In a browser** (Playwright's Chromium against a local PostgreSQL database; front desk at
  1366×768, the room at a 1280×800 touch tablet):

  ```text
  Double tap: two clicks on Triage's Call next 40 ms apart
    POST requests sent: 1          called tickets in Triage: 0 -> 1        status line: "Called T001."
  Same Idempotency-Key sent twice at once from the page: [200 T002] and [200 T002]
  Optimistic update on a slow connection (1.5 s latency), 300 ms after the press, before any answer:
    waiting 30 -> 29, "Calling A001…" in the panel, button "Calling…", status line "Sending…"
    after the server answered: waiting 29, A001 with staff
  Undo: button "Undo call (25 s)"; pressed ->
    A001 waiting, called_at null; Call next shows A001 again
    audit: "A001: waiting → called (call next) | A001: called → waiting (undo call)"
    status line: "A001 is back in the line, in the same place."
  Second device (the manager's front desk, live): A presses Call next on Immunisation, 5 times;
    click on A to B's card changing: 320, 369, 341, 357, 340 ms
  No-show asks first: "Mark T003 as a no-show? Their ticket ends, and they would have to join again."
  In-room clock: T001 started 12 minutes earlier -> "T001 Being seen 12 min"
  Rollback: the nurse's room is open on Triage; the manager takes Triage off her; on a slow connection she
    presses Call next -> 404 -> waiting 25 before and 25 after, no pending row left, button enabled,
    "This queue or patient is no longer yours to act on (a room change, or they moved on), so nothing
    was changed."; the next refresh shows she has no room here
  Room live: "Live"; a patient being seen offers only "Done"; touch targets 48 px (Call next 56 px)
  ```

### Screenshots

The front desk at 1366×768 right after two calls, with the undo countdown:

![Front desk with undo windows](https://github.com/Billykat7/clinicQ/blob/d5307ec54868a55acc74b85f982bba5cd8d4194a/docs/GITHUB/PR/M7/assets/pr50/board-undo-window.png?raw=true)

A card on a slow connection, before the server has answered (optimistic):

![Calling A001, optimistic](https://github.com/Billykat7/clinicQ/blob/d5307ec54868a55acc74b85f982bba5cd8d4194a/docs/GITHUB/PR/M7/assets/pr50/call-next-optimistic.png?raw=true)

The patients with staff: being seen for 12 minutes (Done), called again (Start, No-show), called
(Start, Recall, No-show, Undo):

![Front desk with the patient buttons](https://github.com/Billykat7/clinicQ/blob/d5307ec54868a55acc74b85f982bba5cd8d4194a/docs/GITHUB/PR/M7/assets/pr50/board-actions.png?raw=true)

No-show asks first:

![No-show confirmation](https://github.com/Billykat7/clinicQ/blob/d5307ec54868a55acc74b85f982bba5cd8d4194a/docs/GITHUB/PR/M7/assets/pr50/no-show-confirm.png?raw=true)

A refusal rolled back with its reason, in a nurse's room on a tablet:

![Rollback with the reason](https://github.com/Billykat7/clinicQ/blob/d5307ec54868a55acc74b85f982bba5cd8d4194a/docs/GITHUB/PR/M7/assets/pr50/room-rollback.png?raw=true)

The room, live, after a call:

![Room on a tablet](https://github.com/Billykat7/clinicQ/blob/d5307ec54868a55acc74b85f982bba5cd8d4194a/docs/GITHUB/PR/M7/assets/pr50/room-tablet.png?raw=true)

## Acceptance criteria

- [ ] Call Next updates the patient's phone and the waiting-room board within 2 seconds. **Partly
      met.** Staff screens are measured: a second device's live front desk changed 320–369 ms after
      the click, end to end. The waiting-room board is Issue 57 and the patient's ticket page Issue 68;
      neither exists yet. Every call is already published after its commit with the called number
      (Issue 49's `ticket.called`), which is what both will listen to. The 2-second measurement on
      those two screens is recorded as open for when #57 lands (see Known limits).
- [x] A double tap issues exactly one call, proven by a test: same-key tests over HTTP, a four-thread
      PostgreSQL race, and one request from a real double click
- [x] A server rejection rolls the optimistic update back and explains why: the card is restored and
      says why (browser, screenshot)
- [x] Undo-last-call is available for a short window and is audited: 30 seconds by default, two audit
      rows, refused after the window or once the patient is seen (tests, browser)
- [x] A nurse can only call from a queue they are assigned to: call and undo in another room answer
      `404` (test); a room taken away mid-shift is refused and rolled back (browser)
- [x] The in-room panel shows elapsed consultation time: *Being seen N min* from the start of the
      consultation, counting live (test, browser)

## Risk and rollback

**Migration `0027_queue_request_keys`** creates `clinicq.queue_request_key` with its unique key and an
index. It is additive, and its downgrade drops the table: retries sent before a downgrade then count
as new requests.

**The lifecycle table gains `called → waiting`.** The transitions route refuses it, so no existing
client can make it. Consumers of the table (the diagram, the state-machine and property tests) are
updated. Reports that count calls still see the call in the audit trail. The ticket's `called_at` is
cleared, so an undone call does not count toward today's average wait.

**The header is optional.** API clients that do not send `Idempotency-Key` behave exactly as before.

Rollback is a revert of this PR and a downgrade of `0027`.

**Known limits:**

- The 2-second measurement to the patient's phone and the waiting-room board waits for Issues 57 and
  68. Issue 55's end-to-end suite is the place to automate it once #57 is merged.
- Undo is available to anyone with `queues.call` on the queue, not only the person who called. Two
  receptionists at one desk share mistakes, and the audit trail names who undid.
- The offline queue, exponential backoff and a reconnect replay of pressed actions are Issue 55's. The
  request keys here are what will make that replay safe.

Closes #50
