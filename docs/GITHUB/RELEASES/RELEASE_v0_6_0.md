# Release v0.6.0: Queue Engine Core

**Date:** 2026-09-14 · **Milestone:** M6 · **Issues closed:** 39–47

A pre-release. v0.5.0 let a patient find a clinic; this one gives them **a place in its queue**. It
builds the engine the rest of the project is a view onto: a ticket numbered by the database, one join
service for every channel, a strict lifecycle, a wait shown as an honest range, recall and no-show
timers, cancellation, transfers that keep the visit, and priority overrides that are on the record.

There is still **no screen that uses it**. The dashboard (M7), the waiting-room board (M8), the
patient's ticket page (M9) and the USSD and WhatsApp menus (M10) are all built on this engine's API.
A patient's place and wait are correct on every read, but nothing pushes a change to an open screen
until the board stream (Issue 57).

Two rules shape everything in it, and each is enforced where a bug cannot get round it. **One queue,
not two:** a phone join and a walk-in take numbers from the same sequence, allocated inside
PostgreSQL under a unique constraint, and only `join_queue()` can issue a ticket. **The status changes
in one place:** `transition_ticket()` is the only writer of `ticket.status`, the model refuses any
other write at runtime, and a guard test fails the build on one in the source.

All nine pull requests merged on 14 September 2026, each after its checks were green and before the
next branched from `main`: #161 → #162 → #163 → #164 → #165 → #166 → #167 → #168 → #169. The tag is
cut from `main` after #169 merges.

## What shipped

