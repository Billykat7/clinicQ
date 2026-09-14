# Release v0.7.0: Clinic Dashboard

**Date:** 2026-09-14 · **Milestone:** M7 · **Issues closed:** 48–55

A pre-release. v0.6.0 built the queue engine with no screen on top of it; this release is **the screen
reception, nurses and the clinic manager run the day from**. It covers:

- a front desk that updates itself and says when it cannot;
- one large *Call next* that answers at once and never calls two patients;
- walk-in intake with a name and the Enter key, and a stub for a 58 mm printer;
- moving a visibly unwell patient forward, with a reason on the record;
- a room view for nurses and doctors, with encrypted private notes;
- the manager's settings;
- a dashboard that keeps working honestly when the clinic's internet does not.

The waiting-room display (M8) and the patient's ticket page (M9) are still to come, so a call is not
yet seen outside the staff screens (see *Known issues*).

Three rules shape the release, and each is enforced where a bug cannot get round it:

- **The page decides nothing.** Every button is a request to the API, which decides. The templates
  hold no role name and no status comparison, and a guard test fails on a role check in a template.
  A control the person may not use is disabled with the reason, never hidden.
- **The screen never lies about being current.** The line above the cards always says *Live*,
  *Reconnecting (attempt N)*, *Updating every 5 seconds* or *Offline*, with the data's age whenever it
  might be stale.
- **A press is done once or reported, never lost.** Every staff action carries an `Idempotency-Key`.
  An action the clinic cannot receive is held until it can be sent, or reported as not sent.

All eight pull requests merged on 14 September 2026, each after its checks were green and before the
next branched from `main`: #170 (48) → #171 (52) → #172 (54) → #173 (49) → #174 (53) → #175 (50) →
#176 (51) → #177 (55). The tag is cut from `main` after #177 merges.

## What shipped

