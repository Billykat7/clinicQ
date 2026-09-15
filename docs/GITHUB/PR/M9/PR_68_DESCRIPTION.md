# PR: A patient follows their ticket live on a page reached by an unguessable link (Issue 68 / M9-68)

**Milestone:** [Milestone 9: Notifications & Patient PWA](https://github.com/Billykat7/clinicQ/milestone/9) ·
**Issue:** [#68](https://github.com/Billykat7/clinicQ/issues/68) · **Builds on:** #40 (join), #42 (wait
estimates), #57 (the live stream's envelope) and #63 (PR #186), all merged · **Unblocks:** #69 (PWA shell),
#70 (QR ticket code)

This is the screen a patient looks at while deciding whether to leave the house. It has one job: answer "how
much longer?" honestly and live, and keep cancelling within reach. With this PR:

- **A page for every ticket, at `/t/{token}`.** The token is 43 characters from 256 random bits, never an id.
  A patient can share it with whoever is driving them.
- **It updates without a refresh.** The page follows a `ticket.state` stream in the waiting-room board's
  envelope, and polls while the stream is down. It always shows how old its data is, and says **"Not live"**
  once updates stop.
- **"You are next" and "please come in now" take over the top of the screen** in a colour nothing else on the
  page uses. The browser tab says it too, and the phone vibrates where the browser allows it.
- **The wait is always a range**, counted down between updates, and ends at "any moment now" rather than 0.
- **The ticket's own patient cancels in two taps.** A phone that was only sent the link is not offered
  cancellation.
- **Directions and a tap-to-call button** for the clinic.

**Not done here, and not claimed:**

- **Nobody has used it on a real phone on a real 3G connection.** The Slow 3G numbers below come from
  Chromium's throttling, against the local development server, which does not compress.
- **The vibration was not observed.** Headless Chromium has no vibration motor, so only the screen changes
  are proven.

## Summary

- **The link** (migration `0032`, `ticket.page_token`, unique):
  - `sequence.new_page_token()` is `secrets.token_urlsafe(32)`, set on every issued ticket;
  - the migration backfills tickets that are still in their day, and finished tickets keep `NULL`;
  - `JoinOut.page_url` and `MyTicketOut.page_url` carry it, so a patient who closed the page finds it again.
- **The data** (`src/modules/queue/ticket_page.py`, new; `GET /api/v1/tickets/{page_token}`, no sign-in,
  `Cache-Control: no-store`):
  - `find_by_page_token` looks up only a string of the token's exact shape;
  - `page_state` derives, on every read: `headline` (`TicketPageHeadline`: waiting, next, called,
    in_progress, done, cancelled, missed, transferred), `position`, `waiting_ahead`, `wait` (the #42 range),
    the clinic's address, `call_url` and `directions_url`, `queue.room`, `as_of`, `refresh_seconds` (15),
    `stale_after_seconds` (45), `stream_url`, `cancel_url` (only for the ticket's own signed-in patient
    while waiting) and `next_page_url` (the new ticket after a transfer);
  - no name, phone number, reason or ticket id is in it.
- **The page and stream** (`src/web/ticket.py`, new):
  - `/t/{token}` renders `queue/ticket.html` with its first state embedded as a JSON data block, so the
    number shows before any script loads. An unknown link gets `queue/ticket_missing.html` with a 404.
  - `/t/{token}/stream` sends `ticket.state` first, then again after every change in the ticket's own queue,
    with a heartbeat every 15 s. It ends once the ticket is finished.
- **A stream budget for patients** (`src/core/live_events.py`): a second broker, `patient_broker`, holds up to
  `MAX_PATIENT_STREAMS_PER_SITE` (500) streams per clinic.
  - Every change goes to both brokers, and through the Redis fan-out to both on every instance.
  - A waiting room full of phones cannot take a stream from the board's 100. Beyond 500 a ticket stream
    answers `503` with `Retry-After`, and the page polls instead.
- **The browser code** (`src/static/js/ticket.js`, `src/static/css/ticket.css`, both new; external files
  for the CSP):
  - every sentence lives in the template, and the script only chooses which block shows and fills in the
    values;
  - it runs the stream with a polling fallback, a once-a-second age and stale check, the wait countdown, and
    the title and vibration for the urgent headlines;
  - sharing uses `navigator.share`, with a copy-link fallback;
  - cancelling is a confirm panel that posts to `cancel_url` with the CSRF header.
- **An optional patient session** (`patients/sessions.py::signed_in_patient_id`): the same checks as
  `get_current_patient`, from the cookie or a bearer token, but it answers "nobody" instead of `401`.
- **Contract:** `contracts/queue.yaml` documents `GET /tickets/{page_token}` (200 and 404, with examples),
  `TicketPageOut`, `TicketPageHeadline`, and `page_url` on the join and my-tickets answers.

## Design notes

**Following is not owning.** The link is a capability to *follow*, because sharing it is the point.
Cancelling needs the ticket's own patient session, which a family member does not have. `cancel_url` is set
only for that patient, and the existing cancel route checks ownership again (another patient gets its 404).

**Why the token is stored, not hashed.** A hashed token could not be shown to the patient again: they close
the tab and come back through *My tickets*. It opens nothing beyond what the page shows (a number, a clinic
and a place in line), and anyone who can read the `ticket` table already sees more. The address is 256 bits,
compared by equality through a unique index, and the lookup refuses anything not shaped like a token. The site
already sends `Referrer-Policy: strict-origin-when-cross-origin`, so the path does not leave in a `Referer`
header, and the directions link adds `rel="noreferrer"`.

**Found on the way: a released savepoint published domain events before the commit.** SQLAlchemy fires
`after_commit` when a savepoint is *released*, while the real transaction is still open.
`domain_events._drain` published everything queued at that moment. Since #63 writes its ledger row in a
savepoint inside every call, every call now published its `QueueChanged` early. The browser test caught the
effect: the ticket stream read the ticket before the call committed and stayed on "you are next". The same
bug could hand a post-commit notification to its worker before its row was committed. `_drain` now does
nothing inside a nested transaction. The new test fails on `main`:

```text
E       AssertionError: assert ['the move'] == []
E         Left contains one more item: 'the move'
```

**Guards updated, with reasons:**

- `test_api_route_gates` lists `GET /api/v1/tickets/{page_token}` as public and says why;
- `test_site_scoped_queries` names `find_by_page_token`, `page_state` and `_next_leg`: a token holder has no
  clinic role, as a paired kiosk box has none;
- the queue contract test requires only a 404 for this route, since there is no sign-in to lack.

**The look** is the landing page's phone mock-up: the number is the biggest text, the wait sits in a boxed
range, and the tokens come from `site.css`, so the dark theme works unchanged. "You are next" uses
`--warn-ink`, which nothing else on the page uses (the browser test checks), and "come in now" uses the brand
green.

## Changes

- **New:**
  - `src/modules/queue/ticket_page.py`, `src/web/ticket.py`
  - `src/templates/queue/ticket.html`, `ticket_missing.html`
  - `src/static/js/ticket.js`, `src/static/css/ticket.css`
  - `alembic/versions/0032_ticket_page_token.py`
- **Changed:**
  - `ticket.py` (model), `sequence.py`, `schemas.py`, `router.py` (the route, and `page_url`)
  - `patients/sessions.py`, `live_events.py` (patient broker), `domain_events.py` (the release fix)
  - `enums.py` (`TicketPageHeadline`, `LiveEventType.TICKET_STATE`), `main.py`
  - `contracts/queue.yaml`
- **Tests, new:**
  - `tests/integration/queue/test_ticket_page.py`: 10 tests of the data contract and the stream
  - `tests/e2e/patient/` (conftest and 5 browser tests)
  - the savepoint-release case in `tests/unit/sites/test_domain_events.py`
- **Tests, updated:** `test_api_route_gates.py`, `test_site_scoped_queries.py`, `test_queue_contract.py`.
- **Docs:** the M9 status row and progress bars (`--assume-closed 68`), the README Status block (64 of 109),
  and sprint 7's row in `WORKLOAD_SPLIT.md` (ticket page delivered).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (286 files).
- [x] `TZ=UTC pytest tests/ -n auto` against the Docker PostgreSQL 18 and Redis, with
  `REQUIRE_BROWSER_TESTS=1`: **2122 passed, 1 skipped, 9 xfailed, 0 failed**.
- [x] The template punctuation check over `src/templates/queue` finds nothing.
- [x] **The data contract** (`tests/integration/queue/test_ticket_page.py`, 10 passed), reading JSON and
  events, never HTML:
  - `test_the_link_is_a_long_unguessable_token_and_nothing_else_opens_the_page`: the id, the number and
    the reference code (with and without its dash) each get the same `404 No such ticket.`, and so does a
    token with its last character changed;
  - `test_position_and_wait_follow_the_queue_and_the_headlines_say_what_to_do`: 3rd, then 2nd, then
    `next`, then `called`;
  - `test_only_the_tickets_own_patient_is_offered_cancellation_and_it_works`: a stranger and another
    patient get `cancel_url: null`, and another patient posting to it gets 404;
  - `test_the_stream_opens_with_the_ticket_and_sends_it_again_after_each_change_in_its_queue`: an event
    in another queue is not sent;
  - plus a finished ticket's stream ending, a transfer's `next_page_url`, the 503 budget and the page
    data naming nobody.
- [x] **In a browser** (`tests/e2e/patient`, 5 passed, run twice):

  ```text
  position 3→2 in 0.06 s; 2→next in 0.06 s; one navigation
  router dead: 'Not live', 'Not live: this is how things stood 50 s ago. Trying to reconnect.'
  ```

  The stale test cuts a TCP relay and moves the page's own clock 50 s on. The numbers stay, "Live" becomes
  "Not live", and the warning clears after the relay returns and a poll lands.
- [x] **How to verify, step 1, by hand**, on the development server against a migrated database:
  - a separate process called the three patients ahead, and the open page went from "number 4" to
    "You are next" without a reload;
  - the tab title became `You are next · T004`;
  - then "Please come in now: Room 2".

  This goes through the Redis fan-out, the way a second instance would.
- [x] **How to verify, step 3: 320 px on Chromium's "Slow 3G" profile** (2 s latency, 400 kbit/s, cache
  off), against `uvicorn` without compression. Production's nginx gzips CSS and JS, so these are the worst
  case:

  | | light | dark |
  |---|---|---|
  | Time to first byte | 907 ms | 95 ms |
  | First contentful paint (the number is on screen) | 6.2 s | 6.0 s |
  | Every script loaded and the stream live | 9.7 s | 9.6 s |
  | Bytes transferred | 195 KB | 195 KB |
  | Wider than the screen | no | no |

  Once the page is live, the change after a call shows within 0.02 s of the calling process exiting. The
  light run's first byte includes the server's first request after starting.

  | Waiting, light | You are next, light | Come in now, light |
  |---|---|---|
  | ![Ticket page at 320 px on Slow 3G, light theme, waiting, number 4 in line with a wait range](https://github.com/Billykat7/clinicQ/blob/a4d64342160b16a3d2d89f9b2323931be0d971d9/docs/GITHUB/PR/M9/assets/pr68/320-slow3g-light-waiting.png?raw=true) | ![You are next alert filling the top of a 320 px screen](https://github.com/Billykat7/clinicQ/blob/a4d64342160b16a3d2d89f9b2323931be0d971d9/docs/GITHUB/PR/M9/assets/pr68/320-slow3g-light-you-are-next.png?raw=true) | ![Please come in now alert naming the room](https://github.com/Billykat7/clinicQ/blob/a4d64342160b16a3d2d89f9b2323931be0d971d9/docs/GITHUB/PR/M9/assets/pr68/320-slow3g-light-come-in-now.png?raw=true) |

  | Waiting, dark | You are next, dark | Come in now, dark |
  |---|---|---|
  | ![Ticket page at 320 px on Slow 3G, dark theme, waiting](https://github.com/Billykat7/clinicQ/blob/a4d64342160b16a3d2d89f9b2323931be0d971d9/docs/GITHUB/PR/M9/assets/pr68/320-slow3g-dark-waiting.png?raw=true) | ![You are next in the dark theme](https://github.com/Billykat7/clinicQ/blob/a4d64342160b16a3d2d89f9b2323931be0d971d9/docs/GITHUB/PR/M9/assets/pr68/320-slow3g-dark-you-are-next.png?raw=true) | ![Please come in now in the dark theme](https://github.com/Billykat7/clinicQ/blob/a4d64342160b16a3d2d89f9b2323931be0d971d9/docs/GITHUB/PR/M9/assets/pr68/320-slow3g-dark-come-in-now.png?raw=true) |

  | Updates stopped | Cancel, tap 1 | Cancelled | 320 px, called |
  |---|---|---|---|
  | ![Not live warning with the data's age, the last numbers kept](https://github.com/Billykat7/clinicQ/blob/a4d64342160b16a3d2d89f9b2323931be0d971d9/docs/GITHUB/PR/M9/assets/pr68/ticket-not-live.png?raw=true) | ![The confirmation panel after the first tap](https://github.com/Billykat7/clinicQ/blob/a4d64342160b16a3d2d89f9b2323931be0d971d9/docs/GITHUB/PR/M9/assets/pr68/ticket-cancel-confirm.png?raw=true) | ![The cancelled ticket with its confirmation](https://github.com/Billykat7/clinicQ/blob/a4d64342160b16a3d2d89f9b2323931be0d971d9/docs/GITHUB/PR/M9/assets/pr68/ticket-cancelled.png?raw=true) | ![Called state on a 320 px screen, signed in as the patient](https://github.com/Billykat7/clinicQ/blob/a4d64342160b16a3d2d89f9b2323931be0d971d9/docs/GITHUB/PR/M9/assets/pr68/ticket-320px-called.png?raw=true) |

## Acceptance criteria

- [x] **Position and estimate update live without a manual refresh:** the browser test (3rd, then 2nd, then
  next, then called, one navigation) and the manual run across processes. The polling fallback is exercised
  by the stale test's recovery.
- [x] **"You are next" is impossible to miss on a phone in a pocket-glance:**
  - the alert covers the top of the screen in a colour nothing else on the page uses (asserted: top under
    120 px, taller than 100 px, background not reused);
  - the tab title changes, shown in the screenshots.

  The vibration is coded but not observed, because headless Chromium cannot vibrate.
- [x] **Cancelling takes two taps and confirms clearly:** tap 1 opens the confirmation and tap 2 cancels,
  with `Ticket T002 is cancelled. …`, and the family's page follows to "cancelled".
- [x] **The page works on a 320 px screen and on a throttled 3G profile:**
  - nothing is wider than 320 px in the waiting, next and called states;
  - on Slow 3G the number is on screen at 6 s and the page is live at 9.7 s, uncompressed.

  Not tried on a physical phone.
- [x] **The ticket link is a long unguessable token, not a sequential id:** 43 characters, 256 random bits,
  unrelated between consecutive tickets. Id, number and reference code all 404.
- [x] **The page shows how stale its data is whenever updates stop:** "Updated 08:41:05 · 12 s ago" is always
  there, and "Not live: this is how things stood 50 s ago" appears once 45 s pass without a state or a
  heartbeat.

## Risk and rollback

- **The savepoint-release fix changes when every domain event is published:** at the outermost commit, as
  the bus always said, rather than possibly earlier. Nothing can legitimately depend on hearing about an
  uncommitted change. The full suite, including the board and dashboard stream tests, is green.
- **A new public route.** It reads one ticket by an exact 43-character token and returns no patient data.
  A single guess finds a ticket with a probability of about one in 2^256 divided by the tickets on file.
- **Migration `0032`** adds a nullable column with a unique constraint and backfills today's active tickets.
  The previous release ignores the column.
- **Rollback** is a revert and a downgrade to `0031`. Shared links then 404, and nothing else changes.

Closes #68
