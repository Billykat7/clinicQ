# PR: One join service for every channel, with the guards that keep it fair (Issue 40 / M6-40)

**Milestone:** [Milestone 6: Queue Engine Core](https://github.com/Billykat7/clinicQ/milestone/6) ·
**Issue:** [#40](https://github.com/Billykat7/clinicQ/issues/40) · **Builds on:** #39 (tickets,
merged in #161)

This PR adds `join_queue()`, the one function that puts a patient in a queue, and routes onto it.
Non-negotiable 1 says a ticket joined remotely and one issued at reception draw from **the same
sequence**, in arrival order. That holds here because web, USSD, WhatsApp and the front desk all
call the same function, and a guard test fails if anything else issues a ticket. A patient who
joins twice gets the ticket they already hold. A closed clinic, a closed or walk-in-only queue, a
full queue and a flood of joins are each refused with a sentence a patient can read. The abuse
guards reuse the kernel's sliding-window limiter.

## Summary

- **`join_queue()`** (`src/modules/queue/service.py`) decides in a fixed order. First, does this
  patient already hold a ticket here today? If so, it is returned. Then the clinic's join gate
  (hours, holidays, closures, listing), the queue's own rule (closed, walk-in only), the abuse guards
  for remote joins, and capacity. It then issues the ticket, audits it, records the clinic's
  view-to-join analytics for a phone join, and writes the queue snapshot through.
- **Routes** (`src/modules/queue/router.py`):
  - `POST /api/v1/clinics/{site_id}/queues/{queue_id}/tickets`: a patient's web join.
  - `POST /api/v1/sites/{site_id}/queues/{queue_id}/tickets`: the front desk's walk-in, with an
    optional phone number.
  - `GET /api/v1/sites/{site_id}/tickets`: the clinic's open tickets today.
  - `GET /api/v1/patients/me/tickets`: a patient's own tickets.

  A new ticket answers `201`, the one already held `200`. A refusal is `409` with
  `code: queue.join.<reason>`, or `429` with `Retry-After`.
- **USSD and WhatsApp** get no routes here. Their adapters (M10) call `join_queue()` directly after
  `patient_for_gateway()`, and the tests do exactly that.
- **One active ticket per patient per queue per day**, held twice: by the service's check, and by
  the partial unique index `uq_ticket_active_patient` (migration `0019`) for two joins at the same
  instant.
- **Abuse guards** use one new module-global limiter, `queue_join_limiter`, on the kernel's
  `SlidingWindowRateLimiter`, so the Redis backend shares the budget across workers:
  - per phone number on every remote channel (`QUEUE_JOIN_RATE_LIMIT_PER_PHONE`, 6 an hour);
  - per client address on the web path (`QUEUE_JOIN_RATE_LIMIT_PER_IP`, 30 an hour);
  - per clinic per service day on remote joins (`QUEUE_JOIN_SITE_DAILY_CAP`, 1500).

  Walk-ins are exempt from all three.
- **`contracts/queue.yaml`** (new) documents the four routes, every refusal and 25 examples. It
  starts the queue contract that Issue 47 completes.

## Design notes

**The order of the checks is the design.** The duplicate check comes first, so a patient who
rejoins after the queue fills up or the doors close still holds the place they had. The gate comes
before the abuse guards, so a closed clinic does not spend anyone's allowance. Capacity comes last,
**after** the number is allocated: allocating locks the queue's counter row (Issue 39), so every
other join on this queue waits, and the count of today's non-cancelled tickets cannot change between
reading it and inserting. A join that finds the queue full raises inside its savepoint. That rolls
the counter back, so the refused join leaves no gap
(`test_a_full_queue_refuses_and_gives_the_number_back`: T001, T002, refused, then T003). This is a
`COUNT` used as a capacity check under a lock. Numbers still come only from the counter.

**Walk-ins are exempt from the abuse guards.** Reception is authenticated staff. A per-phone or
per-clinic cap at the desk would turn away a patient standing at the counter, and the desk is not
where bulk abuse comes from. The clinic's daily cap is **checked** before issuing and **recorded**
only after a ticket is really issued, so refused and duplicate joins never spend a clinic's budget.
The phone and address hits are recorded even when refused, as the kernel limiter recommends, so
sustained abuse keeps tripping the limit.

**The ticket's name is now `walk_in_name`, and only a walk-in has one.** Issue 39 gave the ticket a
`display_name`. Wiring the web join exposed the problem: copying the patient's name onto the ticket
made a second, unconsented route from a patient's record to the public board.
`test_consent_is_not_bypassable.py` failed on it, which is what that guard exists for. A phone join
is now named only by the patient's own record, which reaches the board only through
`board_projection()` and consent (Issue 21). What remains is what the desk writes down to call a
walk-in by. It is staff-facing, redacted in audit diffs, and limited to walk-ins by
`ck_ticket_walk_in_name`. Migration `0019` renames the column; no route had written tickets
before it.

**Where the schedule comes from.** `join_queue()` takes the clinic's `OpeningSchedule` rather than
loading it, because the two doors read it differently. The desk uses `schedule_for(access)`, through
the site guard. A patient uses `published_schedules()`, which admits only listed clinics. An
unverified clinic's queue is therefore a `404` to a patient, as in discovery, and its desk can still
issue walk-ins to try the flow.

**Two guards and a contract change.**

- `tests/unit/queue/test_one_join_door.py` (new) fails, naming the file and line, if anything but
  `join_queue()` calls `issue_ticket`/`allocate_sequence` or builds a `Ticket`. Its fixtures prove it
  catches a USSD handler that allocates its own number and a route that builds the row.
- `test_mutations_are_audited.py` now covers the `queue` module; the join routes audit through the
  service.
- The contract harness learned one thing. The queue contract owns every `/tickets` route wherever it
  hangs (`/sites`, `/clinics`, `/patients/me`), through a `pattern` on its `Contract` entry. The
  prefix contracts cede those routes automatically, so a route cannot be claimed twice or silently
  skipped, and no exclusion list has to track another file
  (`test_a_ticket_route_belongs_to_the_queue_contract_wherever_it_hangs`).

**A bug the transcript found.** The first run of the HTTP transcript below showed the repeated join
returning `joined_at` **without its offset** (`2026-09-14T08:27:16.242407`), because SQLite drops it
on read-back. That is the class of bug the UTC rule warns about. `TicketOut` now reads stored times
through `stored_sast()`, and the duplicate-join test asserts the offset.

**Out of scope:** the walk-in form and ticket stub (Issue 51), the channel adapters (M10) and
cancellation (Issue 44). The site cap is per clinic, not per queue; each queue's own capacity is the
per-queue limit.

## Changes

- **`src/modules/queue/service.py`** (new): `join_queue`, `JoinResult`, `JoinRefusedError`, and the
  refusal sentences `QUEUE_FULL`, `SITE_DAILY_CAP`, `RATE_LIMITED`.
- **`src/modules/queue/router.py`**, **`schemas.py`** (new): the four routes; `JoinIn`, `WalkInIn`,
  `TicketOut`, `JoinOut`, `TicketListOut`. Registered in **`src/api/v1/router.py`**.
- **`src/modules/queue/tickets.py`:** `site_day_select`, `waiting_ahead`.
  **`sequence.py`:** `walk_in_name`, refused on anything but a walk-in.
- **`src/database/models/ticket.py`** and **`alembic/versions/0019_ticket_active_patient.py`**
  (new): `uq_ticket_active_patient`, the `walk_in_name` rename, `ck_ticket_walk_in_name`.
- **`src/commons/enums.py`:** `JoinRefusal`, `AuditEntityType.TICKET`, `walk_in_name` in
  `AUDIT_REDACTED_FIELDS`.
- **`src/core/rate_limit.py`:** `queue_join_limiter`, in `ALL_LIMITERS`. **`src/core/config.py`:**
  the four `QUEUE_JOIN_*` settings. **`.env.example`:** regenerated (`make env-example`).
- **`contracts/queue.yaml`** (new), **`contracts/README.md`**; `Contract.pattern` and `owns()` in
  **`tests/integration/contracts/test_openapi_contracts.py`**.
- **Tests (new):** `tests/integration/queue/test_join_queue.py` (16, two on PostgreSQL),
  `tests/integration/queue/conftest.py` (two open, listed clinics over HTTP) and
  `tests/unit/queue/test_one_join_door.py` (5).
- **Tests (updated):** `test_cross_tenant.py` (the `ticket` and `queues.tickets` pending entries
  became a case on `GET /sites/{site_id}/tickets`), `test_site_scoped_queries.py` (the duplicate
  check's reason), `test_mutations_are_audited.py`, and the Issue 39 tests for the rename.
- **Docs:** `docs/guideline.md` (non-negotiable 1 names its guard test), `docs/PRODUCT/03`, and the
  M6 Status row, sprint 6 row, README Status block and progress bars
  (`make milestone-progress ARGS='--assume-closed 40'`).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (233 files).
- [x] Full suite in UTC with PostgreSQL and Redis required: **1723 passed, 9 xfailed**. The first
      full run had 5 failures, all the contract drift test naming the new routes, which are now
      documented in `queue.yaml`. A unit run before that failed `test_consent_is_not_bypassable`
      on the copied patient name, as described above.
- [x] `make milestone-progress-check ARGS='--assume-closed 40'`: 14 milestones up to date.
- [x] `alembic check` and the round trip (`test_alembic_baseline.py`) pass with `0019`.
- [x] The Issue 40 tests and the contract suite (47 passed):

```text
queue/test_join_queue.py::test_all_four_sources_join_through_the_one_service_and_share_one_sequence PASSED
queue/test_join_queue.py::test_a_remote_join_and_a_walk_in_in_the_same_second_get_adjacent_numbers PASSED
queue/test_join_queue.py::test_a_second_join_by_the_same_patient_returns_the_existing_ticket PASSED
queue/test_join_queue.py::test_joining_a_closed_clinic_is_refused_with_a_reason_a_patient_understands PASSED
queue/test_join_queue.py::test_a_walk_in_only_queue_refuses_a_phone_and_takes_the_desk PASSED
queue/test_join_queue.py::test_a_deactivated_queue_refuses_every_door PASSED
queue/test_join_queue.py::test_a_full_queue_refuses_and_gives_the_number_back PASSED
queue/test_join_queue.py::test_the_rate_limiter_blocks_bulk_joins_from_one_phone PASSED
queue/test_join_queue.py::test_the_web_path_is_limited_per_address PASSED
queue/test_join_queue.py::test_a_clinic_takes_a_capped_number_of_remote_joins_a_day_and_the_desk_keeps_working PASSED
queue/test_join_queue.py::test_consent_given_at_join_is_persisted_with_the_ticket_and_the_audit_keeps_no_reason PASSED
queue/test_join_queue.py::test_a_walk_in_with_a_phone_is_linked_to_that_patient_and_cannot_hold_two PASSED
queue/test_join_queue.py::test_the_desk_cannot_issue_into_another_clinics_queue PASSED
queue/test_join_queue.py::test_a_remote_join_without_its_patient_is_a_programming_error PASSED
queue/test_join_queue.py::test_a_phone_and_the_desk_racing_twenty_times_always_get_adjacent_numbers PASSED  (PostgreSQL)
queue/test_join_queue.py::test_two_joins_by_one_patient_at_the_same_instant_make_one_ticket PASSED          (PostgreSQL)
queue/test_one_join_door.py (5) PASSED
contracts/test_openapi_contracts.py (26, now including [queue]) PASSED
```

- [x] **How to verify, over HTTP.** The real app (`create_app`) on the test fixture's two open,
      listed clinics, with the requests made in this order. Response bodies are trimmed to the
      fields that matter. This is the first run, before the offset fix, which is how the naive
      `joined_at` in 1b was found:

```text
1a POST /api/v1/clinics/{A}/queues/{triage}/tickets  (patient)
  -> {"status": 201, "number": "T001", "source": "web", "created": true, "waiting_ahead": 0, "message": "You are T001."}
1b POST the same again, same patient
  -> {"status": 200, "number": "T001", "source": "web", "joined_at": "2026-09-14T08:27:16.242407", "created": false,
      "message": "You already hold T001 in this queue."}
2a POST /api/v1/clinics/{A}/queues/{triage}/tickets  (another patient)
  -> {"status": 201, "number": "T002", "source": "web", "joined_at": "2026-09-14T08:27:16.272746+02:00", "waiting_ahead": 1}
2b POST /api/v1/sites/{A}/queues/{triage}/tickets  (the desk, same second)
  -> {"status": 201, "number": "T003", "source": "walk_in", "joined_at": "2026-09-14T08:27:16.284447+02:00", "waiting_ahead": 2}
3  POST a join during an announced closure
  -> {"status": 409, "detail": "Hillbrow Community Health Centre is closed: Water outage", "code": "queue.join.clinic_closed"}
3b POST a walk-in during the same closure
  -> {"status": 409, "detail": "Hillbrow Community Health Centre is closed: Water outage", "code": "queue.join.clinic_closed"}
```

- [ ] Screenshot: no template, stylesheet or script changed; the walk-in form is Issue 51.

## Acceptance criteria

- [x] **A remote join and a walk-in issued in the same second occupy adjacent numbers in one
      sequence.** Two ways:
      - Over HTTP, 2a and 2b above: the same second, T002 then T003.
      - On PostgreSQL, a USSD join and a walk-in released together by a barrier, twenty times.
        Every pair is `n, n+1`, and the pairs run 1–2, 3–4 … 39–40
        (`test_a_phone_and_the_desk_racing_twenty_times_always_get_adjacent_numbers`).
- [x] **A second join attempt by the same patient on the same queue returns the existing ticket, not
      a new one.** Over HTTP, `201` then `200` with the same id, *You already hold T001 in this
      queue.*, one row and the same `joined_at`. On PostgreSQL, two joins by one patient at the same
      instant, five rounds: one ticket each round, one `created: true` and one `false`.
- [x] **Joining a closed or full queue is rejected with an explanatory error.** Each refusal has
      its own code and sentence:
      - clinic closed: *Hillbrow Community Health Centre is closed: Water outage*;
      - queue deactivated: *This queue is not taking patients at the moment.*;
      - walk-in only: *This queue takes walk-in patients only. Please join at the clinic's front
        desk.*;
      - full: *This queue is full for today. Please try another queue, or come back tomorrow.*

      Each is tested, and the closure shuts the desk too.
- [x] **The rate limiter blocks bulk joins from one phone number.** With a budget of two, one USSD
      number joining three queues at two clinics is refused on the third with `rate_limited`,
      while a different number still joins. On the web path, three patients from one address with a
      budget of two: `201, 201, 429` with `Retry-After: 3600`. The per-clinic cap of two refuses a
      third remote join with `site_daily_cap`, while the desk still issues T003.
- [x] **All four sources are exercised by tests against the same service function.** A spy on
      `join_queue` records `web, walk_in, ussd, whatsapp` from the two routes and the two
      adapter-style calls, and the tickets are T001–T004 in that order.
      `test_one_join_door.py` fails if a second way to issue a ticket appears.
- [x] **Consent captured at join time is persisted with the ticket.** `comment_consent: true` and
      the trimmed reason are on the row. The audit row is `create`/`ticket` by `patient:<id>`, with
      context *joined Triage as T001 via web*, the reason `<redacted>` and the consent recorded. The
      queue snapshot reads 1 waiting in the same request.

## Risk and rollback

**Migration `0019`** renames `ticket.display_name` to `walk_in_name`, adds a check constraint and a
partial unique index. No route wrote tickets before this PR, so there is no data to reinterpret. It
is reversible (`alembic downgrade 0018`). The new routes are additive. The limiter defaults to
process-local windows, like every kernel limiter, and needs `RATE_LIMIT_BACKEND=redis` to hold one
budget across workers. That is the existing pen-test finding F-02, not a new one. Rollback is a
revert plus `alembic downgrade 0018`.

**Follow-ups:** the lifecycle (Issue 41) must call `on_queue_changed` on every status change, as
this join does. A per-queue remote cap, if clinics ask for one, would sit beside the per-clinic cap.
The desk's walk-in form (Issue 51) should offer the phone number as optional, as the API does.

Closes #40
