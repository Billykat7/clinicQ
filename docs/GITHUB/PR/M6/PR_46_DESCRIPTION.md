# PR: Move a visibly unwell patient forward, never silently (Issue 46 / M6-46)

**Milestone:** [Milestone 6: Queue Engine Core](https://github.com/Billykat7/clinicQ/milestone/6) ·
**Issue:** [#46](https://github.com/Billykat7/clinicQ/issues/46) · **Builds on:** #41 (the lifecycle)
and #20 (the audit log), merged; #45 merged in #167

"One fair queue" only survives a real clinic if staff can bump a visibly unwell patient. This PR
makes that a two-tap clinical judgement on the record, not a way to skip the line:

- **An override without a reason code does not save**, enforced on the server.
- **A patient can never be moved ahead of someone already being seen.**
- **Every override writes two rows:** a `queue_reorder` row with the place before and after, the
  staff member and the reason, and an audit row that is the proof.

Nothing about priority reaches the public board or any patient-facing response, and a test walks the
API to keep it that way. The per-staff override counts for the reports are listed **by name, never
ranked**, with a sentence saying they are not a measure of performance.

## Summary

- **`override_priority(db, ticket_id, *, ahead_of_ticket_id, reason, actor, note)`**
  (`src/modules/queue/priority.py`):
  1. Refuses `reason=None` with `PriorityReasonRequiredError` (422) before reading or writing
     anything.
  2. Locks both tickets.
  3. Refuses a target that is called, recalled or in progress (`AheadOfCalledPatientError`, 409),
     and a move that is not forward, not waiting or not in the same queue.
  4. Sets the patient's `order_key` just ahead of the target.
  5. Writes the `queue_reorder` row and the audit row, then the snapshot.
- **`PriorityReason`** in `src/commons/enums.py` is `visibly_unwell`, `elderly`, `infant`,
  `pregnancy`, `staff_referral` or `other`. It is a closed list, never free text. A note of up to
  140 characters is optional, and redacted in the audit diff (`note` joins
  `AUDIT_REDACTED_FIELDS`).
- **`queue_reorder`** (migration `0024`) holds the ticket, queue, staff name and id, reason code,
  note and the place before and after. `ck_queue_reorder_reason_code` holds the vocabulary, and
  `ck_queue_reorder_moves_forward` records that an override only moves a patient forward.
- **Routes** (in `contracts/queue.yaml`):
  - `POST /api/v1/sites/{site_id}/tickets/{ticket_id}/priority` (`queues.tickets.priority`
    update);
  - `GET /api/v1/sites/{site_id}/tickets/reorders`, today's trail (`queues.tickets.priority` read);
  - `GET /api/v1/sites/{site_id}/tickets/reorders/counts`, the counts per staff member over a
    period (`sites.reports` read: the clinic manager).

## Design notes

**"Ahead of which ticket", not "to which position".** The override names the waiting ticket the
patient should now be called before. That is what a drag-to-reorder (Issue 52) produces, and it makes
the in-progress rule a check on a named row rather than on arithmetic. If the named ticket has been
called or is being seen, the answer is 409. Moving ahead of such a patient is also impossible by
construction, because only waiting tickets are in the call order.

**Server-side, twice.** `PriorityIn` requires `reason`, so a request without one is a 422 from
validation. That alone would be a rule in a form, so the service refuses `reason=None` itself, and
the test calls it that way directly. In both cases the queue's order keys are unchanged and no
`queue_reorder` row exists.

**Two rows, one transaction.** The issue asks for both, and they have different jobs. The
`queue_reorder` row is the queue-shaped detail the dashboard trail and the reports read. The audit
row is append-only by database trigger, and is the proof. Both are added before the caller commits,
so neither can exist without the other.

**Positions stay derived.** Only the moved ticket's `order_key` changes: it is set between the target
and the waiting ticket before it (the key #45 introduced). The test reads everyone's place after the
override: T007 is second, T002–T006 each moved back one, T001 is untouched, and the other six
tickets' `order_key` values are exactly as they were. Reading all seven places took well under the
2-second criterion.

**Not a ranking, by construction.** `override_counts` groups by staff member and **orders by name**.
The response carries only `staff` and `overrides` per row, plus a `note`: *How often each staff
member used a priority override. Overrides are clinical judgements, and these counts are not a
measure of anybody's performance; they are listed by name, not by count.* The test makes the manager
the higher count and asserts they are still listed second. The counts are the clinic manager's
(`sites.reports`); the receptionist gets a 403.

**Off the public board.** `test_nothing_about_priority_reaches_a_public_or_patient_facing_shape`
collects every schema reachable from the public `/api/v1/clinics` routes and the patient's own
`/api/v1/patients` routes. It fails on any field named like `priority`, `reorder`, `reason_code` or
`override`, and it pins the board projection's fields (`BoardEntry`) to the number, name and comment.

**Guards.** The override's read of the ticket just ahead (`_key_ahead_of`) and of the ticket's own
queue for the snapshot are listed in the site-scope guard with reasons. The cross-tenant suite
replaces the `queues.tickets.priority` pending entry with a `queuereorder` case on the trail.

**Out of scope:** the drag-to-reorder UI and its inline trail (Issue 52), and the report pages
(Issue 90), which read these routes.

## Changes

- **`src/modules/queue/priority.py`** (new): `override_priority`, `reorder_trail`,
  `override_counts`, `COUNTS_ARE_NOT_RANKINGS`, and the three errors.
- **`src/database/models/queue_reorder.py`**, **`alembic/versions/0024_queue_reorder.py`** (new).
- **`src/modules/queue/router.py`**, **`schemas.py`:** the three routes, `PriorityIn`, `PriorityOut`,
  `ReorderOut`, `ReorderTrailOut`, `OverrideCountsOut`.
- **`src/commons/enums.py`:** `PriorityReason`; `note` in `AUDIT_REDACTED_FIELDS`.
- **`contracts/queue.yaml`:** the three routes and their schemas.
- **`docs/PRODUCT/03-booking-and-queue.md`:** the override rules and the `queue_reorder` table.
- **Tests (new):** `tests/integration/queue/test_priority_override.py` (6).
- **Tests (updated):** `test_cross_tenant.py` and `test_site_scoped_queries.py`.
- **Docs:** the M6 Status row, the sprint 7 row, the README Status block and the progress bars
  (`make milestone-progress ARGS='--assume-closed 46'`).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (243 files).
- [x] Full suite in UTC with PostgreSQL and Redis required: **1861 passed, 9 xfailed**.
- [x] `make milestone-progress-check ARGS='--assume-closed 46'`: up to date.
- [x] The Issue 46 tests:

```text
queue/test_priority_override.py::test_an_override_without_a_reason_code_does_not_save PASSED
queue/test_priority_override.py::test_an_override_writes_a_reorder_row_and_an_audit_row_and_moves_everyone_behind PASSED
queue/test_priority_override.py::test_an_override_cannot_move_a_ticket_ahead_of_one_already_in_progress PASSED
queue/test_priority_override.py::test_other_refusals_move_nothing PASSED
queue/test_priority_override.py::test_the_trail_is_visible_to_the_clinic_manager_and_counts_are_not_a_ranking PASSED
queue/test_priority_override.py::test_nothing_about_priority_reaches_a_public_or_patient_facing_shape PASSED
6 passed in 1.97s
```

- [x] **How to verify, over HTTP.** Seven walk-ins in Triage, the receptionist overriding and the
      manager reading:

```text
POST priority, no reason -> 422 request.invalid
POST priority T007 ahead of T002, visibly_unwell -> place 7 -> 2
POST priority T006 ahead of T001 (in progress) -> 409 A patient cannot be moved ahead of someone who has already been called or is being seen.
GET reorders (manager) -> [{"staff": "desk.a@clinicq.example", "reason": "visibly_unwell", "note": "short of breath", "position_before": 7, "position_after": 2}]
GET reorders/counts (manager) -> {"items": [{"staff": "desk.a@clinicq.example", "overrides": 1}], "note": "How often each staff member used a priority override. Overrides are clinical judgements, and these counts are not a measure of anybody's performance; they are listed by name, not by count."}
```

- [ ] Screenshot: no template, stylesheet or script changed; the reorder UI is Issue 52.

## Acceptance criteria

- [x] **An override cannot be saved without a reason code.** Refused over HTTP (422, with no reason
      or with free text) and by the service when called with `reason=None` (422, *A priority override
      needs a reason.*). No `queue_reorder` row is written and every order key is unchanged.
- [x] **Every override writes a `queue_reorder` row with before and after positions.** One row with
      place 7 → 2, `visibly_unwell`, the note and the staff member. The audit row beside it has
      `position {before: 7, after: 2}`, the note `<redacted>`, and context
      `T007: priority override, place 7 → 2, reason visibly_unwell`.
- [ ] **Affected patients see their updated position within 2 seconds.** *Partly: every place is
      correct on the next read (T002–T006 one further back, T007 second), read back well within 2 s,
      and no other row is written. Pushing it to an open screen waits for the board stream, Issue 57,
      as in #44.*
- [x] **Override counts per staff member are reportable.** `GET …/tickets/reorders/counts` for the
      clinic manager: counts per staff member over a period, listed by name (the higher count is
      listed second), with the not-a-ranking note. The receptionist gets 403.
- [x] **An override cannot move a ticket ahead of one already `in_progress`.** Naming an in-progress
      ticket, or a called one, is refused with `ticket.priority.ahead_of_called`, and nothing is
      written.
- [ ] **The override trail is visible to the clinic manager in the dashboard.** *Partly: the manager
      reads the day's trail over the API (who, why, note, place before and after). The dashboard view
      that renders it is Issue 52.*

## Risk and rollback

**Migration `0024`** adds one table and is reversible (`alembic downgrade 0023`). The audit rows
written with each override remain as the proof. The override moves only one ticket's `order_key`,
and joins still append by sequence. The routes are additive. Rollback is a revert plus the downgrade.

**Follow-ups:** Issue 52 builds the drag-to-reorder UI and inline trail on these routes. Issue 90
presents the counts in the reports and must keep the by-name order and the note. Issue 57 pushes new
positions to open screens.

Closes #46
