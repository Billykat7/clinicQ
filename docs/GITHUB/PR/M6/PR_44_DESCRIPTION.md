# PR: Give a place back from any channel, and everyone behind moves up (Issue 44 / M6-44)

**Milestone:** [Milestone 6: Queue Engine Core](https://github.com/Billykat7/clinicQ/milestone/6) ·
**Issue:** [#44](https://github.com/Billykat7/clinicQ/issues/44) · **Builds on:** #41 (the lifecycle,
merged in #163) and #43 (merged in #165)

A patient who can no longer come can now give their place back from the web, USSD, WhatsApp or the
front desk. All four use one function, and the audit row names the channel. Cancelling is a
transition through `transition_ticket()`, so it is locked, audited and written through to the queue
snapshot like every other move.

Nobody else's row is touched. **Positions are derived from the order of the waiting tickets, never
stored.** The cancellation itself is the whole recalculation: the next read shows every patient
behind one place further forward.

A patient who has already been called cannot cancel from their phone; they are told to speak to
reception, and reception can cancel for them. The reason is optional, comes from a closed list, and
is countable for the no-show analysis.

## What this PR does not do, and says so

**Pushing the new positions to screens that are already open depends on the board stream
(Issue 57), which does not exist yet.** Until it lands, a patient sees their new place **on the next
page load**, from `GET /api/v1/patients/me/tickets`, which derives it at every read. Nothing here
fakes liveness with a poll loop or a pretend push. The criterion "affected patients see an updated
position without refreshing" is left unticked below, with the reason.

## Summary

- **`cancel_ticket(db, ticket_id, *, channel, actor, reason)`** (`src/modules/queue/cancellation.py`):
  1. Locks the row (`lock_ticket`, new in the lifecycle).
  2. Refuses a **patient** cancelling after being called with `CalledTicketSelfCancelError` (409,
     `ticket.cancel.after_call`).
  3. Moves the ticket to `cancelled` through `transition_ticket()`. The audit context reads e.g.
     `cancelled by patient via ussd, reason wait_too_long`.
  4. Records `cancelled_via` and `cancellation_reason` on the ticket, and returns how many patients
     moved up.
- **`cancel_own_ticket(db, patient_id, ticket_id, *, channel)`** is the patient's door (web, USSD,
  WhatsApp). Another patient's ticket is the same 404 as a missing one.
- **Routes** (in `contracts/queue.yaml`):
  - `POST /api/v1/patients/me/tickets/{ticket_id}/cancel` (web);
  - `POST /api/v1/sites/{site_id}/tickets/{ticket_id}/cancel` (the front desk, allowed after a call).

  `GET /api/v1/patients/me/tickets` now gives a waiting ticket its `waiting_ahead` and `wait`, derived
  at that read.
- **`CancellationReason`** is `feeling_better`, `wait_too_long`, `cannot_get_there`,
  `went_elsewhere`, `joined_by_mistake` or `other`. Free text is refused (422).
  `reason_counts(db, access, first_day, last_day)` is the per-clinic query the no-show analysis
  (Issue 93) reads.
- **Migration `0022`**: `ticket.cancelled_via`, `ticket.cancellation_reason`, and
  `ck_ticket_cancellation_only_when_cancelled`.

## Design notes

**Why no position column.** A stored position turns one cancellation into ten `UPDATE`s. The stale
rows that follow are exactly the disagreement between the board, the SMS and the patient's screen
that the lifecycle exists to prevent. A place in the line is the count of waiting tickets ahead in
call order, computed on the `ix_clinicq_ticket_board` index at every read. The test proves both
halves: tickets 4–10 read one place further forward, and their `modified_at` is unchanged, because
their rows were never written. `test_a_ticket_has_no_stored_position_to_go_stale` fails if a
`position`, `place` or `rank` column ever appears on `ticket`.

**The window is checked against the locked row.** A patient who pressed *Cancel* while a
receptionist pressed *Call* must not win on a stale read. So the service locks the ticket first,
checks its current status, and passes that status to `transition_ticket()` as `expected_status`.
Called, recalled and in-progress tickets are refused with the reception sentence. A ticket that is
already finished gets the lifecycle's own 409 (`ticket.transition.illegal`), because cancelling a
finished visit is not a question of the window.

**One function, four doors.** The web and the desk go over HTTP. USSD and WhatsApp are exercised
exactly as their adapters (M10) will call the service: the gateway-vouched patient, then
`cancel_own_ticket`. All four leave the same `cancelled` ticket. The audit contexts end
`(cancelled by patient via web)`, `via ussd`, `via whatsapp` and `(cancelled by staff via walk_in)`,
and `actor_role` separates patient from staff.

**The desk is "walk_in".** `PatientChannel` names the four doors, and the front desk's is `walk_in`.
A desk cancellation is recorded as that channel with the staff member as the actor, so the report
can tell "cancelled at reception" from "cancelled on the phone".

**A guard learned to follow a call across files.** `test_mutations_are_audited.py` read each file of a
module on its own. The cancellation routes call the cancellation service, which audits through the
lifecycle in another file, so the guard wrongly reported them as unaudited. It now reads a module's
files together. The rule is unchanged: a mutating route with no audit anywhere below it still fails.

**Guards.** `cancel_own_ticket` (a patient's own ticket, by id and owner) and `my_tickets` (the queue
of the patient's own ticket) are listed in the site-scope guard with their reasons.

**Out of scope:** the USSD and WhatsApp menu entries (Issues 73, 75, which call this), the no-show
analysis itself (Issue 93), and the live push (Issue 57).

## Changes

- **`src/modules/queue/cancellation.py`** (new): `cancel_ticket`, `cancel_own_ticket`,
  `reason_counts`, `CalledTicketSelfCancelError`, `CancelResult`.
- **`src/modules/queue/lifecycle.py`:** `lock_ticket`.
- **`src/modules/queue/router.py`**, **`schemas.py`:** the two cancel routes, `CancelIn`, `CancelOut`,
  `MyTicketOut`, and positions and waits on the patient's own tickets.
- **`src/database/models/ticket.py`** and **`alembic/versions/0022_ticket_cancellation.py`** (new):
  the two columns and the constraint. **`src/commons/enums.py`:** `CancellationReason`.
- **`contracts/queue.yaml`:** the cancel routes, `CancellationReason`, `CancelIn`, `CancelOut`,
  `MyTicketOut`.
- **`docs/PRODUCT/03-booking-and-queue.md`:** the recall, no-show and cancel table now describes what
  #43 and this PR do.
- **Tests (new):** `tests/integration/queue/test_cancellation.py` (7).
- **Tests (updated):** `tests/unit/security/test_mutations_are_audited.py` (reads a module's files
  together), `test_site_scoped_queries.py` (two reasons), and `test_join_queue.py` (the racing helper
  keeps a thread's exception; see *Testing*).
- **Docs:** the M6 Status row, the sprint 7 row, the README Status block and the progress bars
  (`make milestone-progress ARGS='--assume-closed 44'`).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean.
- [x] Full suite in UTC with PostgreSQL and Redis required: **1844 passed, 9 xfailed** (after merging
      `main` with #43). **One honest note:** the first full run after that merge failed once in
      `test_a_phone_and_the_desk_racing_twenty_times_always_get_adjacent_numbers` (Issue 40). One of
      its two racing joins raised inside its thread, and the helper turned that into a `None`, so the
      error itself was lost. It did not recur in two further full runs or in 24 runs of the race tests
      across six parallel processes. The helper now records a thread's exception and the test asserts
      on it, so if it happens again the failure names the error instead of hiding it. That change is
      in this PR.
- [x] `make milestone-progress-check ARGS='--assume-closed 44'`: up to date.
- [x] The Issue 44 tests:

```text
queue/test_cancellation.py::test_cancelling_3_of_10_moves_4_to_10_up_one_place_within_2_seconds PASSED
queue/test_cancellation.py::test_a_ticket_has_no_stored_position_to_go_stale PASSED
queue/test_cancellation.py::test_a_called_ticket_cannot_be_self_cancelled_and_the_desk_can_cancel_it PASSED
queue/test_cancellation.py::test_another_patients_ticket_cannot_be_cancelled_and_looks_like_no_ticket PASSED
queue/test_cancellation.py::test_cancellation_is_identical_on_all_four_channels_and_audited_with_the_channel PASSED
queue/test_cancellation.py::test_reasons_are_optional_from_a_closed_list_and_counted_for_the_analysis PASSED
queue/test_cancellation.py::test_a_finished_ticket_cannot_be_cancelled_either PASSED
7 passed in 1.88s
```

- [x] **How to verify, step 1, measured over HTTP.** Ten patients joined on the web, ticket 3 was
      cancelled, then each of tickets 4–10 read its own ticket back:

```text
POST cancel T003 -> 200 Ticket T003 is cancelled. Thank you for giving your place back: 7 people moved up.
tickets 4-10 waiting_ahead before: [3, 4, 5, 6, 7, 8, 9]
tickets 4-10 waiting_ahead after:  [2, 3, 4, 5, 6, 7, 8]
cancel plus seven reads: 41 ms
```

- [ ] Screenshot: no template, stylesheet or script changed; the ticket page is Issue 68.

## Acceptance criteria

- [x] **Cancelling frees the slot and recalculates positions for everyone behind, within 2 seconds.**
      Tickets 4–10 moved from `[3…9]` to `[2…8]` ahead. The cancellation and all seven reads took
      41 ms, and the test asserts under 2 s. No other ticket's row changed. The cancelled ticket no
      longer counts toward capacity, which counts non-cancelled tickets (Issue 40).
- [ ] **Affected patients see an updated position without refreshing.** *Partly: the position is
      correct on the next read of any surface (derived, never stored). Pushing it to a page that is
      already open waits for the board stream, Issue 57.*
- [x] **Cancellation works identically on all four channels.** Web and the desk over HTTP, USSD and
      WhatsApp through the service as their adapters will call it. Each ends `cancelled`, with
      `cancelled_via` recorded.
- [x] **A called ticket cannot be self-cancelled without staff action.** The patient gets 409 with
      *You have already been called, so this ticket can no longer be cancelled from your phone. Please
      speak to reception.* The desk's cancellation of the same ticket succeeds.
- [x] **Cancellation is audited with the channel it came from.** One audit row per cancellation, whose
      context ends `(cancelled by patient via web|ussd|whatsapp)` or `(cancelled by staff via
      walk_in)`, with `actor_role` `patient` or `staff`.
- [ ] **Cancellation reasons feed the no-show analysis in M12.** *Partly: the reason is stored as a
      closed enum (free text is a 422), and `reason_counts` returns a clinic's counts per reason over a
      date range (tested: 2 `wait_too_long`, 1 with none, and nothing from another clinic). The analysis
      that reads it is Issue 93.*

## Risk and rollback

**Migration `0022`** adds two nullable columns and a check constraint that every existing row
satisfies, and it is reversible (`alembic downgrade 0021`). The cancel routes are additive.
`GET /patients/me/tickets` gains two fields. Rollback is a revert plus the downgrade.

**Follow-ups:** Issue 57 pushes position changes to open screens. Issues 73 and 75 add the menu
entries that call `cancel_own_ticket`. Issue 93 reads `reason_counts`. A reply to an SMS as a fifth
way to cancel arrives with M9's inbound messages and would call the same service.

Closes #44
