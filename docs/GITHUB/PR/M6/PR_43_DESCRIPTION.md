# PR: A called patient who does not arrive is recalled once, then marked a no-show (Issue 43 / M6-43)

**Milestone:** [Milestone 6: Queue Engine Core](https://github.com/Billykat7/clinicQ/milestone/6) ·
**Issue:** [#43](https://github.com/Billykat7/clinicQ/issues/43) · **Builds on:** #41 (the lifecycle,
merged in #163) and #2 (the dev stack)

A called patient who is in the bathroom should not lose their place, and one who left an hour ago
should not hold a room up. This PR adds the timers.

- A called ticket not attended within the timeout is **recalled, exactly once**, and the patient is
  texted that they have been called again and how long they have.
- If they are still absent after the same timeout again, the ticket becomes a **no-show**. That
  frees the room, and the patient is texted how to join again.

Every automatic move goes through `transition_ticket()` as the **system** actor, so the audit trail
tells a timer from a receptionist.

The issue carried a decision first, and it is settled here: **no `arq`.** Open decision 1 is now
recorded in `docs/GITHUB/ISSUES/README.md`. The timers are a sweep in the kernel's APScheduler under
a PostgreSQL advisory lock. Restart safety is not asserted but proved: the sweep runs in brand-new
Python processes before the deadline, across a restart, after it, and two at a time.

## Summary

- **`run_recall_timers(db, *, moment, settings, sms_provider)`** (`src/modules/queue/timers.py`)
  works in four steps:
  1. Finds called and recalled tickets whose deadline has passed, rows locked with
     `FOR UPDATE SKIP LOCKED`.
  2. Moves each through `transition_ticket(..., actor=Actor.system(), expected_status=...)`, each in
     its own savepoint.
  3. Skips any ticket staff moved first.
  4. Sends the patient's message.

  It returns what it recalled and what it marked no-show.
- **`run_recall_timer_sweep()`** in `src/core/scheduler.py` takes advisory lock **443** and runs every
  `QUEUE_RECALL_SWEEP_SECONDS` (30). A missed run is coalesced.
- **The deadline is data.** It is `called_at` plus the timeout, then the new `ticket.recalled_at`
  plus the timeout (migration `0021`). There is no timer in memory to lose.
- **The timeout** is the queue's `recall_timeout_minutes`, else the clinic's, else
  `QUEUE_RECALL_TIMEOUT_MINUTES` (5). A manager sets the clinic's with
  `GET/PUT /sites/{site_id}/settings/recall` (audited) and a queue's through the queue API (1–60
  minutes).
- **The patient's messages** use two new templates, `TICKET_RECALLED` and `TICKET_NO_SHOW`. They go
  through the notification service, so they are on the ledger, sent by the configured provider (the
  logging provider until M9), gated by the patient's notification consent, and marked urgent so
  quiet hours never hold a recall back. A walk-in with no number is called by the board and the
  desk.
- **Staff override** was already there: the transition route from #41 recalls or marks a no-show at
  once. The timer then starts the no-show clock from the staff member's recall.
- **Decision 1** is recorded in `docs/GITHUB/ISSUES/README.md`. The losing side's wording is corrected
  in the M6 milestone and this issue's spec.

## Design notes

**Why not `arq`.** `arq` was proposed for two properties, "survives a restart" and "never
double-fires", and the sweep has both for a reason worth stating: **a timed job's state lives in the
database, not in the worker.** A deadline is two columns, so a process that started a second ago sees
exactly what the last one saw. Double-firing is refused three separate times:

1. the advisory lock elects one runner;
2. each candidate row is locked, with `SKIP LOCKED`, so a ticket a receptionist is moving right now
   waits for the next sweep instead of blocking this one;
3. the move carries `expected_status`, and the lifecycle's table has no way from `recalled` back to
   `called`.

`arq` would have made Redis a hard dependency of correctness, added a second process type to deploy
and watch, and created a second place a job's state could disagree with the database, for no
property the sweep lacks. The README note says when to revisit: many short fan-out tasks, which no
current spec has.

**"Exactly once" is structural, not just tested.** A recall is a transition `called → recalled`, and
`recalled` has no exit back to `called` (#41). A second recall is therefore illegal, not merely
unlikely. The test sweeps before the deadline, just after it, at the same instant again and four
minutes later: `0, 1, 0, 0`.

**"Frees the room" means nothing is occupying it.** After the no-show there is no `called` or
`recalled` ticket in the queue, and `call_next` calls the next waiting patient. The board and
dashboard (M7, M8) read occupancy from those statuses.

**System, visibly.** `Actor.system()` writes `actor = "system"`, `actor_role = "system"`, and a
context like `T001: called → recalled (recall timer: not attended within 5 min)`. The staff call on
the same ticket carries `actor_role = "staff"` and the receptionist's email. (`Actor.system()` also
loses a leftover argument from #41; no caller passed it.)

**Consent comes first.** A recall message is a message to a patient, so it passes the same consent
gate as every other (Issue 21). A patient who has not agreed gets a `suppressed` ledger row, not an
SMS, and the move still happens. Both cases are tested. The templates are in the essential category
because the patient asked for this by joining, and urgent so quiet hours cannot delay a recall.
Issue 67 gives patients their own preferences and may give queue messages a category of their own.

**Guards.** The sweep's platform-wide read (`_due`) and its message lookup (`_notify`) are listed in
the site-scope guard with reasons, like the snapshot reconciliation's. The new site settings routes
audit through the sites router, as the mutation guard requires.

**Out of scope:** real SMS and WhatsApp delivery (M9) and the dashboard's recall and no-show buttons
(Issue 50, on the existing transition route).

## Changes

- **`src/modules/queue/timers.py`** (new): `run_recall_timers`, `RecallSweep`, `timeout_minutes`.
- **`src/core/scheduler.py`:** `run_recall_timer_sweep`, lock key 443, the job.
- **`alembic/versions/0021_recall_timers.py`** (new): `ticket.recalled_at`,
  `queue.recall_timeout_minutes`, `site.recall_timeout_minutes`. The models match.
- **`src/modules/queue/lifecycle.py`:** stamps `recalled_at`; `Actor.system()` takes no argument.
- **`src/core/config.py`:** `QUEUE_RECALL_TIMEOUT_MINUTES`, `QUEUE_RECALL_SWEEP_SECONDS`.
  **`.env.example`:** regenerated.
- **`src/commons/enums.py`** and **`src/modules/notifications/templates.py`:** the two templates, their
  category, urgency and SMS renderers.
- **`src/modules/sites/router.py`** and **`schemas.py`:** the recall settings routes.
  **`src/modules/queues/`:** `recall_timeout_minutes` on the queue API.
- **`contracts/sites.yaml`:** the settings routes and the queue field.
- **`docs/GITHUB/ISSUES/README.md`** (decision 1), **`docs/GITHUB/MILESTONES/M6_queue_engine_core.md`**
  and **`docs/GITHUB/ISSUES/M6/ISSUE_43_recall_noshow_timers.md`** (the `arq` wording).
- **Tests (new):** `tests/integration/queue/test_recall_timers.py` (8, one on PostgreSQL in
  subprocesses).
- **Tests (updated):** `tests/integration/queue/conftest.py` (a clinic manager) and
  `tests/unit/security/test_site_scoped_queries.py` (the sweep's reasons).
- **Docs:** the M6 Status row, the sprint 7 row, the README Status block and the progress bars
  (`make milestone-progress ARGS='--assume-closed 43'`).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (238 files).
- [x] Full suite in UTC with PostgreSQL and Redis required: **1836 passed, 9 xfailed**. The first run
      failed only the site-scope guard, on the sweep's platform-wide read, which now has its reason.
- [x] `make milestone-progress-check ARGS='--assume-closed 43'`: up to date.
- [x] The Issue 43 tests:

```text
queue/test_recall_timers.py::test_a_called_ticket_recalls_exactly_once_then_becomes_a_no_show_that_frees_the_room PASSED
queue/test_recall_timers.py::test_the_patient_is_told_at_both_steps_and_how_to_rejoin PASSED
queue/test_recall_timers.py::test_a_patient_who_has_not_agreed_to_messages_is_not_texted_but_the_ledger_says_so PASSED
queue/test_recall_timers.py::test_the_timeout_is_the_queues_then_the_clinics_then_the_default PASSED
queue/test_recall_timers.py::test_a_clinic_manager_sets_the_clinics_timeout_and_a_queues_is_validated PASSED
queue/test_recall_timers.py::test_staff_can_recall_or_mark_a_no_show_at_once_and_the_timer_follows PASSED
queue/test_recall_timers.py::test_the_sweep_is_registered_once_and_replaced_on_restart PASSED
queue/test_recall_timers.py::test_timers_survive_a_restart_and_never_double_fire PASSED
4.47s call     queue/test_recall_timers.py::test_timers_survive_a_restart_and_never_double_fire
8 passed in 7.11s
```

- [x] **How to verify, step 3, in separate processes.** Each sweep in
      `test_timers_survive_a_restart_and_never_double_fire` is `run_recall_timer_sweep(moment=…)`, the
      scheduler's own entry point. It runs in a new `python -c` process against the throwaway
      PostgreSQL database, so nothing survives in memory from one step to the next. Tickets moved per
      process:

```text
04:59 after the call   one worker                               -> [0]      not yet due
05:01                  a new worker (the restart)               -> [1]      recalled, once
05:02                  two new workers at the same time         -> [0, 0]   neither recalls again
10:05                  two new workers race the second deadline -> [0, 1]   exactly one no-show
audit trail (system):  called → recalled, recalled → no_show
ledger:                ticket_recalled, ticket_no_show
```

- [x] **The two messages**, as the provider receives them:

```text
BK ClinicQ: ticket T005 at Hillbrow Community Health Centre was called and you have not arrived. We have called you once more: please come to Room 2 now. If you are not there within 5 minutes the ticket will be marked missed.
BK ClinicQ: ticket T005 at Hillbrow Community Health Centre was marked missed because you did not arrive after being called twice. To join the queue again, use BK ClinicQ on your phone, dial the BK ClinicQ USSD code, or ask at the front desk.
```

- [ ] Screenshot: no template, stylesheet or script changed; the buttons are Issue 50.

## Acceptance criteria

- [x] **A called ticket not attended within the timeout auto-recalls exactly once.** Sweeps at
      4:59, 5:01, 5:01 again and 9:00 after the call move `0, 1, 0, 0`. In separate processes, and with
      two at once, the recall happens once.
- [x] **A second timeout marks the ticket `no_show` and frees the room.** At 10:02 the ticket is
      `no_show` with `completed_at` stamped, nothing in the queue is `called` or `recalled`, and
      `call_next` calls the patient behind.
- [x] **The patient is notified at both steps with a clear explanation.** Two ledger rows
      (`ticket_recalled`, `ticket_no_show`), both `sent` through the provider, with the texts above:
      what happened, where to go, the minutes left, and how to join again. Without consent the move
      happens and the ledger row is `suppressed`.
- [x] **Timers survive a worker restart and do not double-fire.** Shown in fresh processes: no
      recall before the deadline, one recall after the restart, none from two concurrent workers,
      and one no-show from two racing workers. A restarted scheduler also carries exactly one
      `queue_recall_timers` job.
- [ ] **Staff can override either transition immediately from the dashboard.** *Partly: the API
      half is done; the dashboard buttons wait for Issue 50.* Over the transition route, the receptionist recalls
      one ticket and marks another a no-show at once. The timer then marks the recalled one a no-show
      five minutes after the staff recall, and never touches the other.
- [x] **Auto-transitions are audited as system actions, distinguishable from staff actions.** The two
      automatic rows have `actor = system` and `actor_role = system`, with contexts naming the recall
      and no-show timers. The staff call on the same ticket has `actor_role = staff`.

## Risk and rollback

**Migration `0021`** adds three nullable columns and is reversible (`alembic downgrade 0020`). The new
job is on by default wherever the scheduler runs. A clinic that has not changed its settings gets 5
minutes before a recall and 5 more before a no-show. That is a behaviour change at every clinic
calling tickets, and a manager can lengthen it to 60. The sweep's reads touch only called and
recalled tickets from today and yesterday, a handful at a time. Rollback is a revert plus the
downgrade. The job disappears with the code.

**Follow-ups:** Issue 50 puts recall and no-show buttons on the transition route. M9 swaps the logging
SMS provider for real delivery. Issue 67 may give queue messages their own preference category.

Closes #43
