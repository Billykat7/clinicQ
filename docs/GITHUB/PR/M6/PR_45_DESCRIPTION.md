# PR: Move a patient from triage to the doctor to the pharmacy without rejoining (Issue 45 / M6-45)

**Milestone:** [Milestone 6: Queue Engine Core](https://github.com/Billykat7/clinicQ/milestone/6) ·
**Issue:** [#45](https://github.com/Billykat7/clinicQ/issues/45) · **Builds on:** #41 (the lifecycle)
and #25 (queues), merged; #44 merged in #166

A clinic visit is a journey, not a line. With this PR, staff move a patient on with one action:
- the ticket in the queue they are leaving becomes `transferred`;
- the patient takes the next number in the new queue without rejoining;
- they get a message with the new queue, number and expected wait.

Every ticket of the journey belongs to one **visit**, so the visit keeps its history: its legs in
order, and its total time once it ends. A transfer into a queue that is closed or full is refused
with a clear message and changes nothing. The end-to-end triage → doctor → pharmacy journey is one
integration test.

## The placement default, and why

**Placement in the target queue is a site setting, and the default is arrival order.**
`transfer_placement` is either `arrival_order` or `back_of_line`:

- **`arrival_order`** (the default) puts the patient among the people waiting in the new queue
  **by when each visit began**. A patient triaged at 07:40 goes ahead of one who walked into the
  doctor's queue at 08:10.
- **`back_of_line`** puts them behind everyone, as if they had just joined.

The default follows from non-negotiable 1, "fair by arrival order", applied to the whole visit
rather than to each line. Being moved on by the clinic should not cost a patient the time they have
already spent there. The obvious alternative, the back of the line, punishes exactly the patients
who went through triage first, which is how a clinic is meant to run. It also teaches patients to
skip triage.

`back_of_line` exists for a clinic that prefers the simpler rule, and a manager switches with
`PUT /sites/{site_id}/settings/transfers` (audited). Both behaviours are tested:
- arrival order lands a visit that began at +20 minutes between waiting visits from +10 and +30;
- the back of the line lands it behind all three.

## Summary

- **`transfer_ticket(db, ticket_id, target, *, actor, reason)`** (`src/modules/queue/transfer.py`),
  in one savepoint:
  1. Moves the source to `transferred` through `transition_ticket()`. Only `waiting` (the wrong
     line) and `in_progress` (seen, on to the next step) may be transferred, per #41's table.
  2. Issues the new ticket from the target's sequence with the same `visit_id` and
     `transferred_from_id`, placed by the clinic's setting.
  3. Checks the target's capacity under its counter lock, and audits the new ticket.

  A refusal of any kind undoes all three.
- **Refusals** (409, nothing changed): `ticket.transfer.queue_closed` (*This queue is not taking
  patients at the moment.*), `queue_full`, `same_queue`, `already_there` (the patient already holds a
  ticket there), and #41's `ticket.transition.illegal` for a called ticket.
- **`visit`** (migration `0023`) holds the site, the patient (nullable, as for walk-ins) and
  `started_at`. `visit_summary()` derives the legs in issue order, `ended_at` (when the last leg is
  terminal and not `transferred`) and total minutes.
- **`ticket.order_key`** is the order a queue is called in. It is the sequence unless a transfer (or
  #46's priority override) places the ticket between two neighbours. `CALL_ORDER` and the position
  helpers (`waiting_ahead`, `ahead_of`, `behind`) read it, so positions stay derived and nobody
  else's row is written.
- **Routes:**
  - `POST /api/v1/sites/{site_id}/tickets/{ticket_id}/transfer`: body `{queue_id, reason}`, where
    `reason` is a closed `TransferReason`.
  - `GET /api/v1/sites/{site_id}/visits/{visit_id}` and `GET /api/v1/sites/{site_id}/visits` (today).
  - `GET/PUT /api/v1/sites/{site_id}/settings/transfers`.

  The patient's own `GET /patients/me/tickets` shows the new ticket, with its wait.
- **The patient's message** is the new `TICKET_TRANSFERRED` template, through the notification
  service (ledger, consent, provider), like #43's.

## Design notes

**Not a join.** A transfer is the clinic moving somebody already in the building, so it skips the
join gate, the rate limits and the "you already hold" answer. The pharmacy in the tests takes
walk-ins only, and a transfer into it works: a patient sent there by the doctor did not join from a
phone. It still draws the number from the same sequence as every join. The one-join-door guard lists
`transfer_ticket` as the second allowed caller of `issue_ticket`, with that reason.

**Nothing half-happens.** The source's transition, the new ticket, its number and its audit row are
all inside one savepoint. When the target turns out to be full, the patient already holds a ticket
there, or the queue is inactive, leaving the savepoint rolls everything back. The source is still
`in_progress`, the target has no new ticket, and its number is not spent (after raising the
capacity, the next transfer is `D002`, not `D003`).

**The visit is derived, not maintained.** No column holds an end time or a total that could drift
from the tickets. The last leg's `completed_at` minus the visit's `started_at` is the total, and the
E2E test recomputes it from the rows and compares.

**The migration works on a populated database.** `0023` backfills a visit for every existing ticket
(a pre-visit ticket was a journey of one leg) and copies `sequence` into `order_key` before making
both columns required. A PostgreSQL test runs it: upgrade to `0022`, insert two tickets by SQL,
upgrade to head, and each has its own visit starting at its join time and keeps its place.

**Guards.** The transfer's reads of the ticket's own queue, visit and clinic (`transfer_ticket`), of
the target queue's waiting tickets (`_placement_key`) and of the patient for the message (`_notify`)
are listed in the site-scope guard with reasons. The cross-tenant suite gains a `visit` case on
`GET /sites/{site_id}/visits`. The queue contract's pattern now owns `/visits` as well as `/tickets`.
The duplicate-ticket detector moved to `sequence.is_second_active_ticket`, shared by joins and
transfers.

**Out of scope:** staff notes on a visit (Issue 53) and visit-time reports (Issue 90, which reads
`visit_summary`).

## Changes

- **`src/modules/queue/transfer.py`** (new): `transfer_ticket`, `TransferResult`,
  `TransferRefusedError`, `visit_summary`, `VisitSummary`.
- **`src/database/models/visit.py`** (new), **`ticket.py`** (`visit_id`, `transferred_from_id`,
  `order_key`, `ix_clinicq_ticket_visit`), **`site.py`** (`transfer_placement` and its check);
  **`alembic/versions/0023_visits_and_call_order.py`** (new, with the backfill).
- **`src/modules/queue/sequence.py`:** `issue_ticket` starts or continues a visit and takes an
  `order_key`; `is_second_active_ticket`. **`tickets.py`:** `CALL_ORDER` by `order_key`, `ahead_of`,
  `behind`. **`cancellation.py`**, **`service.py`:** use them.
- **`src/modules/queue/router.py`**, **`schemas.py`:** the transfer and visit routes and their models.
  **`src/modules/sites/router.py`**, **`schemas.py`:** the transfer settings routes.
- **`src/commons/enums.py`:** `TransferReason`, `TransferPlacement`,
  `SITE_DEFAULT_TRANSFER_PLACEMENT`, and the `TICKET_TRANSFERRED` template (category and urgency).
  **`src/modules/notifications/templates.py`:** its SMS.
- **`contracts/queue.yaml`** (transfer, visits) and **`contracts/sites.yaml`** (settings);
  `tests/integration/contracts/test_openapi_contracts.py` (the queue pattern includes `/visits`).
- **`docs/PRODUCT/03-booking-and-queue.md`:** transfers and the `visit` table.
- **Tests (new):** `tests/integration/queue/test_transfer.py` (7, one on PostgreSQL).
- **Tests (updated):** the one-join-door, site-scope and cross-tenant guards;
  `test_sequence_concurrency.py` and `test_ticket_indexes.py` (their hand-built rows carry a visit and
  an order key).
- **Docs:** the M6 Status row, the sprint 7 row, the README Status block and the progress bars
  (`make milestone-progress ARGS='--assume-closed 45'`).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (241 files).
- [x] Full suite in UTC with PostgreSQL and Redis required: **1853 passed, 9 xfailed**.
- [x] `make milestone-progress-check ARGS='--assume-closed 45'`: up to date.
- [x] The Issue 45 tests:

```text
queue/test_transfer.py::test_triage_to_doctor_to_pharmacy_is_one_visit_with_three_tickets PASSED
queue/test_transfer.py::test_a_transfer_into_an_inactive_queue_is_refused_and_changes_nothing PASSED
queue/test_transfer.py::test_a_transfer_into_a_full_queue_is_refused_and_changes_nothing PASSED
queue/test_transfer.py::test_the_same_queue_a_called_ticket_and_a_queue_already_held_are_refused PASSED
queue/test_transfer.py::test_a_transfer_lands_by_arrival_order_by_default_or_at_the_back_if_the_clinic_says PASSED
queue/test_transfer.py::test_a_clinic_manager_chooses_the_placement_and_the_change_is_audited PASSED
queue/test_transfer.py::test_the_migration_gives_every_existing_ticket_a_visit_and_its_call_order PASSED
7 passed in 3.72s
```

- [x] **How to verify, over HTTP.** One patient's journey on the app with the fixture's open
      clinic: a web join, then call, start and transfer at each step, then done. The messages are
      what the SMS provider received. Every step here ran in the same second, hence 0.0 minutes; the
      test compares the total with the rows:

```text
join Triage -> T001
POST transfer T001 -> Doctor: Moved to Doctor as D001. Expected wait ~0–10 min (approximate). (from_ticket transferred)
POST transfer D001 -> Pharmacy: Moved to Pharmacy as P001. Expected wait ~0–10 min (approximate). (from_ticket transferred)
GET visit: {"legs": [["T001", "transferred"], ["D001", "transferred"], ["P001", "done"]], "ended": true, "total_minutes": 0.0}
SMS: BK ClinicQ: at Hillbrow Community Health Centre you are now in the Doctor queue as ticket D001. Expected wait ~0–10 min (approximate). You do not need to join again.
SMS: BK ClinicQ: at Hillbrow Community Health Centre you are now in the Pharmacy queue as ticket P001. Expected wait ~0–10 min (approximate). You do not need to join again.
```

- [ ] Screenshot: no template, stylesheet or script changed; the dashboard's transfer action is M7.

## Acceptance criteria

- [x] **A transferred patient appears in the target queue without rejoining.** D001 in the doctor's
      queue and P001 in the pharmacy's, issued by staff moves with no join request. The patient's own
      ticket list shows the new ticket with its place and wait.
- [x] **The visit record links every leg of the journey and total visit time is derivable.** One
      visit, three legs in order (`T001 transferred`, `D001 transferred`, `P001 done`), each pointing
      back at the one before. `total_minutes` equals the last leg's `completed_at` minus the visit's
      `started_at`, recomputed from the rows.
- [x] **The transfer is audited with the staff member and reason.** Four rows by
      `desk.a@clinicq.example`: `T001: in_progress → transferred (transferred to Doctor, reason
      next_step)`, `D001 in Doctor: transferred from T001 in Triage, reason next_step`, and the same
      pair for the pharmacy.
- [x] **The patient is notified of the new queue, number and estimate.** Two `ticket_transferred`
      ledger rows and the two messages above: queue, number and the wait range. The web response
      carries the same.
- [x] **A transfer to an inactive or full queue is rejected with a clear message.** Inactive:
      `ticket.transfer.queue_closed`, *This queue is not taking patients at the moment.* Full:
      `ticket.transfer.queue_full`, *That queue is full for today. Choose another queue.* In both
      cases the source is still `in_progress`, and no ticket or number was spent.
- [x] **End-to-end triage → doctor → pharmacy is covered by an integration test.**
      `test_triage_to_doctor_to_pharmacy_is_one_visit_with_three_tickets`, over HTTP, from the
      patient's join to the pharmacy's `done`.

## Risk and rollback

**Migration `0023`** creates `visit`, adds three ticket columns and one site column, and backfills
existing tickets before tightening `NOT NULL` (tested on a populated database). It is reversible
(`alembic downgrade 0022`): the legs then stand as unrelated tickets. **The call order changes
source**, from `sequence` to `order_key`, but the backfill makes them equal, and joins set
`order_key` to the sequence, so no queue changes order until a transfer or override places someone.
Rollback is a revert plus the downgrade.

**Follow-ups:** M7 puts a transfer action on the dashboard. Issue 90 reports visit times from
`visit_summary`. Issue 46 reuses `order_key` for priority overrides.

Closes #45