- **The dashboard shell** (Issue 48, PR #170; migration `0025`).
  - Every clinic screen lives at `/dashboard/sites/{site_id}/…`, inside one frame: the clinic, its
    screens, and who is signed in.
  - The navigation comes from the nav registry, resolved with the roles the person holds **at that
    clinic**, so the same person can be a receptionist at one clinic and a manager at another.
  - Staff at two clinics switch between them without signing in again, and the board that opens is
    the new clinic's.
  - A signed-out visitor returns to the page after signing in. `g` plus a letter moves between
    screens, and `?` lists the keys.
  - `nav_gate_overrides` may now require the `assigned` tier, which separates the front desk (a
    clinic-wide grant) from a nurse (their own queues).
- **Reordering with a reason and a trail** (Issue 52, PR #171). Drag a patient up the waiting line, or
  press *Move forward* (the same action by touch and keyboard). A prompt asks for a reason from the
  closed list and an optional note, and nothing is saved without a reason. A moved patient carries a
  **Priority** badge that only staff screens show. Each card lists the queue's overrides of the day,
  and the manager's **Overrides** screen shows every one with counts per person, listed by name and
  never ranked.
- **The manager's settings** (Issue 54, PR #172).
  - Profile with a map pin, weekly hours and closures, queues, services, staff and room assignments,
    display mode and payment details. These are screens over the M4 APIs, with no new rules.
  - Each list follows the list-view pattern: filters, sortable headers and a slide-over record.
  - A change that removes something asks first, in words that say what happens, and the API audits it.
  - The display settings show a preview of the waiting-room board with the privacy warning.
  - Another clinic's settings answer 404.
- **The live front desk** (Issue 49, PR #173).
  - Every queue is a card: waiting and with staff, the average wait today, the longest wait now. A
    queue where someone has waited past `DASHBOARD_STUCK_WAIT_MINUTES` (45) turns red.
  - Changes arrive over server-sent events. `src/core/live_events.py` holds the envelope and broker
    Issue 57 will share. An event names what changed and carries no patient data; the page fetches
    its cards again through its own gate.
  - A change on one device reached another in 308–381 ms. The page falls back to polling without a
    reload.
  - Nothing on the page takes focus from someone typing.
- **The nurse's room and private visit notes** (Issue 53, PR #174; migration `0026`).
  - *My room* shows only the queues the clinician is assigned to, built for a 10-inch tablet.
  - Notes are stored with `EncryptedString`, attributed and timestamped, and kept for
    `VISIT_NOTE_RETENTION_DAYS` (30). A nightly sweep then deletes them and audits the count.
  - A note never appears in any board or patient-facing response, proven by a test that searches
    every one.
  - Notes from a patient's earlier visits show only with the new `visit_note_history` consent.
  - **This PR fixed a hole:** a nurse whose queue grant reaches only `own` could call next in any
    queue at the clinic. The site guard now narrows such a grant to the caller's rooms, so another
    room's calls answer 404.
- **Call next, recall, done, no-show and undo** (Issue 50, PR #175; migration `0027`).
  - Each card and room leads with *Call next* showing the number it will call. Beside each patient
    with staff are the buttons the lifecycle allows, with the time at their step counting live.
  - A press changes the card at once and is put back, with the server's reason, if refused.
  - **A double tap calls one patient.** The request key is recorded in the same transaction as the
    move, proven over HTTP and by a four-thread race on PostgreSQL.
  - A patient called by mistake can be put back **in the same place** within `QUEUE_CALL_UNDO_SECONDS`
    (30). This is a new, dedicated `called → waiting` move, and the audit trail keeps the call and its
    undoing.
  - *No-show* asks first; nothing else does.
  - The room became live, on its own stream filtered to its queues.
- **Walk-in intake and the ticket stub** (Issue 51, PR #176).
  - The *Walk-in* screen calls the join service, so a walk-in takes the next number in the shared
    sequence. The name field has focus and the queue last used is already chosen: keyboard-only
    intake took 0.42–2.53 s.
  - A phone number links the patient, and their answer to being messaged is recorded as their
    notifications consent.
  - The stub prints on a 58 mm roll and is the large on-screen number when there is no printer. It
    carries no name, phone or reason.
  - The desk's last walk-in can be undone for `QUEUE_WALK_IN_UNDO_SECONDS` (120), as an audited
    cancellation.
- **Offline, reconnect, sign-in again, and browser tests** (Issue 55, PR #177).
  - **Offline within 10 seconds.** Staff streams beat every 5 seconds (access is still re-checked
    every 15). A silence of 7 seconds asks `/health` with a 2-second limit. A server that stopped
    answering was reported Offline in 4.8–8.9 s; a browser that knows it is offline says so at once.
  - **Reconnecting** backs off exponentially with jitter, from 1 second, capped at 30. After three
    failed attempts the page polls, and it goes live again on its own.
  - **The outbox.** An action the clinic cannot receive is held with its key and shown above the cards.
    It is sent in order when the page is live again, or reported: *Not done* with the clinic's reason,
    or *Not sent* when it waited longer than `DASHBOARD_OUTBOX_EXPIRY_SECONDS` (120). It survives a
    reload, and closing the tab while one waits asks first.
  - **An expired session** asks the person to sign in again in place, then sends what they pressed.
    The walk-in form sends the same walk-in again with the same key.
  - **Playwright** is in `requirements.txt`. A fourth CI shard, `browser`, runs `tests/e2e` against a
    real server on PostgreSQL in about 30 seconds: Call next, walk-in, reorder, the offline and dead
    stream paths, the outbox and session expiry. `tests/e2e/conftest.py` holds the fixtures Issue 62
    reuses.

## Migrations

Three revisions, `0025` to `0027`, applied in order by `scripts/db/deploy-sequence.sh`. None rewrites
existing data on the way up, and each is reversible.

- **`0025_nav_gate_assigned_scope`**: widens the check on `nav_gate_overrides.scope` to allow
  `assigned`. The downgrade first moves any `assigned` row to `business`, so a surface then opens for
  fewer people, never more.
- **`0026_visit_notes`**: a new `visit_note` table (the note text is a Fernet token) with indexes on
  ticket, patient and expiry. **The downgrade drops every note.** Audit rows, which never held the
  words, remain.
- **`0027_queue_request_keys`**: a new `queue_request_key` table, unique per user and key, with an
  index for the hourly sweep. The downgrade drops it; a retry sent before the downgrade then counts as
  a new request.

## Upgrade notes

- **Six new settings**, all with defaults (`make env-example` regenerated `.env.example`):
  `DASHBOARD_STUCK_WAIT_MINUTES` (45), `VISIT_NOTE_RETENTION_DAYS` (30),
  `VISIT_NOTE_RETENTION_SWEEP_HOUR` (2), `QUEUE_CALL_UNDO_SECONDS` (30), `QUEUE_WALK_IN_UNDO_SECONDS`
  (120) and `DASHBOARD_OUTBOX_EXPIRY_SECONDS` (120). Run `make check-config` after copying.
- **Two new scheduler jobs:** `visit_note_retention_sweep`, nightly at the sweep hour under advisory
  lock 553, and `queue_request_key_sweep`, hourly under lock 554.
- **RBAC:** the new `visits` manifest (`visits.notes`, the nurse/doctor role at `own`) is synced by the
  deploy sequence's RBAC seed. It also writes the default `assigned` gate rows that `0025` allows.
- **A behaviour change for `own`-tier queue grants.** A caller whose grant on a queue-shaped resource
  reaches only `own` (the built-in nurse/doctor role, or any custom role written that way) now reaches
  only the queues they are assigned to. **A clinician with no room assignment at a clinic sees no
  queues there.** Managers should assign rooms (Settings → Staff) before nurses start using *My room*.
- **The lifecycle table gains `called → waiting`**, made only by the undo-call route. The transitions
  route refuses `to: waiting` with `409 ticket.transition.dedicated_route`, as it does for cancelled
  and transferred.
- **New consent purpose `visit_note_history`**, with wording version `2026-09-v2-draft`.
- **New routes**, all in `contracts/queue.yaml` or served as dashboard pages:
  - `POST …/tickets/{id}/undo-call`, `POST …/tickets/{id}/undo-walk-in`, and `GET`/`POST
    …/tickets/{id}/notes`;
  - the optional `Idempotency-Key` header on call-next, transitions and walk-ins, and
    `notifications_consent` on walk-ins;
  - the dashboard pages under `/dashboard/sites/{site_id}/` (board, room, walk-in, overrides,
    settings), with `/board/stream`, `/room/stream`, `/board/cards`, `/room/cards` and
    `/walk-in/recent`.

  API clients that send neither the key nor the new fields behave exactly as before.
- **Put the streams behind a proxy that does not buffer them.** The responses send
  `X-Accel-Buffering: no` and `Cache-Control: no-transform`. A proxy whose idle timeout is shorter
  than the 5-second heartbeat would cut the staff streams.
- **Playwright is a test-time dependency.** CI installs Chromium for the browser shard. The image build
  removes `playwright` and `pyee` with the other test tooling, so the image does not grow.

## Known issues

- **A call is not yet seen outside the staff screens.** "Call Next updates the patient's phone and the
  waiting-room board within 2 seconds" is met only for the staff screens (320–369 ms to a second
  device's front desk). The board (Issue 57) and the ticket page (Issue 68) do not exist yet. Every
  call is published after its commit, with its number, for them to listen to. The 2-second measurement
  on those screens is open until #57 merges.
- **The event broker is per process.** With several application instances behind a balancer, each
  hears only its own writes. Screens stay correct through the one-minute safety refresh, but can be up
  to a minute late until Issue 57 adds Redis fan-out.
- **Not tested on physical hardware.**
  - The ticket stub was checked in Chromium's print emulation and as a one-page 58 mm PDF, not on a
    thermal printer. Printable widths vary, and the stylesheet assumes about 48 mm.
  - The room view was checked at 10-inch tablet sizes in Chromium, not on a tablet.
- **No messages are sent.** Walk-in phone numbers and consents are recorded, but notifications,
  including an SMS of the ticket number, are M9.
- **Held actions live in one browser tab.** The outbox keeps them across a reload and asks before the
  tab closes, but actions held in a tab that is closed anyway, or on a device that loses power, are
  not sent. The next time the page loads, nothing says they were pressed.
- **Offline detection reads `/health` on the same host.** A proxy that answered `/health` itself while
  the application behind it was down would delay *Offline* until the stream's 12-second silence.
- **Undo is shared, and the walk-in list is personal.** Anyone with `queues.call` at the queue can undo
  a call in its window, not only the person who called; the audit trail names who undid it. The walk-in
  list shows the signed-in person's own walk-ins, so two receptionists sharing one sign-in share one
  list.
- **The notification bell answers 403 for clinic roles.** This is a v0.3.0 gate on the kernel's
  notification centre, found while building the shell and left to its own fix. The bell shows nothing
  for reception, nurses and managers; the dashboard's own screens are unaffected.
- **The retention sweep covers visit notes only.** The data map and the shared purge runner are Issue
  95's. The consent wording, including `visit_note_history`, is still a draft for the privacy review in
  M13.
- **Everything from v0.6.0's list that M7 did not touch still stands.** Queue texts still go to the
  logging provider, the estimator is still evaluated on synthetic data, and the rush budget is still
  provisional. The one-time-code store is still process-local, the credential in the repository's
  history is still not rotated, and nothing is provisioned. **Only `v0.2.0` has ever been tagged**:
  `v0.1.0` and `v0.3.0`–`v0.6.0` have release notes but no tags, and they should be cut in order
  before this one.

## Verification

Run on the Issue 55 branch with `main` merged in, which is `main` as #177 will leave it. It used
PostgreSQL 18 + PostGIS and Redis in Docker, both required rather than skippable, and Playwright's
Chromium:

```text
TZ=UTC pytest -q -n auto --dist loadscope tests
                                      1956 passed, 1 skipped (the rush), 9 xfailed in 184 s
tests/e2e (8 browser tests)           3 consecutive runs under -n auto: 8 passed each, 28–29 s
ruff check . / ruff format --check    clean
mypy src/                             clean (262 files)
```

CI ran the unit, integration and flow suites on every pull request, plus the browser shard on #177,
and each merged with every check green. Each pull request carries its own evidence from a browser
against a local PostgreSQL database, and it is worth reading beside the suite:

- **#170:** each role's menu at 1366×768, the clinic switch, and sign-in returning to the page.
- **#171:** drag and tap making the same move, a save refused without a reason, and the board with no
  badge for a role without the grant.
- **#172:** every settings tab, the display preview with its privacy warning, and another clinic's
  settings answering 404.
- **#173:** a change on a second device in 308–381 ms, focus kept while typing, the polling fallback,
  and ten queues with 200 tickets.
- **#174:** the tablet room in landscape and portrait, and the note absent from every board and
  patient response.
- **#175:** one request from a double click, the optimistic card on a slow connection, undo with its
  two audit rows, and the rollback with its reason.
- **#176:** four keyboard-only walk-ins timed, the stub on screen and in print emulation, and undo by
  Alt+U.
- **#177:** Offline within 10 seconds (at once, and 4.8–8.9 s for a frozen server), reconnecting and
  polling, actions held offline and sent once on return, one reported *Not sent*, and a session ended
  and finished after signing in again.
