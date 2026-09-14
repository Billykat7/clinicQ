# PR: The ticket lifecycle, and the only code allowed to change a ticket's status (Issue 41 / M6-41)

**Milestone:** [Milestone 6: Queue Engine Core](https://github.com/Billykat7/clinicQ/milestone/6) ·
**Issue:** [#41](https://github.com/Billykat7/clinicQ/issues/41) · **Builds on:** #39 and #40
(merged in #161 and #162)

The board, the notifications, the reports and the patient's screen all act on `ticket.status`. If
any of them could write it directly, they would disagree without anyone noticing. This PR makes
non-negotiable 2 true. The legal moves live in one table in `src/modules/queue/lifecycle.py`, and
`transition_ticket()` is the only function that applies them. An illegal move is a `409` and
changes nothing. Every move is timestamped and audited. Two moves on the same ticket at the same
instant are serialised, and the second one gets a `409`.

The most important deliverable is the guard that keeps it true. A test fails the build on a direct
status write anywhere in `src/`, and the model refuses one at runtime. Both were shown failing on
the mistake they exist to catch before this PR was opened.

## Summary

- **`TRANSITIONS`**, over `TicketStatus` from Issue 4:
  - `waiting → called | cancelled | transferred`
  - `called → in_progress | recalled | no_show | cancelled`
  - `recalled → in_progress | no_show | cancelled`
  - `in_progress → done | transferred`

  `done`, `no_show`, `cancelled` and `transferred` never change again. That is 12 legal moves out
  of 64 pairs.
- **`transition_ticket(db, ticket_id, to, *, actor, expected_status=None, note=None)`**:
  1. Locks the row (`SELECT … FOR UPDATE`, re-read with `populate_existing`).
  2. Refuses with `IllegalTransitionError` or `StaleTransitionError`, both `409`.
  3. Stamps `called_at`, `started_at` or `completed_at`.
  4. Writes one audit row whose `actor_role` is `staff`, `patient` or `system` (`ActorKind`).
  5. Writes the queue snapshot through.
- **`call_next(db, queue, *, actor)`** picks the next waiting ticket with `FOR UPDATE SKIP LOCKED`,
  so two staff pressing *Call next* call two different patients, and moves it through
  `transition_ticket()`.
- **Two layers of enforcement.**
  - `tests/unit/queue/test_status_written_only_by_lifecycle.py` scans `src/`. It flags an
    assignment to a ticket's `status`, `setattr`, `Ticket(status=…)`, a bulk `update(Ticket)`, raw
    SQL that sets a ticket's status, and the runtime guard's key used anywhere but the lifecycle.
  - The model's attribute listener raises `DirectStatusWriteError` for any write outside
    `status_write_permitted()`.
- **Routes** (in `contracts/queue.yaml`):
  - `POST /sites/{site_id}/tickets/{ticket_id}/transitions`
  - `POST /sites/{site_id}/queues/{queue_id}/tickets/call-next`
  - `GET /sites/{site_id}/queues/{queue_id}/tickets/next` (who would be called)
- **The diagram** in `docs/PRODUCT/03-booking-and-queue.md` is a mermaid state diagram that a test
  compares arrow by arrow with the table.

## Design notes

**Which moves are in the table, and why.**

- *Called → no-show* is allowed directly, so staff can mark an absent patient at once (Issue 43's
  override).
- *Recalled → called* is not: a recall happens exactly once, which is what lets Issue 43 promise
  "recalls exactly once" by construction.
- *Transferred* is reachable from *waiting* (a patient in the wrong line) and from *in progress*
  (triage done, on to the doctor), not from *called*: a transfer happens to a patient who is either
  still waiting or has been seen.
- *In progress → cancelled* is not allowed: a consultation that started ends as *done* or moves on.

The legal set is small on purpose. Every extra arrow is a path the board and the reports must handle.

**Serialisation, and the two kinds of 409.** The row lock serialises moves on one ticket. The
second of two identical moves (both *call*) waits, then finds `called → called` illegal. The second
of two *different* moves decided on the same screen (one *start*, one *cancel*) could be legal from
the new status, which would silently apply a decision made on stale information. So a caller passes
`expected_status`, and a mismatch is `ticket.transition.stale` rather than a quiet success. Both
cases are raced ten times on PostgreSQL. `call_next` uses `SKIP LOCKED` instead of waiting, because
the second person pressing *Call next* wants the *next* patient, not a 409.

**Why a runtime guard as well as the static one.** The source scan attributes a write to a ticket
by name (`ticket.status`, `result.ticket.status`) or by the module importing `Ticket`. A write
through a variable named `row` in a module that imports the model under another path would slip
past it. The attribute listener catches exactly that (`test_a_direct_write_at_runtime_…` uses a
variable named `row`). In the other direction, a bulk `UPDATE` or raw SQL never fires an ORM event,
and the source scan catches both. The permission is a `ContextVar`, so a write permitted in one
request or thread never leaks into another. Constructing a new ticket as `waiting` is allowed,
because that is where every ticket starts rather than a transition. Loading a row does not fire the
listener.

**Terminal means terminal.** A ticket marked *done* by mistake is not reopened. The patient joins
again and gets a new number in arrival order, and the old ticket's history stays true. The
one-active-ticket rule from Issue 40 counts only tickets still in the day, so this works without any
special case (`test_a_terminal_ticket_cannot_be_reopened_and_a_correction_is_a_new_ticket`: T001 is
*done*, reopening is refused with *A done ticket cannot change any more.*, and the patient's next
join is T002).

**The system actor.** `Actor.system()` writes `actor = "system"` and `actor_role = "system"`, and
the job names itself in the transition's note. Issue 43's recall timer uses it, so the audit trail
tells a timer from a receptionist by a field rather than by parsing names.

**One order, defined once.** `CALL_ORDER` (sequence, which is arrival order across every channel) is
what *Call next*, the board and the peek all sort by. Issues 45 and 46 change the order there and
nowhere else.

**A CI change, required by an existing guard.** The diagram test reads `docs/PRODUCT/03`, and
`test_the_prose_only_fast_path_skips_nothing_a_test_reads` rightly failed. A PR touching only
`docs/PRODUCT/` would otherwise skip the tests that read it. Following that test's own instruction,
and the precedent of `docs/TEAM/` in Issue 13, `docs/PRODUCT/` leaves `PROSE_PATHS` in `ci.yml`,
and `docs/CICD/PIPELINES.md` records why.

**Guards updated.** The site-scope guard lists three lifecycle reads, each with its reason: the lock
re-reads an id its caller already scoped, the snapshot hook reads the ticket's own queue, and
`call_next` reads within a queue from `get_queue`. The cross-tenant suite gains a `queues.call` case
on the peek route, with one queue per clinic in its fixture, replacing the pending entry.

**Out of scope:** timed recall and no-show (Issue 43), transfers (Issue 45), and the dashboard
buttons (Issue 50).

## Changes

- **`src/modules/queue/lifecycle.py`** (new): `TRANSITIONS`, `is_legal`, `transition_ticket`,
  `call_next`, `Actor`, and `IllegalTransitionError`, `StaleTransitionError`, `NobodyWaitingError`.
- **`src/database/models/ticket.py`:** `status_write_permitted()`, `DirectStatusWriteError`, the
  `Ticket.status` set listener.
- **`src/modules/queue/tickets.py`:** `CALL_ORDER`, used by `board_select`.
- **`src/modules/queue/router.py`**, **`schemas.py`:** the three routes and `TransitionIn`.
- **`src/commons/enums.py`:** `ActorKind`.
- **`contracts/queue.yaml`:** the three routes, both `409` codes, `TransitionIn`.
- **`docs/PRODUCT/03-booking-and-queue.md`:** the *Ticket lifecycle* section and diagram.
  **`docs/guideline.md`:** non-negotiable 2 names its two enforcements.
- **`.github/workflows/ci.yml`** and **`docs/CICD/PIPELINES.md`:** `docs/PRODUCT/` is no longer
  prose-only.
- **Tests (new):** `tests/unit/queue/test_ticket_state_machine.py` (64 pairs plus 3),
  `test_status_written_only_by_lifecycle.py` (11) and
  `tests/integration/queue/test_ticket_lifecycle.py` (8, two on PostgreSQL).
- **Tests (updated):** `tests/factories.py` (`status_path`, `drive_ticket_to`: the one way a test
  puts a ticket in a status), `test_cross_tenant.py`, `test_site_scoped_queries.py`.
- **Docs:** the M6 Status row, sprint 6 row and prose, README Status block and progress bars
  (`make milestone-progress ARGS='--assume-closed 41'`).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (234 files).
- [x] Full suite in UTC with PostgreSQL and Redis required: **1810 passed, 9 xfailed**.
- [x] `make milestone-progress-check ARGS='--assume-closed 41'`: up to date.
- [x] The Issue 41 tests (86 passed), trimmed:

```text
queue/test_ticket_state_machine.py::test_every_pair_is_either_a_legal_move_or_a_409_that_changes_nothing[waiting->waiting] PASSED
queue/test_ticket_state_machine.py::test_every_pair_is_either_a_legal_move_or_a_409_that_changes_nothing[waiting->called] PASSED
…                                                                                      (64 pairs)
queue/test_ticket_state_machine.py::test_every_pair_is_either_a_legal_move_or_a_409_that_changes_nothing[transferred->transferred] PASSED
queue/test_ticket_state_machine.py::test_the_table_speaks_for_every_status_and_nothing_leaves_a_terminal_one PASSED
queue/test_ticket_state_machine.py::test_every_move_is_timestamped PASSED
queue/test_ticket_state_machine.py::test_the_documented_state_diagram_matches_the_transition_table PASSED
queue/test_status_written_only_by_lifecycle.py::test_nothing_outside_transition_ticket_writes_a_tickets_status PASSED
queue/test_status_written_only_by_lifecycle.py::test_the_guard_fails_on_every_shape_it_exists_to_catch[…] PASSED   (8 shapes)
queue/test_status_written_only_by_lifecycle.py::test_the_guard_passes_the_lifecycle_and_other_records_statuses PASSED
queue/test_status_written_only_by_lifecycle.py::test_a_direct_write_at_runtime_is_refused_the_moment_it_runs PASSED
queue/test_ticket_lifecycle.py::test_every_illegal_pair_is_a_409_over_http_and_changes_nothing PASSED
queue/test_ticket_lifecycle.py::test_each_move_writes_an_audit_row_with_actor_role_and_time PASSED
queue/test_ticket_lifecycle.py::test_a_move_decided_on_a_stale_screen_is_refused PASSED
queue/test_ticket_lifecycle.py::test_another_clinics_ticket_is_not_found PASSED
queue/test_ticket_lifecycle.py::test_a_terminal_ticket_cannot_be_reopened_and_a_correction_is_a_new_ticket PASSED
queue/test_ticket_lifecycle.py::test_call_next_calls_in_sequence_and_says_when_nobody_is_waiting PASSED
queue/test_ticket_lifecycle.py::test_concurrent_moves_on_one_ticket_are_serialised_and_the_loser_gets_409 PASSED   (PostgreSQL)
queue/test_ticket_lifecycle.py::test_two_staff_pressing_call_next_at_once_call_two_different_tickets PASSED          (PostgreSQL)
86 passed in 8.38s
```

- [x] **How to verify, step 2, for real.** `ticket.status = TicketStatus.DONE.value` added to the
      transition route in `src/modules/queue/router.py`, then the guard run. The line was removed
      afterwards and the guard passes again:

```text
E       AssertionError: a ticket's status written outside the lifecycle:
E         src/modules/queue/router.py:278 assigns ticket.status; only transition_ticket() writes a ticket's status (non-negotiable 2)
1 failed in 0.58s
```

- [x] **The diagram check can fail.** Changing `recalled --> in_progress` to `recalled --> called`
      in `docs/PRODUCT/03`, then restoring it:

```text
E         Extra items in the left set:
E         ('recalled', 'called')
E         Extra items in the right set:
E         ('recalled', 'in_progress')
1 failed in 0.14s
```

- [ ] Screenshot: no template, stylesheet or script changed; the dashboard buttons are Issue 50.

## Acceptance criteria

- [x] **Every illegal transition is rejected with 409 and leaves state untouched, covered
      exhaustively by tests.** All 64 `(from, to)` pairs, twice:
      - through the service: 52 raise `IllegalTransitionError` (409, `ticket.transition.illegal`);
        after committing the session, the row read back has the same status and timestamps and no
        new audit row;
      - over HTTP: the same 52 answer `409` with the row unchanged, and the 12 legal ones answer
        `200`.
- [x] **No code path outside `transition_ticket()` writes `ticket.status`, enforced by a guard
      test.** The scan passes on `src/`, fails on each of eight fixture shapes, and failed on the
      real router line above. The runtime listener refuses a write under a name the scan cannot
      attribute.
- [x] **Each transition writes an audit row with actor and timestamp.** `update`/`ticket`, actor
      `desk.a@clinicq.example`, `actor_role` `staff`, `diff` `{status: waiting → called}`, context
      `T001: waiting → called`, the clinic, `created_at`. `called_at` is stamped in Johannesburg
      time. In the concurrency test, only the winners' moves appear on the trail.
- [x] **A terminal ticket cannot be reopened.** The table gives the four terminal statuses no exits
      (tested), `done → waiting` over HTTP is *A done ticket cannot change any more.*, and the
      correction is a new ticket, T002.
- [x] **The state diagram is documented and matches the transition table, verified by a test.**
      `test_the_documented_state_diagram_matches_the_transition_table` compares every arrow, the
      start and the four ends, and it failed on a deliberately wrong arrow.
- [x] **Concurrent transitions on one ticket are serialised, with the loser receiving 409.** On
      PostgreSQL, ten rounds of each race:
      - two identical moves: one moves, the other gets `IllegalTransitionError` (409);
      - two different moves on the same screen: one moves, the other gets `StaleTransitionError`
        (409).

      Two staff pressing *Call next* at once, ten rounds: always two different tickets, all 20
      called exactly once.

## Risk and rollback

No migration. The runtime listener raises on a direct status write, which no code does today (the
scan proves it), so its only production effect is to turn a future bug into an immediate error.
The CI change widens what runs the tests: a PR touching only `docs/PRODUCT/` now runs the suite. The
new routes are additive. Rollback is a revert.

**Follow-ups:** Issue 43 uses `Actor.system()` and the `recalled` step. Issues 45 and 46 change
`CALL_ORDER`, and `waiting_ahead()` alongside it. Issue 50 builds the dashboard buttons on these
routes.

Closes #41
