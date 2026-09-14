# PR: Move a patient forward by drag or tap, with a reason, a staff-only badge and the trail (Issue 52 / M7-52)

**Milestone:** [Milestone 7: Clinic Dashboard](https://github.com/Billykat7/clinicQ/milestone/7) ·
**Issue:** [#52](https://github.com/Billykat7/clinicQ/issues/52) · **Builds on:** #46 (the priority
service, PR #168) and #48 (the dashboard shell, PR #170), both merged

This is the screen for Issue 46's priority override. Every card on the front desk now holds its queue's
waiting line in call order. A receptionist can drag a patient up the line, or press the **Move
forward** button beside them. Both open the same reason prompt, and saving it sends the same request.
A moved patient carries a **Priority** badge on staff screens only. The card lists the queue's overrides
of the day, and the clinic manager has an **Overrides** screen with every override and a count per
staff member.

**No rule is written again in JavaScript.** The prompt does not check the reason, the direction or who
may move whom. It sends what the person chose, and shows the server's answer when it refuses. A move
without a reason goes to the server with no reason and comes back `422`, so nothing is saved and the
line is unchanged. That is demonstrated below in a real browser, and by a test against the API.

## Summary

- **The waiting line** (`src/templates/dashboard/_reorder.html`, `queue_line`): every waiting ticket in
  `CALL_ORDER` with its place, number, the desk's name for a walk-in (or its channel), a drag handle and
  a round **Move forward** button, plus the staff-only **Priority** badge on a ticket moved today. Each
  line sits in a disclosure on its queue card, and the card also shows the queue's latest three
  overrides.
- **The reason prompt** (`move_dialog`): "Call before" (a select of the tickets ahead), the six reasons
  from `PriorityReason` in words, an optional note (up to `MAX_REORDER_NOTE_LENGTH`), Save and Cancel.
- **`src/static/js/dashboard-reorder.js`:** HTML5 drag and drop onto a ticket above, and the button,
  both call `openMove(ticket, callBefore)`. Save posts `POST /api/v1/sites/{site}/tickets/{ticket}/priority`
  with `{ahead_of_ticket_id, reason, note}`. A success reloads the board and reopens that line. A
  refusal shows the server's sentence in the prompt, and a missing reason is named from the `422`.
- **`src/web/dashboard/reorder.py`:** what the page shows, read through the site guard: `queue_lines()`
  (line, badges, card trail), `override_day()` (the manager's list and counts), `OverrideFilters`,
  `reason_choices()`. Overrides are read **only** when the caller's grant at the clinic reads
  `queues.tickets.priority`.
- **Disabled, not hidden** (`can_reorder`, `queues.tickets.priority:update` at the clinic): without it
  the handles, the buttons and `draggable` render switched off, with "Moving a patient forward needs the
  priority permission at this clinic." The prompt is not rendered, and the API still answers `403`.
- **The manager's Overrides screen** (`/dashboard/sites/{id}/overrides`, a new registry destination
  gated like the counts API on `sites.reports:read`, shortcut `g o`), following
  `docs/IDE/RULES/list-view-ui-pattern.mdc` in its server-rendered form:
  - a `GET` filter bar (day, queue, reason, staff member);
  - click-to-sort columns;
  - a row click opens the override, with its note, beside the list.

  The counts cover the whole day whatever the filters say, are listed by name, and carry
  `COUNTS_ARE_NOT_RANKINGS`. A day in the future reads as today.
- **`PRIORITY_REASON_LABELS`** in `src/modules/queue/priority.py`, next to the vocabulary, with a test
  that every reason has words.
- **Docs:** the dashboard product doc, the M7 Status row, the README Status block, sprint 8's row and the
  generated bars; the RBAC decision snapshot gains the `overrides` surface.

## Design notes

**One gesture function, one request, so "identical" holds by construction.** A drag and a tap differ
only in how the "call before" ticket is picked: the drop target, or the one just ahead (changeable in
the prompt). From there both run the same code and send the same body. The browser run below records
the bodies both gestures sent and the lines they left behind. The API test sends that body into two
identical queues and gets the same order and the same record.

**The page offers only forward moves; the server still decides.** The control is "move forward", so a
drag only highlights and accepts tickets *above* the dragged one, and the button is disabled on the
first ticket (there is nothing ahead to pick). That is what the control is, not a copy of Issue 46's
rule: a request for any other move still reaches the server and is refused there, as its own tests
show.

**Reload after a saved move instead of an optimistic re-order.** The line, the badge, the trail and the
places all change on a save. Re-reading the page shows the server's version of all four rather than a
guess at them. Issue 49 makes the board live, and a save will then arrive like any other change.

**Reading priority follows the grant, including by inheritance.** A custom trainee role holding only
`queues.tickets:read` inherits `read` on `queues.tickets.priority`, so it reads the badge and the trail
exactly as the trail API lets it (`200`), with every control disabled. Deny that read and the page stops
reading overrides for it altogether (no badge, no trail, and the trail API answers `403`). Both halves
are in one test.

**The public board never shows priority.** It is not merely hidden there; it cannot reach it:

- the waiting-room board's projection, `BoardEntry`, has exactly `ticket_number`, `name` and `comment`
  (Issue 46's test);
- no public or patient-facing API schema carries a priority field (the same test walks the OpenAPI);
- a patient who was moved forward reads their own ticket with no `priority`, `reorder`, reason or note
  in the response (new test);
- no template on the waiting-room layout imports the reorder partial or says "priority" (new test);
- the badge data is built only in a staff route behind the site guard and the priority grant.

**Staff are named as the audit names them** (the staff member's email, `QueueReorder.staff`), so the
trail, the counts and the audit log say the same thing.

**Files the issue listed:** `src/templates/dashboard/_reorder.html` and `src/static/js/dashboard-reorder.js`
are as listed. The view-model code went to `src/web/dashboard/reorder.py` rather than `actions.py`,
which Issue 50 uses for Call next and the other actions.

## Changes

- **New:** `src/web/dashboard/reorder.py`, `src/templates/dashboard/_reorder.html`,
  `src/templates/dashboard/overrides.html`, `src/static/js/dashboard-reorder.js`,
  `src/static/js/dashboard-overrides.js`, `tests/integration/dashboard/test_dashboard_reorder.py`,
  `tests/unit/queue/test_priority_labels.py`.
- **`src/web/dashboard/routes.py`:** the front desk reads the lines and the two grants; `GET
  /dashboard/sites/{id}/overrides`.
- **`src/core/nav_registry.py`:** the `overrides` destination; its rail icon in
  `partials/clinic_icons.html`.
- **`src/modules/queue/priority.py`:** `PRIORITY_REASON_LABELS`.
- **`src/templates/dashboard/board.html`, `src/static/css/dashboard.css`:** the line, the trail, the
  prompt and the overrides layout.
- **`tests/integration/dashboard/conftest.py`:** a `trainee` built from grants alone, and a `patient()`
  helper; the shell tests expect the manager's new screen.
- **Docs:** `docs/PRODUCT/05-clinic-dashboard.md`, `docs/GITHUB/MILESTONES/M7_clinic_dashboard.md`,
  `README.md`, `docs/GITHUB/README.md`, `docs/TEAM/WORKLOAD_SPLIT.md`, `tests/snapshots/rbac_decisions.txt`.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` clean (249 files)
- [x] Whole suite as CI runs it, in UTC with PostgreSQL and Redis:

  ```text
  $ TZ=UTC TEST_DATABASE_URL=… TEST_REDIS_URL=… pytest tests/ -q --no-cov -n auto --dist loadscope
  1896 passed, 1 skipped, 9 xfailed, 32 warnings in 155.36s
  ```

- [x] The new tests:

  ```text
  tests/integration/dashboard/test_dashboard_reorder.py::test_the_line_is_in_call_order_and_a_moved_ticket_has_its_badge_and_trail
  tests/integration/dashboard/test_dashboard_reorder.py::test_dragging_and_tapping_send_one_request_that_lands_identically
  tests/integration/dashboard/test_dashboard_reorder.py::test_a_move_without_a_reason_does_not_save_and_the_line_is_unchanged
  tests/integration/dashboard/test_dashboard_reorder.py::test_reordering_is_disabled_not_hidden_for_a_role_without_the_permission
  tests/integration/dashboard/test_dashboard_reorder.py::test_the_manager_sees_the_days_overrides_and_counts_by_name_with_filters
  tests/integration/dashboard/test_dashboard_reorder.py::test_priority_never_reaches_a_patients_ticket_or_a_board_template
  tests/unit/queue/test_priority_labels.py::test_every_priority_reason_has_a_label_and_no_label_is_a_wire_value
  tests/unit/security/test_nav_registry.py::test_every_clinic_destination_names_its_site_and_has_its_own_shortcut
  8 passed in 2.13s
  ```

- [x] **In a browser** (Playwright's Chromium at 1366×768 against a local PostgreSQL database with the
  demo clinics, 3/5/7/9 walk-ins in four queues, and a trainee role built from grants alone). Every
  request the page sent to the priority endpoint was recorded:

  ```text
  1. Triage, "Move forward" on the 3rd, Save with no reason chosen
     request body:  {"ahead_of_ticket_id": "…"}                        (no reason, as the prompt left it)
     prompt says:   Choose a reason. A move without one is not saved.  (the prompt stays open)
     line unchanged: True
  2. Chronic medication collection, DRAG the 5th onto the 2nd
     prompt "Call before" preselected: 2. C002; reason Elderly; Save
     request body:  {"ahead_of_ticket_id": "…", "reason": "elderly"}
     line before:   C001 C002 C003 C004 C005 C006 C007
     line after:    C001 C005 C002 C003 C004 C006 C007
  3. Immunisation, TAP "Move forward" on the 5th
     prompt "Call before" preselected: 4. I004; picked the 2nd; reason Elderly; Save
     request body:  {"ahead_of_ticket_id": "…", "reason": "elderly"}
     line before:   I001 I002 I003 I004 I005 I006 I007 I008 I009
     line after:    I001 I005 I002 I003 I004 I006 I007 I008 I009       (the same move as the drag)
  badges on the board: 2;  card trail: "13:39 C005 from 5 to 2: Elderly, by reception@clinicq.example"
  no horizontal scroll at 1366×768: True
  trainee: 24 move buttons, 0 enabled, 0 draggable rows, no prompt rendered,
           hint "Moving a patient forward needs the priority permission at this clinic."
  manager's Overrides: 2 rows; counts "reception@clinicq.example 2"
  ```

### Screenshots (1366×768)

The reason prompt after dragging C005 onto C002:

![The reason prompt](https://github.com/Billykat7/clinicQ/blob/111e8c2d42120d78ce1c4063d2bad547ca2824ae/docs/GITHUB/PR/M7/assets/pr52/reason-prompt.png?raw=true)

A move without a reason: refused, the prompt still open, the line behind it unchanged:

![A move without a reason is refused](https://github.com/Billykat7/clinicQ/blob/111e8c2d42120d78ce1c4063d2bad547ca2824ae/docs/GITHUB/PR/M7/assets/pr52/no-reason-refused.png?raw=true)

The front desk afterwards: the staff-only Priority badge, and each card's overrides of the day:

![The board after two moves](https://github.com/Billykat7/clinicQ/blob/111e8c2d42120d78ce1c4063d2bad547ca2824ae/docs/GITHUB/PR/M7/assets/pr52/board-after-moves.png?raw=true)

A trainee without the permission: the same line, controls disabled, and the reason why:

![Reordering disabled for the trainee](https://github.com/Billykat7/clinicQ/blob/111e8c2d42120d78ce1c4063d2bad547ca2824ae/docs/GITHUB/PR/M7/assets/pr52/trainee-disabled.png?raw=true)

The manager's Overrides screen, and one override opened beside the list:

![The manager's overrides](https://github.com/Billykat7/clinicQ/blob/111e8c2d42120d78ce1c4063d2bad547ca2824ae/docs/GITHUB/PR/M7/assets/pr52/manager-overrides.png?raw=true)

![One override opened](https://github.com/Billykat7/clinicQ/blob/111e8c2d42120d78ce1c4063d2bad547ca2824ae/docs/GITHUB/PR/M7/assets/pr52/manager-override-detail.png?raw=true)

## Acceptance criteria

- [x] A reorder cannot be completed without selecting a reason code: `422`, no `queue_reorder` row, the
      line unchanged (test); the prompt says so and stays open (browser)
- [x] Drag and the tap-alternative produce identical results: one function and one request body for
      both (the recorded bodies above); the same move in two identical queues lands the same (test and
      browser)
- [x] The audit trail is visible without leaving the board: each card lists its queue's overrides of the
      day (test and screenshot)
- [x] Priority badges never appear on the public waiting-room board: no board projection, public schema
      or patient response carries priority, and no board template can import the badge (tests; see
      *Design notes*)
- [x] The manager view shows override counts per staff member for the day: listed by name, with the
      "not a ranking" sentence, whatever the filters (test and screenshot)
- [x] Reordering is disabled for roles without the permission: the controls render disabled with the
      reason, no prompt is rendered, and the API answers `403` (test and browser)

## Risk and rollback

No migration and no API change. The board gains a disclosure per queue and a trail line; the manager
gains one screen. Rollback is a revert of this PR.

**Known limits:** the board reloads after a saved move until Issue 49 makes it live. Staff appear by
email on the trail and the counts, as the audit log records them. An end-to-end drag test joins CI with
the Playwright suite in Issue 55; until then the drag was checked in the browser run above.

Closes #52
