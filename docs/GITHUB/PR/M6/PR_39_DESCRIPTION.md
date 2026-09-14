# PR: Tickets numbered by the database, safe when two receptionists tap at once (Issue 39 / M6-39)

**Milestone:** [Milestone 6: Queue Engine Core](https://github.com/Billykat7/clinicQ/milestone/6) ·
**Issue:** [#39](https://github.com/Billykat7/clinicQ/issues/39) · **Builds on:** #17 (patients) and
#25 (queues), both merged

This PR adds the `ticket` table that the rest of the product is built on, and the one guarantee it
exists for: **two joins at the same instant can never get the same number.** The number is allocated
inside PostgreSQL by a single statement that increments a counter row and returns the new value. It
is stored under a unique constraint on `(queue_id, service_day, sequence)`. Nothing computes
`COUNT(*) + 1` in Python. The service day is the Johannesburg date, so numbering restarts at SAST
midnight, not at 02:00. A walk-in with no phone is a ticket with no patient, and no placeholder
patient row is created. Reference codes are six characters that survive being read aloud.

Every criterion below was demonstrated on PostgreSQL 18, not asserted. That includes a negative
control: the naive allocator, run in the same harness, collides 91 times in 100.

## Summary

- **`ticket` and `ticket_sequence`** (migration `0018`). The constraints are the point:
  `uq_ticket_queue_id_service_day_sequence`, `uq_ticket_reference_code`,
  `ck_ticket_patient_or_walk_in`, `ck_ticket_status`, `ck_ticket_source` and
  `ck_ticket_sequence_positive`. The three indexes are `ix_clinicq_ticket_board`
  `(queue_id, service_day, status, sequence)`, `ix_clinicq_ticket_patient` (partial) and
  `ix_clinicq_ticket_site_day`.
- **`allocate_sequence()`** runs `INSERT … ON CONFLICT (queue_id, service_day) DO UPDATE SET
  last_value = last_value + 1 RETURNING last_value`. The row lock serialises joins on one queue and
  day, and only those. A rollback returns its number, so the day has no gaps.
- **`issue_ticket()`** is the storage primitive behind joining. It allocates the number, draws a
  reference code (redrawn inside a savepoint on collision), and inserts a `waiting` ticket. Whether a
  patient *may* join is Issue 40's `join_queue()`.
- **Reference codes** use `23456789ABCDEFGHJKMNPQRSTUVWXYZ`, which has no `0`/`O` and no
  `1`/`I`/`L`. Six characters give 887 million codes. A code is printed as `K7M-4QP` and parsed back
  whatever the case or separators. A confusable character is refused, never guessed.
- **`board_select()` and `patient_tickets_select()`** are the two reads later issues use, each
  running on its index, as `EXPLAIN` shows.
- **Discovery now counts real tickets.** `read_waiting_counts()` returns today's `waiting` tickets per
  queue in one grouped query, as its docstring said Issue 39 would. An empty queue is a measured
  `0` ("No one waiting"). `None` still means nobody counted.
- **`TicketFactory`** is real now. `create()` issues through `issue_ticket()`, so factory numbers come
  from the database counter too.

## Design notes

**Upsert on a counter row, not `SELECT … FOR UPDATE` on the tickets.** The spec allowed either.
`SELECT max(sequence) … FOR UPDATE` cannot lock a row that does not exist yet, so the first two
tickets of a day could still collide. The upsert creates the day's row and locks it in one statement.
The same statement is the whole midnight reset, because a new day is a new row: nothing runs at
midnight, so nothing can fail to run.

**Gapless, and the price of it.** Because the counter joins the caller's transaction, a join that
fails after allocating rolls its number back (`test_a_rolled_back_join_leaves_no_gap`). The cost is
that joins on one queue wait for each other until commit. That is why a join transaction must stay
short (allocate, insert, audit, commit), and why the lock is scoped to one queue's day:
`test_joins_wait_only_for_their_own_queue` shows the pharmacy allocating while triage's lock is
held, and a second triage join timing out on it. A PostgreSQL `SEQUENCE` was rejected: it cannot
reset per day and it leaves gaps on rollback.

**The service day is a column.** `service_day` is written from `business_date(joined_at)` rather
than derived in each query. The unique constraint and the board index read it directly, and no
query can take the date in the wrong zone. The test issues at 01:59 SAST, which is 23:59 UTC on the
*previous* date, and still gets the new day's `T002`.

**A walk-in without a patient, enforced both ways.** `patient_id` is nullable, and
`ck_ticket_patient_or_walk_in` refuses a web, USSD or WhatsApp ticket without one. The only way to
lose a patient is a walk-in, and a remote join can never quietly become anonymous. The foreign keys
are `RESTRICT`: a queue, clinic or patient with history is deactivated or soft-deleted, never
removed from under its tickets.

**Reference codes are unique platform-wide, and not a secret.** They are random rather than derived
from the number, so yesterday's `A043` and today's differ. They are drawn with `secrets`, but they
only find a ticket at the desk and never authenticate anyone. The bulk-seeded index test found the
real cost of global uniqueness: 20,000 draws collide about one time in five. `issue_ticket()`
redraws, and the seeding now de-duplicates. At ten million tickets a single insert collides about 1%
of the time, so the redraw holds for years. Length is worth revisiting with the QR code (Issue 70).

**Stored `number`.** `A043` is stored because it is what was printed and read out. A queue whose
prefix is renamed at noon must not renumber the morning.

**Status values are frozen in the migration.** `ck_ticket_status` lists today's eight
`TicketStatus` values. A later member arrives with its own migration, which is the conventions
guard's stated reason for keeping migrations out of its scope.

**The discovery change is visible.** A card that read *Queue length not reported yet* now reads
*No one waiting, counted 12 s ago* when a clinic has no tickets. That is the honest reading once
tickets exist: the ticket table is the queue. No template changed. Four M5 tests asserted the old
text or `null`. They now assert the real count: two walk-ins read as `2`, the empty queues as `0`,
and a reader that cannot count still gives `null`.

**Guards.** `read_waiting_counts` and `patient_tickets_select` are listed in
`test_site_scoped_queries.py`, each with its reason. The first counts queues its caller already
narrowed (published clinics, or the sweep). The second is a patient's own tickets, scoped by
identity rather than by clinic. The cross-tenant suite lists `ticket` as pending on Issue 40, whose
routes are the first to read tickets.

**Out of scope:** joining (Issue 40), status changes (Issue 41) and the QR image (Issue 70).

## Changes

- **`alembic/versions/0018_tickets.py`** (new): both tables, the constraints and indexes above, and
  a downgrade that refuses while tickets exist.
- **`src/database/models/ticket.py`** (new): `Ticket`, `TicketSequence`. Also registered in
  **`src/database/models/__init__.py`**.
- **`src/modules/queue/sequence.py`** (new): `allocate_sequence`, `issue_ticket`,
  `new_reference_code`, `format_reference_code`, `parse_reference_code`, `format_ticket_number`,
  `REFERENCE_ALPHABET`.
- **`src/modules/queue/tickets.py`** (new): `board_select`, `patient_tickets_select`.
- **`src/commons/enums.py`:** `TICKET_ACTIVE_STATUSES`, derived from the terminal set.
- **`src/modules/queues/live.py`:** `read_waiting_counts` counts today's waiting tickets.
- **Docstrings brought up to date:** `src/modules/queue/__init__.py`, `snapshot.py`,
  `src/database/models/site_queue_snapshot.py`.
- **Tests (new):** `tests/integration/queue/test_sequence_concurrency.py` (7, PostgreSQL),
  `test_ticket_indexes.py` (2, PostgreSQL), `test_tickets.py` (6) and
  `tests/unit/queue/test_reference_codes.py` (13 with parameters).
- **Tests (updated):** `tests/factories.py` (a real `TicketFactory`), `test_factories.py` and
  `test_queues_api.py` (real tickets instead of the stub), `test_cross_tenant.py` (pending entry),
  `test_site_scoped_queries.py` (two reasons), and the four discovery and snapshot tests described
  above.
- **Docs:** `docs/PRODUCT/03-booking-and-queue.md` (the `ticket` and `ticket_sequence` rows), and the
  M6 milestone Status row, sprint 6 row, README Status block and progress bars
  (`make milestone-progress ARGS='--assume-closed 39'`).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (230 files).
- [x] Full suite in UTC with PostgreSQL and Redis required, as CI runs it:
      **1692 passed, 9 xfailed**. The first run had 4 failures: the M5 tests that asserted a
      "not measured" queue length, updated as described above.
- [x] `make milestone-progress-check ARGS='--assume-closed 39'`: 14 milestones up to date.
- [x] `alembic upgrade head` on the development database (0014 → 0018), then autogenerate check
      and the round-trip in `test_alembic_baseline.py`: 6 passed.
- [x] The Issue 39 tests, verbose, with the measurements the tests log (`--log-cli-level=INFO`):

```text
test_100_concurrent_joins_get_1_to_100_with_no_gaps_or_repeats                PASSED
  100 joins: 1–100, no gaps, no repeats; PostgreSQL saw up to 20 join transactions open at once
  and up to 19 waiting on the counter's lock
test_count_plus_one_repeats_numbers_under_the_same_load_and_the_constraint_refuses_them PASSED
  COUNT(*)+1: 91 of 100 joins computed a number already taken and were refused by the constraint
  (PostgreSQL saw up to 20 join transactions open at once)
test_a_duplicate_inserted_by_hand_is_refused_by_the_constraint                PASSED
test_a_rolled_back_join_leaves_no_gap                                         PASSED
test_joins_wait_only_for_their_own_queue                                      PASSED
test_numbering_restarts_at_johannesburg_midnight_not_utc_midnight             PASSED
test_a_walk_in_needs_no_patient_and_a_remote_join_cannot_lack_one             PASSED
test_the_board_query_for_one_queue_runs_on_the_board_index                    PASSED
test_a_patients_own_tickets_run_on_the_patient_index                          PASSED
test_tickets.py (6) and test_reference_codes.py (13)                          PASSED
26 passed in 37.06s
```

The "open at once" figure is read from `pg_stat_activity` on a separate connection during the race,
not counted by the threads. The joins share a pool of 20 connections, which is how the application
runs and what CI's PostgreSQL (100 connections for all parallel workers) allows. The hundred joins
therefore contend for 20 connections and one row lock.

- [x] **How to verify, step 2, in psql** on the development database, after a ticket was issued
      through `issue_ticket()` (`T001`, reference `5CU-CHK`). The demo row was removed afterwards:

```text
btk=# INSERT INTO clinicq.ticket (id, site_id, queue_id, service_day, sequence, number, reference_code, source, joined_at)
      SELECT gen_random_uuid()::text, site_id, queue_id, service_day, sequence, number, 'ZZZZZZ', 'walk_in', now()
      FROM clinicq.ticket WHERE queue_id = '01a09c0b-…' AND sequence = 1;
ERROR:  duplicate key value violates unique constraint "uq_ticket_queue_id_service_day_sequence"
DETAIL:  Key (queue_id, service_day, sequence)=(01a09c0b-ce5e-7037-8071-cd12f438bd3d, 2026-09-14, 1) already exists.

btk=# INSERT … sequence 2, source 'web', no patient_id …;
ERROR:  new row for relation "ticket" violates check constraint "ck_ticket_patient_or_walk_in"
```

- [x] **`EXPLAIN (ANALYZE, BUFFERS)` of `board_select()`** over 20,000 analysed tickets, with the
      planner not nudged:

```text
Sort  (actual time=1.776..1.781 rows=100.00 loops=1)
  Sort Key: sequence
  ->  Index Scan using ix_clinicq_ticket_board on ticket  (actual time=0.896..0.965 rows=100.00 loops=1)
        Index Cond: ((queue_id = '…') AND (service_day = '2026-09-14') AND (status = ANY ('{called,in_progress,recalled,waiting}')))
        Filter: (site_id = '…')
Execution Time: 1.952 ms
```

- [ ] Screenshot: no template, stylesheet or script changed. The discovery card's wording changes
      only through the count it is given.

## Acceptance criteria

- [x] **100 concurrent joins produce 100 unique consecutive numbers, proven by a concurrency
      test.** `test_100_concurrent_joins_get_1_to_100_with_no_gaps_or_repeats` releases 100 threads
      through a barrier. It asserts the sequences are exactly 1–100, the stored numbers are
      `T001`–`T100` and the counter reads 100. PostgreSQL showed 19 joins waiting on the lock at
      once. The negative control shows the harness detects the bug: `COUNT(*) + 1` produced 91
      refused duplicates.
- [x] **The unique constraint on (queue, service_day, sequence) makes a duplicate impossible at the
      database level.** Refused in psql above and in
      `test_a_duplicate_inserted_by_hand_is_refused_by_the_constraint`, both by raw SQL that bypasses
      the application.
- [x] **Numbers restart at 1 at the start of each service day, tested across a midnight boundary.**
      On PostgreSQL, tickets at 23:57–23:59 SAST are `T001`–`T003`, and 00:01 is `T001` on the next
      service day. 01:59 SAST, still the previous date in UTC, is `T002` on the new day. The
      same boundary is tested on SQLite in `test_the_service_day_is_the_johannesburg_date`.
- [x] **The board query for one queue runs on an index, confirmed by `EXPLAIN`.** `Index Scan using
      ix_clinicq_ticket_board`, with no sequential scan, over 20,000 analysed rows and without
      disabling sequential scans. The patient lookup uses `ix_clinicq_ticket_patient` in the same
      way.
- [x] **A walk-in with no patient record is representable without a placeholder patient row.** A
      walk-in is stored with `patient_id NULL` and the patient table still holds one row. The check
      constraint refuses the web ticket without a patient, in psql and in the test.
- [x] **Reference codes are short, unambiguous and safe to read aloud.** Six characters from a
      31-symbol alphabet with none of `0 O 1 I L`, printed in two groups of three. A mistyped
      confusable is refused rather than matched to another ticket
      (`test_a_mistyped_code_is_refused_rather_than_guessed`). A collision is redrawn and keeps the
      allocated number (`test_a_colliding_reference_code_is_redrawn_and_keeps_the_number`).

## Risk and rollback

**Migration `0018`** adds two tables and changes nothing existing. Its downgrade refuses while
`ticket` holds rows, so a rollback cannot silently delete a clinic's queue history; on a database
with tickets, a revert needs the rows archived first. No route writes tickets yet, so until Issue 40
merges the only visible change is the discovery wording described above. Rollback before tickets
exist is a revert of this PR plus `alembic downgrade 0017`.

**Follow-ups:** Issue 40 must call `on_queue_changed` after every join, so the snapshot sees new
tickets; Issue 41's status writes must do the same. The reference-code length should be reviewed
with Issue 70's QR work at scale.

Closes #39