- **Tickets numbered by the database** (Issue 39, PR #161; migration `0018`). A ticket's number is
  allocated by `INSERT … ON CONFLICT DO UPDATE … RETURNING` on a counter row per queue and
  Johannesburg service day, never by `COUNT(*) + 1`. `uq_ticket_queue_id_service_day_sequence` refuses
  a repeat whatever the application does. **100 concurrent joins get 1–100** with no gap or repeat,
  and the naive allocator fails the same harness. The number restarts at `#001` at Johannesburg
  midnight, including at 01:59 SAST, which is still the day before in UTC. A walk-in needs no patient
  row. The six-character reference code has no `0`/`O` or `1`/`I`/`L`.
- **One join service for every channel** (Issue 40, PR #162; migration `0019`). `join_queue()` serves
  the web, USSD, WhatsApp and the reception desk. A patient joining twice gets their existing ticket
  back (`200`, `created: false`), backed by a partial unique index on active tickets. Every refusal is
  a code and a sentence a patient can understand: closed, not taking remote joins, full, the clinic's
  daily cap, or rate limited per phone and per address (`429` with `Retry-After`). The limits reuse
  the kernel's rate limiter. A walk-in and a phone join in the same second get adjacent numbers, 20
  times in a row on PostgreSQL. The ticket's `display_name` became `walk_in_name`, for walk-ins only,
  so a phone join is named only through its consent-gated patient record.
- **The lifecycle** (Issue 41, PR #163). There is one table of twelve legal moves over the eight
  statuses, and everything else is a `409` that changes nothing: all 64 pairs are tested in the service
  and over HTTP. Concurrent moves on one ticket are serialised by a row lock, and a move decided on a
  stale screen is refused. *Call next* takes its ticket with `FOR UPDATE SKIP LOCKED`, so two staff
  pressing at once call two different patients. Every move writes an audit row that says whether a
  person or the system made it. The state diagram in `docs/PRODUCT/03-booking-and-queue.md` is
  checked against the table.
- **Wait ranges** (Issue 42, PR #164; migration `0020`). `estimate.py` is a pure function over the
  last 60 finished visits: call intervals, outliers trimmed, weighted by time of day. It always
  returns a range and a confidence, such as "~15–25 min". With too few visits it falls back to the
  service's expected time and says *(approximate)*. Over a synthetic fixture of 47,927 predictions,
  87.2% of real waits fell inside the range, with a median error of 2.3 minutes against 30.5 for the
  naive estimate. The method is written up in `docs/PRODUCT/wait-estimate-methodology.md`. The queue
  snapshot and discovery now carry the range instead of an average.
- **Recall and no-show timers** (Issue 43, PR #165; migration `0021`). Open decision 1 is settled in
  `docs/GITHUB/ISSUES/README.md`: the kernel's APScheduler under a PostgreSQL advisory lock, and no
  `arq`. A called patient who does not arrive within five minutes (or the clinic's or queue's own 1–60)
  is recalled **exactly once**, then marked `no_show`, which frees the room. Both moves are made by the
  system actor, and each sends the patient a message. The sweep is safe to restart: every sweep in
  its test runs in a fresh process, and two processes racing the same instant recall once and mark
  one no-show.
- **Cancellation** (Issue 44, PR #166; migration `0022`). A patient can give their place back from the
  web, USSD or WhatsApp, and reception can cancel for them. The channel and an optional reason are
  recorded for the no-show analysis (Issue 93). **Positions are derived, never stored**, so everyone
  behind is one place further forward on the next read, and no other row is written. A patient who has
  already been called is told to speak to reception.
- **Transfers that keep the visit** (Issue 45, PR #167; migration `0023`). `transfer_ticket()` marks
  the ticket `transferred` and issues the next number in the new queue, in one savepoint, sharing a
  `visit`. The patient never rejoins. A closed, full or same queue is refused and changes nothing.
  Where the patient goes in the new line is a clinic setting: **in arrival order by default**, so a
  patient who has already waited at triage is not sent to the back of the doctor's queue. One
  integration test walks triage → doctor → pharmacy.
- **Priority overrides on the record** (Issue 46, PR #168; migration `0024`). Staff can move a waiting
  patient ahead of another waiting ticket. The server refuses an override **without a reason code**
  from a closed list, and one that would jump a patient already called or being seen. Each override
  writes a `queue_reorder` row (the place before and after, who and why) and an audit row, in one
  transaction. Nothing about priority reaches a public or patient-facing shape. The per-staff counts
  are listed by name, never ranked, and say so.
- **The contract, and the engine re-checked** (Issue 47, PR #169).
  - `contracts/queue.yaml` covers every queue route under the Issue 30 drift test. A new guard reads
    every error code the queue module can raise **from its source** and fails if the contract does
    not name one. It found five codes that were not documented.
  - A **Hypothesis state machine** runs long random sequences of every queue operation and checks,
    after each step, eight rules written out from the product doc rather than imported from the code.
    **It found two reachable bad states**, both fixed here: through the generic transitions route,
    staff could mark a ticket `transferred` with no ticket in the next queue, or `cancelled` with no
    channel recorded. Those two statuses are now made only by the transfer and cancel routes
    (`409 ticket.transition.dedicated_route`).
  - A PostgreSQL **concurrency suite** covers parallel joins from every channel into four queues,
    first joins on a new queue, five staff pressing *Call next* at once, a transfer racing a call, a
    cancel racing a call, and one patient joining on two channels at once. It **passed 20 consecutive
    runs**.
  - A **07:30 rush** replays 200 joins over four queues and all channels, sixty times faster than real
    time, with staff calling and a board reading throughout. It meets the provisional budget (see
    *Verification*).
  - **Its first 20-run loop found a join failing in the rush**, twice. The analytics recorder
    `join_queue()` calls (from v0.5.0) committed the session, which **committed the patient's ticket
    halfway through the join**. That released the queue's number lock before the snapshot was written,
    and a failed event would have rolled the ticket back while the join carried on. A join's events
    now commit with its ticket, and the snapshot write is a single upsert. Two regression tests fail on
    the old code.

## Migrations

Seven revisions, `0018` to `0024`, applied in order by `scripts/db/deploy-sequence.sh`. Each adds
tables, columns with defaults, constraints or indexes on tables that are new in this release. v0.5.0
has no tickets, so nothing it reads is dropped, except the snapshot's average (`0020`, below). Each
is proven reversible by the `postgres`-marked round trip, with the one refusal noted under `0018`.

- **`0018_tickets`**: new `ticket_sequence` and `ticket`, with the sequence, reference-code,
  walk-in, status and source constraints and three indexes (the board, a patient's tickets, a
  clinic's day). The foreign keys are `RESTRICT`. **The downgrade refuses while any ticket exists**,
  because dropping the table would delete the clinics' queue history.
- **`0019_ticket_active_patient`**: `uq_ticket_active_patient`, a partial unique index on a patient's
  active ticket per queue and day. It also renames `display_name` to `walk_in_name`, with a check that
  only a walk-in has one.
- **`0020_wait_time_samples`**: new `wait_time_sample` (one row per finished visit). On
  `site_queue_snapshot` it **replaces `average_wait_minutes`** with `wait_low_minutes`,
  `wait_high_minutes`, `wait_confidence` and `wait_approximate`. A downgrade restores an empty
  average.
- **`0021_recall_timers`**: `ticket.recalled_at`, and a nullable `recall_timeout_minutes` on `site` and
  `queue`.
- **`0022_ticket_cancellation`**: `ticket.cancelled_via` and `ticket.cancellation_reason`, with a check
  that only a cancelled ticket has either.
- **`0023_visits_and_call_order`**: new `visit`; `ticket.visit_id`, `ticket.transferred_from_id` and
  `ticket.order_key`; `site.transfer_placement` (default `arrival_order`). **It backfills** a visit and
  an order key for every existing ticket, proven on PostgreSQL with tickets present. A downgrade
  leaves a transfer's legs as unrelated tickets.
- **`0024_queue_reorder`**: new `queue_reorder`, with checks that a reason is from the list and that
  an override moves a patient forward. A downgrade drops the trail; the audit rows remain.

## Upgrade notes

- **No new runtime dependency.** `hypothesis` is a test dependency and was already pinned.
- **Six new settings**, all with defaults (`make env-example` regenerated `.env.example`):
  - `QUEUE_JOIN_RATE_LIMIT_PER_PHONE` (6), `QUEUE_JOIN_RATE_LIMIT_PER_IP` (30) and
    `QUEUE_JOIN_RATE_LIMIT_WINDOW_SECONDS` (3600).
  - `QUEUE_JOIN_SITE_DAILY_CAP` (1500): remote joins one clinic accepts in a day; walk-ins are not
    counted.
  - `QUEUE_RECALL_TIMEOUT_MINUTES` (5) and `QUEUE_RECALL_SWEEP_SECONDS` (30).

  Run `make check-config` after copying.
- **Set `RATE_LIMIT_BACKEND=redis` with more than one worker**, as for v0.5.0. The join limits and the
  site's daily cap are otherwise counted per process, so they multiply by the worker count.
- **A new scheduler job**, `queue_recall_timers`, runs every 30 seconds under advisory lock 443. Only
  one process runs a sweep at a time, whatever the number of workers.
- **New routes**, all in `contracts/queue.yaml`:
  - A patient joins at `POST /api/v1/clinics/{site_id}/queues/{queue_id}/tickets`, reads
    `GET /api/v1/patients/me/tickets` and cancels at `POST /api/v1/patients/me/tickets/{id}/cancel`.
  - Staff at a clinic have walk-ins, the day's tickets, transitions, *Call next*, cancel, transfer,
    priority, the reorder trail and counts, and visits under `/api/v1/sites/{site_id}/`.
  - The clinic settings are `GET`/`PUT /api/v1/sites/{site_id}/settings/recall` and
    `/settings/transfers`.

  They use grants v0.3.0 already seeded, so there is **no RBAC change**.
- **`PATIENT_JOIN_ENABLED` stays `false`.** The join API exists, but the page a patient lands on after
  joining is the ticket page (Issue 68), so the clinic page's button stays disabled with its reason
  until then.
- **API consumers of the transitions route:** `to: cancelled` and `to: transferred` are now refused
  with `409 ticket.transition.dedicated_route`. Use the cancel and transfer routes. No client used
  them; the dashboard's actions are M7's.

## Known issues

- **No screen shows a change as it happens.** Positions and waits are right on every read, but
  pushing them to an open board, dashboard or patient page waits for the board stream (Issue 57). The
  "within 2 seconds" criteria of Issues 44 and 46 are met on the next read only.
- **Nothing a person uses is built yet.** The call-next and walk-in UI (Issues 50, 51), the
  drag-to-reorder and its trail (Issue 52), the ticket page (Issue 68) and the reports on override
  counts and cancellation reasons (Issues 90, 93) all read this engine's routes, and none exists yet.
- **Queue messages go to the logging provider.** The recall, no-show and transfer texts are recorded
  in the notification ledger and pass the consent gate, but real SMS delivery is M9. They sit in the
  `ACCOUNT` category until the preferences of Issue 67 give queue messages their own.
- **The wait estimate is evaluated on synthetic data.** No clinic has produced a real visit yet. The
  87.2% coverage comes from a fixture built to look like one. F's review of the methodology note (a
  draft in `docs/PRODUCT/wait-estimate-methodology.md`) is still to come, and so is a re-run on real
  visits from the pilot. A new queue's first estimates fall back to expected service times, which err
  on the long side.
- **The rush budget is provisional, and judged on the best of three attempts.** No performance budget
  has been written down yet; Issue 105 owns it. The rush measures the engine at the database layer,
  not over HTTP, at one clinic. About one local rush in seven stalled for a second or so, with every
  thread (including read-only ones) stopped and PostgreSQL waiting on the client. That points at the
  machine, not the engine, so the budget is met by any of three attempts, while errors, gaps and
  double calls fail on any attempt. Issue 105 should find out whether a production host shows the
  same pauses.
- **The queue snapshot can briefly disagree under concurrent changes.** A join and a *Call next* on one
  queue each count the queue inside their own uncommitted transaction, so whichever commits last can
  write a figure that is off by one. The next change or the minute's reconciliation sweep (v0.5.0)
  repairs it, and a patient's own place is always counted fresh. This follows from the snapshot's
  design and was not observed in a test.
- **Nothing records a join started yet.** `record_join_started` now joins its caller's
  transaction like `record_join_completed`, but nothing calls it yet; the ticket page (Issue 68)
  should.
- **The per-address join limit is 30 an hour.** A mobile carrier's NAT can put many phones behind one
  address. That is why the limit is loose, but a busy township on one carrier could still reach it.
  Issue 105's load profile should include real carrier ranges before the pilot.
- **Two race tests failed once, locally, and never again.**
  - #166's `test_a_phone_and_the_desk_racing_twenty_times_always_get_adjacent_numbers` lost its thread
    exception before the helper reported it. It passed 24 stressed reruns, and the helper now keeps the
    exception, so another failure would say what happened.
  - #165's queue settings test failed on CI only: a factory's display order was above 999 on that
    worker. It was fixed in that PR.
- **`hypothesis` was missing from one developer's virtual environment** although `requirements.txt`
  pins it. A `pip install -r requirements.txt` fixes it; CI installs from the file.
- **Everything from v0.5.0's list still stands.** The clinic registration form and the geocoding proxy
  are still not rate limited, and a new clinic's first manager still cannot be appointed through a
  route. The one-time-code store is still process-local. The credential in the repository's history
  has still not been rotated, and nothing is provisioned. **Only `v0.2.0` has ever been tagged**:
  `v0.1.0` and `v0.3.0`–`v0.5.0` have release notes but no tags on `origin`, and they should be cut in
  order before this one.

## Verification

Run on the Issue 47 branch with `main` merged in, which is `main` as #169 will leave it. It used
PostgreSQL 18 + PostGIS and Redis in Docker, both required rather than skippable:

```text
TZ=UTC pytest -q -n auto --dist loadscope tests
                                      1875 passed, 1 skipped (the rush), 9 xfailed in 190 s
the rush alone                        passed on attempt 1: join p95 53.5 ms
concurrency suite, 20 runs in a row   20 of 20 passed (7 tests each)
  rush, first attempts                join p95 median 50.3 ms (worst 194.3); board read p95
                                      median 24.7 ms; Call next p95 median 23.1 ms; one run
                                      needed a second attempt (Call next 214.9 → 22.9 ms)
property tests                        100 examples, up to 50 steps, all 8 statuses reached
ruff check . / ruff format --check    clean (481 files)
mypy src/                             clean (243 files)
```

**The provisional rush budget** (`RUSH_BUDGET` in `tests/integration/queue/test_queue_concurrency.py`)
is a join p95 of at most 300 ms and none over 1.5 s, a board read p95 of at most 150 ms, and a *Call
next* p95 of at most 200 ms. It is met by any of three attempts. Every attempt must have no errors,
gapless numbers and no ticket called twice. The rush replays ten minutes in ten seconds on 16 workers.
It skips under `pytest -n` and runs **alone**, locally and in its own CI step, because it measures
the machine when every CPU is busy with other tests.

CI ran the unit, integration and flow suites on every pull request, and each merged with every check
green. Each pull request carries its own evidence, and it is worth reading beside the suite:

- **#161:** 100 joins with PostgreSQL's own count of the transactions open at once, the naive allocator
  failing, and the board index in `EXPLAIN` over 20,000 tickets.
- **#162:** the four channels over HTTP, the duplicate join, and every refusal's sentence.
- **#163:** all 64 pairs and the stale-screen race.
- **#164:** the estimator's error against the fixture, and the fallback labelled.
- **#165:** the restart test, and the recall and no-show messages in the ledger.
- **#166:** everyone behind moving up on the next read, from each channel.
- **#167:** the triage → doctor → pharmacy walk, and the backfill on a database with tickets.
- **#168:** the refused override, and the trail and counts as the manager reads them.
- **#169:** the 20-run loop, the rush reports, Hypothesis's shrunk counter-examples before the fix, and
  how the mid-join commit was traced.
