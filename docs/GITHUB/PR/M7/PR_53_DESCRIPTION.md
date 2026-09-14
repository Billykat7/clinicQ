# PR: A nurse's room: only their queues, big buttons and encrypted private visit notes (Issue 53 / M7-53)

**Milestone:** [Milestone 7: Clinic Dashboard](https://github.com/Billykat7/clinicQ/milestone/7) ·
**Issue:** [#53](https://github.com/Billykat7/clinicQ/issues/53) · **Builds on:** #48 (the shell, PR
#170), #49 (the board read, PR #173), #28 (room assignment) and #45 (transfers), all merged

A nurse or doctor now has **My room**. It shows only the queues they are assigned to, a large **Call
next**, and **Start**, **Done** and **Transfer** for each patient with them. Beside each patient is a
short private **visit note**. Every button calls the queue engine's API; the page decides nothing.

The note is health information, so it is:

- **stored encrypted** (`EncryptedString`, privacy non-negotiable 4);
- **attributed and timestamped**;
- **audited without its words**;
- **removed by a nightly retention sweep**;
- **never part of any board, display or patient-facing response**, which a test proves by writing one
  and reading all of them.

**This PR also closes a hole it found.** Before this change, a nurse whose grant on the queue routes
reaches only `own` could call next in **any** queue at their clinic. A probe against main answered `200`
for Room 2's nurse calling in the Pharmacy. The site guard only checked the clinic. It now narrows an
`own` grant on a queue-shaped resource to the caller's rooms, so another room's calls answer the same
`404` as another clinic's.

## Summary

- **Room-scoped access, in the kernel** (`src/core/site_scope.py`):
  - `SiteAccess` gains `queue_ids`.
  - `require_site_access` sets `queue_ids` with `own_queue_scope()` when the resource's manifest shape
    is `QUEUE` and the caller's tier at this clinic does not reach `assigned`.
  - `scoped_select` narrows `Queue` by id, and every model with a `queue_id` by that column. Every
    service that already builds its queries through the guard is narrowed without being edited.
  - `SiteAccess.whole_site()` drops the narrowing where a room-scoped caller legitimately names another
    queue at the clinic. Today that is only a transfer's destination (`src/modules/queue/router.py`).
  - Receptionists and managers are untouched, and so is a nurse at a clinic where they hold a wider role.
- **Visit notes** (`src/modules/visits/`, new):
  - **Model and migration:** `VisitNote` (`src/database/models/visit_note.py`, migration `0026`) holds the
    note under `EncryptedString`. It also stores the author's user id and name as written, `created_at`
    and `expires_at`, and the ticket, visit, queue, clinic and patient it belongs to. It has indexes on
    ticket, patient and expiry.
  - **Writing:** `add_note()` accepts 1–1,000 characters, stripped. It refuses a patient who has not been
    seen (waiting, cancelled or no-show: `409 ticket.note.not_seen`) and sets
    `expires_at = now + VISIT_NOTE_RETENTION_DAYS` (default 30, 1–90). It writes an audit row
    (`visit_note`, `create`) with no note text.
  - **Reading:** `read_notes()` returns this visit's notes, oldest first, and notes from the same
    patient's earlier visits at this clinic **only while the patient holds the new
    `visit_note_history` consent**. Otherwise it gives the reason: not shared, or a walk-in with no
    record. Expired notes are never read.
  - **Purging:** `purge_expired_notes()` deletes expired notes and writes one audit receipt with the
    count. The scheduler runs it nightly at `VISIT_NOTE_RETENTION_SWEEP_HOUR` (default 02:00), under
    its own advisory lock so one instance sweeps.
  - **API:** `GET` and `POST /api/v1/sites/{site_id}/tickets/{ticket_id}/notes`, gated on `visits.notes`
    (read and update). Nurse/doctor holds it at `own` with the `QUEUE` shape, so the route is
    room-scoped through the change above. The front desk and the manager are refused (`403`).
- **The room page** (`src/web/dashboard/room.py`, new; `room.html`):
  - `read_room()` reads the clinician's queues through the narrowed access: Issue 49's cards, the
    patients with them now, and each patient's notes.
  - *Start* and *Done* appear only when the lifecycle's own table allows them (`is_legal`).
  - *Transfer* offers every other open queue at the clinic, with Issue 45's reasons in plain words
    (`TRANSFER_REASON_LABELS`, new).
- **Built for a 10-inch tablet:** every control is 48 px or taller (Call next is 56 px), there is no
  horizontal scroll in landscape or portrait, and the notes box is full width. Transfer and earlier
  visits fold away behind a chevron.
- **Consent wording:** `visit_note_history` joins the consent purposes, with web and USSD wording in
  `consent_text.py` (version `2026-09-v2-draft`).
- **Contract:** `contracts/queue.yaml` documents both notes operations (`401`, `403`, `404`, `409` with
  `ticket.note.not_seen`, `422`) and `VisitNoteIn`, `VisitNoteOut` and `VisitNotesOut`. The drift test
  holds.
- **Tidy-up:** `dashboard-settings.js` is renamed `dashboard-api.js`. The room's forms use the same
  declarative `data-api` wiring as Issue 54's settings, so one name now fits both pages.
- **Docs:** the dashboard product doc (the room view, and the data table row brought in line with the
  model), the M7 Status row, the README Status block, sprint 8's row and the generated bars. The RBAC
  snapshot and matrix, and `.env.example`, are regenerated.

## Design notes

**The narrowing lives in the guard, not in the room page.** The issue's first check is "Room 3 is not
visible *and its API calls are refused*". Hiding Room 3 on the page was never the hard part. Every queue
route (call next, peek, transitions, issue a walk-in, notes) must refuse it. Putting the rooms into the
`SiteAccess` the guard already hands every service means `scoped_select` applies them everywhere. The
refusal is the site guard's `404`, with the same body as a clinic that does not exist, so it confirms
nothing about the other room. The test runs six different calls on the Pharmacy as Room 2's nurse and
expects that `404` from each.

**Why `own` becomes "my rooms".** The RBAC manifests (Issue 19) give each resource a scope shape. For
`QUEUE`-shaped resources the `own` tier already meant "queues I am assigned to" on paper
(`permitted_queue_ids`, Issue 28), but only the navigation used it. This PR makes that meaning apply
to the data as well. A grant at `assigned` or `business` (the front desk, the manager) keeps the whole
clinic.

**Notes are not a projection someone must remember to strip.** No board, display, discovery or patient
schema has a notes field, and no domain event is published for a note. The only reader is a route gated
on `visits.notes`. The leak test writes a note containing a marker word on a patient who joined from
their phone. It then reads the front-desk page, the board cards, the clinic tickets and visits APIs, the
patient's own tickets and record, and the public clinic profile, and finds the word in none of them. It
also checks that no public or patient schema in OpenAPI can carry note text.

**Retention now, the general purge later.** The issue puts the generic purge in Issue 95, but asks for
notes to be "purged on the retention schedule" here. So this PR adds a sweep for notes only. Every note
carries its own `expires_at`, and the job deletes by that date. When Issue 95 lands the data map and the
shared purge runner, it can adopt `purge_expired_notes()` or replace the job; the `expires_at` column is
already what it needs. The default of 30 days is set in config, not the database, and a note's expiry is
fixed when it is written, so shortening the window does not retroactively hide stored notes before their
sweep. Issue 95 decides whether it should.

**Earlier visits need their own consent.** A note from last month is shown to today's clinician only if
the patient agreed to that, as a separate purpose. It is not implied by consenting to join a queue. A
walk-in with no patient record simply has no history, and the page says so rather than showing an empty
list.

**Transfer keeps the visit.** The room uses Issue 45's route, and the test checks that the visit row and
its notes are the same after the patient moves on.

**Deviations from the issue's file list:** the service, schemas and route live in `src/modules/visits/`
beside the visits manifest; `src/web/dashboard/room.py` only builds the view.

## Changes

- **New:**
  - `src/database/models/visit_note.py`, `alembic/versions/0026_visit_notes.py`;
  - `src/modules/visits/notes.py`, `src/modules/visits/schemas.py`, `src/modules/visits/router.py`;
  - `src/web/dashboard/room.py`;
  - `tests/integration/dashboard/test_room_view.py`.
- **`src/core/site_scope.py`:** `queue_ids`, `whole_site()`, `own_queue_scope()`, and the narrowing in
  `require_site_access` and `scoped_select`.
- **`src/modules/queue/router.py`:** the transfer destination is looked up clinic-wide.
  **`src/modules/queue/transfer.py`:** `TRANSFER_REASON_LABELS`.
- **`src/commons/enums.py`:** `ConsentPurpose.VISIT_NOTE_HISTORY` and `AuditEntityType.VISIT_NOTE`.
  **`src/modules/patients/consent_text.py`:** its wording.
- **`src/core/config.py`**, **`.env.example`:** `VISIT_NOTE_RETENTION_DAYS` and
  `VISIT_NOTE_RETENTION_SWEEP_HOUR`. **`src/core/scheduler.py`:** the nightly sweep.
- **`src/api/v1/router.py`**, **`src/database/models/__init__.py`**, **`contracts/queue.yaml`**.
- **Web:** `src/web/dashboard/routes.py` (the room), `src/templates/dashboard/room.html`,
  `src/templates/dashboard/settings_base.html`, `src/static/css/dashboard.css`, and
  `src/static/js/dashboard-api.js` (renamed from `dashboard-settings.js`).
- **Tests:** `tests/integration/security/test_cross_tenant.py` (notes and visits cases, a nurse at
  clinic A), `tests/unit/queue/test_priority_labels.py` (transfer labels).
- **Docs:** `docs/PRODUCT/05-clinic-dashboard.md`, `docs/GITHUB/MILESTONES/M7_clinic_dashboard.md`,
  `README.md`, `docs/GITHUB/README.md`, `docs/TEAM/WORKLOAD_SPLIT.md`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (257 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL and Redis:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  1926 passed, 1 skipped, 9 xfailed, 49 warnings in 233.64s
  ```

- [x] The new tests:

  ```text
  tests/integration/dashboard/test_room_view.py::test_a_note_is_attributed_timestamped_encrypted_and_audited_without_its_words
  tests/integration/dashboard/test_room_view.py::test_a_note_on_a_patient_who_has_not_been_seen_is_refused
  tests/integration/dashboard/test_room_view.py::test_a_nurse_sees_only_their_rooms_and_another_rooms_calls_are_refused
  tests/integration/dashboard/test_room_view.py::test_a_note_never_appears_in_any_board_or_patient_facing_response
  tests/integration/dashboard/test_room_view.py::test_expired_notes_are_purged_by_the_nightly_sweep_and_the_receipt_counts_without_words
  tests/integration/dashboard/test_room_view.py::test_earlier_visits_notes_are_shown_only_while_the_patient_agrees
  tests/integration/dashboard/test_room_view.py::test_a_transfer_from_the_room_keeps_the_visit_and_its_notes
  tests/unit/queue/test_priority_labels.py::test_every_transfer_reason_has_a_label_and_no_label_is_a_wire_value
  8 passed
  ```

  The encryption test reads the raw column through a plain SQL query and finds ciphertext, not the words.
  The cross-tenant suite now covers the notes route and a room-scoped nurse at clinic A.
- [x] **In a browser** (Playwright's Chromium with touch, as a Triage nurse, against a local PostgreSQL
  database):

  ```text
  1280×800 landscape tablet:
    My room lists: Triage (only)
    Call next               POST …/queues/<triage>/tickets/call-next -> 200   T001 Demo is with the nurse
    Add note                POST …/tickets/<T001>/notes -> 201                 shown as
      "BP 150/95, dizzy on standing. Recheck Friday." nurse@clinicq.example, 14 Sep 15:53
    Start                   POST …/tickets/<T001>/transitions -> 200           badge: Being seen
    Earlier visits          "A walk-in without a patient record has no earlier visits to show."
    Touch targets (px):     Call next 56 · Done 48 · Transfer toggle 48 · Transfer 48 · Add note 48 · Earlier visits 48
    Horizontal scroll:      none
    Another room from this page: POST …/queues/<general consultation>/tickets/call-next -> 404 "Not found."
  800×1280 portrait tablet: horizontal scroll none
    Transfer to General consultation -> the room is empty; the patient left with their visit
  After the note was written:
    front desk board page contains the note:   no
    board cards fragment contains the note:    no
    clinic tickets API contains the note:      no
    receptionist GET …/tickets/<T001>/notes -> 403
  ```

### Screenshots (10-inch tablet)

Landscape, 1280×800: one room, a patient being seen, a note with its author and time, earlier visits
opened:

![Room view, tablet landscape](https://github.com/Billykat7/clinicQ/blob/cdf7b0bf733c3035aa778b5109bbd20685984b36/docs/GITHUB/PR/M7/assets/pr53/room-tablet-landscape.png?raw=true)

Portrait, 800×1280:

![Room view, tablet portrait](https://github.com/Billykat7/clinicQ/blob/cdf7b0bf733c3035aa778b5109bbd20685984b36/docs/GITHUB/PR/M7/assets/pr53/room-tablet-portrait.png?raw=true)

## Acceptance criteria

- [x] A nurse sees only their assigned queues and cannot act on another room: the page lists only their
      room, and six different calls on another room answer `404` (test); `404` from the page in a
      browser
- [x] Visit notes are never included in any board or patient-facing response, proven by a test: seven
      responses read after writing a note, and no public or patient schema can carry one
- [x] Notes are attributed to the author and timestamped: author id and name and `created_at` stored,
      shown on the page (test, browser)
- [x] Notes are purged on the retention schedule: `expires_at` per note, a nightly sweep that deletes
      expired notes and audits the count, registered with the scheduler (test)
- [x] The view is comfortably usable on a 10-inch tablet: 48 px+ targets, no horizontal scroll in either
      orientation (browser, screenshots)
- [x] Transfer from the room view preserves the visit record: same visit and notes after the transfer
      (test); the transfer empties the room in a browser

## Risk and rollback

**Migration `0026_visit_notes`** creates `clinicq.visit_note` and its three indexes. It is additive, and
its downgrade drops the table (and so any notes written). Apply it before the new image serves traffic.

**The room narrowing changes behaviour for `own`-tier queue grants.** The only built-in role at that tier
is nurse/doctor, and restricting them to their rooms is the intended rule. A custom role given `own` on
`queues.call` or `queues.tickets` is narrowed in the same way. A clinician with **no** room assignment
at a clinic now sees no queues there, where before they could act on all of them. Managers must assign
rooms (Issue 28's settings) before nurses use the room view.

**Encryption keys:** notes use the kernel's existing Fernet key, so no new secret is needed. Losing that
key makes the notes unreadable, as it already does for every other encrypted column.

Rollback is a revert of this PR and a downgrade of `0026`.

**Known limits:** the room page reloads after each action. Live updates on the room come with Issue 50's
call actions, which reuse Issue 49's stream. The retention sweep covers visit notes only. Issue 95's
data map and shared purge runner are still to come. The consent wording is marked draft until the
privacy review in M13.

Closes #53
