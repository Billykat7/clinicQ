# PR: Freeze the queue contract and prove the engine under concurrency, chance and a rush (Issue 47 / M6-47)

**Milestone:** [Milestone 6: Queue Engine Core](https://github.com/Billykat7/clinicQ/milestone/6) ·
**Issue:** [#47](https://github.com/Billykat7/clinicQ/issues/47) · **Builds on:** #39–#46, all merged
(#161–#168) · **Closes M6** and adds the [`v0.6.0` release note](../../RELEASES/RELEASE_v0_6_0.md)

The dashboard, the board, the patient's page and both channel adapters are about to be built on the
queue engine. This PR checks the engine again **independently** before they are, rather than trusting
each issue's own tests. It adds four things:

- **the contract, checked against the code's own error codes;**
- **a Hypothesis state machine** that runs long random sequences of every queue operation;
- **a PostgreSQL concurrency suite**, run 20 times in a row;
- **the 07:30 rush**, measured against a stated budget.

**The new tests found three real bugs**, and this PR fixes them:

1. **The property tests found two reachable bad states** through the generic transitions route. A
   staff member could mark a ticket `transferred` with **no ticket in the next queue**: a patient sent
   nowhere, with a visit that silently ends. They could also mark it `cancelled` with **no channel
   recorded**, which bypasses Issue 44's rules and its reports. Both statuses are now made only by
   their own routes.
2. **The 20-run loop found a web or USSD join failing in the rush** on the queue snapshot's primary
   key, twice in 20 runs. The cause was not the snapshot. `join_queue()` records a *join completed*
   analytics event, and that recorder (Issue 38) **committed the session**. That committed the
   patient's ticket halfway through the join, and released the number lock that serialises a queue's
   joins before the snapshot was written. A failed event would also have rolled the ticket back while
   the join carried on. A join's events now stay in the join's transaction, and the snapshot write is
   one `INSERT … ON CONFLICT` either way, because the reconciliation sweep can insert a row concurrently
   too.

## Summary

- **`tests/integration/contracts/test_queue_contract.py`** (new). Issue 30's drift test already
  matches `contracts/queue.yaml` to the router in both directions. This file adds the half FastAPI
  cannot see: the refusals a service raises. It reads **every error code the queue module can put on
  the wire from its source**: the `*_CODE` constants, the `code="…"` literals, and every reason behind
  an f-string code (`queue.join.<JoinRefusal>`, `ticket.transfer.<reason>`,
  `ticket.priority.<reason>`). It fails if the contract does not name one. It also requires `401`,
  `403` and `404` on every operation and `409` on every write, and that every `409` names its codes. A
  fourth test proves the reader can fail. **On its first run it found five codes the contract did not
  name**; they are now documented.
- **`tests/unit/queue/test_ticket_states_property.py`** (new). A `RuleBasedStateMachine` over one
  clinic, three queues, three patients and walk-ins. Its rules call every operation the way its route
  does:
  - joins from any channel, and *Call next*;
  - a staff move to **any** status, and a clinician's start and finish;
  - a patient's cancel and reception's cancel;
  - a transfer, including to the same queue;
  - an override, including with no reason;
  - the recall sweep.

  After **every** step it checks eight rules written out from the product doc (`SPEC`), **not
  imported** from `lifecycle.py`, so the table is not checking itself:
  1. every status change is legal, and a terminal ticket never changes;
  2. a refused operation changes nothing, anywhere;
  3. each ticket's audited moves form one legal path to its status;
  4. numbers are gapless per queue and day;
  5. one active ticket per patient per queue;
  6. waiting places are exactly `0..k-1`;
  7. timestamps agree with status;
  8. a `transferred` ticket has one successor in its visit, and a `cancelled` one has its channel.
- **`src/modules/queue/lifecycle.py`:** `staff_move()` is what the transitions route now calls. It
  refuses the moves in `DEDICATED_MOVES` (`cancelled`, `transferred`) with `DedicatedMoveError` (`409
  ticket.transition.dedicated_route`) before reading the ticket, then calls `transition_ticket()`.
  `transition_ticket()` itself is unchanged: the table stays the one definition of legal, and
  `cancel_ticket()` and `transfer_ticket()` still move the status through it.
- **`src/modules/discovery/analytics.py`:** `_record(…, commit=)`. A search or a page view still
  records and commits on its own. `record_join_started` and `record_join_completed` only add their
  event, in a savepoint, to the caller's transaction, and a failure rolls back that savepoint and
  nothing else.
- **`src/modules/queue/snapshot.py`:** `_write` is one upsert per call instead of `Session.merge`
  (a read, then an insert), and it expires any snapshot rows already loaded in the session.
- **`tests/integration/queue/test_queue_concurrency.py`** (new, PostgreSQL). Real races, released
  together by a barrier, each on its own connection:
  - 80 joins from all four channels into four queues;
  - ten new queues, each with eight first web joins at once (the rush bug, made deterministic);
  - five staff pressing *Call next* at once for six rounds;
  - ten rounds each of a transfer racing a call and a patient's cancel racing a call;
  - ten rounds of one patient joining on the web and USSD in the same instant;
  - **the 07:30 rush**.

## Design notes

**Why the fix is at the route, not in the table.** `waiting → transferred` and `waiting → cancelled`
*are* legal lifecycle moves; the product doc's diagram has them. What is wrong is making them as bare
status changes, because each needs more than a status: the next queue's ticket, or the channel and the
called-patient rule. So the table and `transition_ticket()` keep their meaning, and all 64 pairs are
still tested against them. The one route that lets a caller choose any status refuses the two that
have their own routes. Hypothesis shrank both bugs to three steps:

```text
state.join(channel=<TicketSource.WEB: 'web'>, queue=0, who=None)
state.staff_moves_a_ticket(data=data(...), to=<TicketStatus.TRANSFERRED: 'transferred'>)
AssertionError: T001 is transferred with nowhere to go

state.join(channel=<TicketSource.WEB: 'web'>, queue=0, who=None)
state.staff_moves_a_ticket(data=data(...), to=<TicketStatus.CANCELLED: 'cancelled'>)
AssertionError: ('T001', None)        # cancelled, cancelled_via is None
```

After the fix, the same run of 100 examples (up to 50 steps each) passes. To show it explored more
than `waiting`, it reports what it reached: **every one of the eight statuses**, and the refusals of
every operation it drives. Those include `ticket.transfer.already_there`,
`ticket.priority.ahead_of_called`, `ticket.cancel.after_call` and `ticket.transition.dedicated_route`
(the statistics are below).
It does not reach the refusals its world has no setup for (a closed or full queue, a stale screen, a
rate limit), and a given run may miss a rare one such as `ticket.transfer.already_there`. Those have
their own tests in #162, #163 and #167. Two generation biases were needed to get there, and neither touches
the judging. Tickets are drawn mostly from those still in the day, and a staff move is drawn half the
time from the moves the spec offers.

**How the rush bug was found, not guessed.** The loop's traceback showed the failing join, not the
racing writer. So the old `_write` was instrumented to print, per call, its transaction id, whether it
found a snapshot row, and which ticket sequences it could see. The failing pair was clear. A second
join could already see the first join's ticket #1 **before the first join had written its snapshot**,
so the first transaction had committed partway through. `_record()`'s `db.commit()` was the only
commit on that path. Two regression tests now pin it, and both fail on the old code:
- `test_a_join_leaves_committing_to_its_caller` (web and USSD fail; walk-ins record no event);
- `test_the_first_joins_on_a_new_queue_at_once_all_succeed`.

The first runs on PostgreSQL, because SQLite's driver does not honour the savepoints it depends on.

**The rush budget is provisional, and says so.** No performance budget exists in the docs: Issue 105
(M14) owns it, over HTTP, on production-sized servers. `RUSH_BUDGET` states the engine's share at the
database layer:
- a join p95 of 300 ms or less, and none over 1.5 s;
- a board read p95 of 150 ms or less;
- a *Call next* p95 of 200 ms or less;
- no errors, gapless numbers, and no ticket called twice.

A clean local rush measures about 50, 25 and 25 ms, so the budget allows about six times that for a
slower CI runner.

**The budget is met by any of three attempts; correctness gets none.** In 40 local rushes, about one
in seven stalled for a second or so. Every thread stopped at once, including the read-only board, and
a `pg_stat_activity` sampler added to the report showed PostgreSQL's sessions in `Client:ClientRead`,
waiting on the test process. The rest were in `Lock:transactionid`, queued behind a lock whose holder
had stalled. That is the machine pausing (a Docker VM on a laptop), not the engine. Freezing Python's
garbage collector for the rush was tried and made no difference, so it was not kept. A latency budget
judged on one ten-second window would be flaky, so the test runs up to three rushes, each at a new
clinic. It passes on the first that meets the budget and logs every one. A real regression slows
every attempt and still fails. Errors, gaps and double calls are asserted on **every** attempt.

The rush is **harsher than a real morning**:
- ten minutes are replayed in ten seconds, so joins arrive 60 times more densely;
- arrivals are front-loaded (a beta distribution);
- the real abuse limits apply;
- 16 workers do the joins, while one staff member per queue calls every three (compressed) minutes
  and a board reads every queue twice a second.

**What "no flakes" rests on.** Nothing in the concurrency suite waits for timing luck. Every outcome it
asserts is guaranteed by a database lock (the counter row, `FOR UPDATE`, `SKIP LOCKED`, the partial
unique index), and each race asserts **exactly one** of the allowed outcomes, never "at least one". A
race with two winners or none fails. Thread exceptions are captured and asserted, not lost.

**Why the rush runs alone.** The first full parallel run (`pytest -n auto`) failed the rush on all
three attempts, with a join p50 of 190–270 ms against about 30 ms alone. Every CPU was busy with other
tests, so the measurement was of the machine. The rush now skips under `pytest -n`, saying how to run
it. The integration shard runs it in its own step after the parallel run (`07:30 rush, alone` in
`ci.yml`, described in `docs/CICD/PIPELINES.md`). The 20-run loop above runs the file without `-n`,
so the rush ran in every loop. The skip was added after that loop and changes nothing outside `-n`.

**Closing M6, re-derived.** The six exit criteria were checked against the shipped code and this PR's
own runs, not copied from #161–#168:

| Exit criterion | Evidence on `main` + this branch |
|---|---|
| 100 concurrent joins → 100 consecutive numbers | `test_sequence_concurrency.py` (PostgreSQL); here, 80 joins over four queues and 200 in the rush, gapless per queue, 20 runs |
| Illegal transition → 409, never mutates | 64 pairs in the service and over HTTP; the property test's rule 2, after every refused step |
| Walk-in and remote join in one second → adjacent | #162's 20-round race; here, all four channels drawing one sequence per queue |
| Estimate is a range from the last N visits | `estimate.py` window of 60, with a labelled fallback; property and rush runs read it on every join |
| Unattended call → recall once → `no_show`, patient told | #165's fresh-process sweep test; the property test's sweeps never break rules 1, 3 or 7 |
| Override needs a reason, visible in audit | the property test's override with `reason=None` is always refused and changes nothing |

## Changes

- **Tests (new):**
  - `tests/integration/contracts/test_queue_contract.py` (4);
  - `tests/unit/queue/test_ticket_states_property.py` (1 state machine: 100 examples, up to 50
    steps);
  - `tests/integration/queue/test_queue_concurrency.py` (7, PostgreSQL);
  - `test_a_join_leaves_committing_to_its_caller` in `tests/integration/queue/test_join_queue.py` (3,
    PostgreSQL).
- **`src/modules/discovery/analytics.py`**, **`src/modules/queue/snapshot.py`:** the rush bug's fix.
- **`tests/integration/discovery/test_discovery_analytics.py`:** the test that calls
  `record_join_completed` directly now commits, as the join flow does.
- **`.github/workflows/ci.yml`**, **`docs/CICD/PIPELINES.md`:** the rush's own step in the
  integration shard.
- **`src/modules/queue/lifecycle.py`:** `staff_move`, `DEDICATED_MOVES`, `DedicatedMoveError`,
  `DEDICATED_MOVE_CODE`; the module docstring says why.
- **`src/modules/queue/router.py`:** the transitions route calls `staff_move`.
- **`contracts/queue.yaml`:** the join refusal's full `queue.join.*` list, the priority `422` code, and
  `ticket.transition.dedicated_route` on the transitions `409`.
- **`tests/integration/queue/test_ticket_lifecycle.py`:** the 64-pair HTTP test now expects the 16
  pairs to `cancelled` or `transferred` to be refused with the dedicated-route code (41 illegal, 16
  dedicated, 7 moved).
- **`docs/PRODUCT/03-booking-and-queue.md`:** the two dedicated moves and the property tests.
- **Closing M6:**
  - **`docs/GITHUB/RELEASES/RELEASE_v0_6_0.md`** (new);
  - the M6 Status row and exit criteria (all six ticked, each with its evidence);
  - the gantt (`M6 … done`);
  - the sprint 7 row and the sprint summary: M6 closed, and the sprints still open because their
    other lanes have not started;
  - the README Status block;
  - the progress bars (`make milestone-progress ARGS='--assume-closed 47'`).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (243 files).
- [x] Full suite in UTC with PostgreSQL and Redis required: **1875 passed, 1 skipped, 9 xfailed**.
      The one skip is the rush, which skips under `-n` and passed alone straight after, on its first
      attempt (join p95 53.5 ms).
- [x] `make milestone-progress-check ARGS='--assume-closed 47'`: up to date.
- [x] **The concurrency suite, 20 consecutive runs**, one `pytest` process per run against the
      dockerised PostgreSQL 18. The rush report is printed by each run:

```bash
for i in $(seq 1 20); do
  log=$(pytest -q --no-cov -p no:randomly tests/integration/queue/test_queue_concurrency.py --log-cli-level=INFO 2>&1)
  echo "run $i: $(echo "$log" | tail -1 | sed -E 's/=+ ?//g')"
  echo "$log" | grep -o "07:30 rush, attempt [0-9]: {'joins'.*'call_next_p95_ms': [0-9.]*"   # the rush reports
done
```

This is the second 20-run loop. The first, on the code before this PR's analytics fix, failed 4 of 20:
twice on the join bug above, and twice on stalls before the budget allowed a retry.

```text
run 1: 7 passed, 1 warning in 22.54s
run 2: 7 passed, 1 warning in 22.52s
run 3: 7 passed, 1 warning in 22.95s
run 4: 7 passed, 1 warning in 23.73s
run 5: 7 passed, 1 warning in 22.80s
run 6: 7 passed, 1 warning in 23.04s
run 7: 7 passed, 1 warning in 22.62s
run 8: 7 passed, 1 warning in 23.15s
run 9: 7 passed, 1 warning in 23.04s
run 10: 7 passed, 1 warning in 23.01s
run 11: 7 passed, 1 warning in 22.76s
run 12: 7 passed, 1 warning in 22.65s
run 13: 7 passed, 1 warning in 22.63s
run 14: 7 passed, 1 warning in 23.77s
run 15: 7 passed, 1 warning in 23.46s
run 16: 7 passed, 1 warning in 22.75s
run 17: 7 passed, 1 warning in 22.96s
run 18: 7 passed, 1 warning in 32.35s
run 19: 7 passed, 1 warning in 22.93s
run 20: 7 passed, 1 warning in 22.14s

20 of 20. Run 18 took 32 s because its first rush missed the budget (Call next p95 214.9 ms > 200)
and its second met it (22.9 ms); every other run met it on the first attempt.
```

- [x] **Two staff pressing Call next at once** get two different tickets. In
      `test_staff_pressing_call_next_at_once_never_call_the_same_ticket`, five staff press at once for
      six rounds: 30 calls, 30 different tickets, every waiting ticket called once. In the rush, 20
      runs of four staff calling during 200 joins never called one ticket twice.
- [x] **The 07:30 rush against the budget**, over the 20 runs:

```text
first attempt of each of the 20 runs      median     best    worst     budget
joins completed / errors                  200 / 0 in every attempt (21), numbers gapless per queue
join p50                                  29.0 ms    27.2     32.4
join p95                                  50.3 ms    41.8    194.3      300
join max                                 183.0 ms   173.9    950.0     1500
board read p95                            24.7 ms    14.7    101.4      150
Call next p95                             23.1 ms    19.8    214.9      200   (run 18; attempt 2: 22.9)
tickets per queue (T/A/C/I)               87 / 53 / 38 / 22, from 16 workers over 9.1 s
```

- [x] **Property tests**, with `--hypothesis-show-statistics`:

```text
    - Typical runtimes: ~ 56-461 ms, of which ~ 1-11 ms in data generation
    - 100 passing, 0 failing, and 5 invalid test cases
      * 54.29%, refused: ticket.call_next.empty
      * 46.67%, reached: waiting
      * 45.71%, refused: ticket.transition.illegal
      * 40.95%, reached: cancelled
      * 35.24%, refused: http.not_found
      * 31.43%, refused: ticket.priority.reason_required
      * 24.76%, refused: ticket.transition.dedicated_route
      * 21.90%, reached: done
      * 21.90%, reached: transferred
      * 21.90%, refused: ticket.priority.not_waiting
      * 21.90%, refused: ticket.transfer.same_queue
      * 18.10%, reached: no_show
      * 18.10%, refused: ticket.priority.other_queue
      * 16.19%, refused: ticket.priority.not_forward
      * 13.33%, reached: in_progress
      * 11.43%, reached: called
      * 10.48%, refused: ticket.cancel.after_call
      * 6.67%, refused: ticket.priority.ahead_of_called
      * 3.81%, reached: recalled
1 passed, 1 warning in 18.43s
```

- [ ] Screenshot: no template, stylesheet or script changed.

## Acceptance criteria

- [x] **`queue.yaml` documents every route and every documented error status.** The drift test covers
      the routes in both directions. `test_queue_contract.py` covers every error code the module can
      raise, read from its source: `401`/`403`/`404` on every operation, `409` on every write, and
      every `409` naming its codes.
- [x] **The concurrency suite passes repeatedly with no flakes across 20 consecutive runs.** See the
      loop above: 20 of 20, 6 passed each time.
- [x] **Property tests find no reachable illegal state.** They find none now. Before this PR they
      found two, fixed here and described above.
- [x] **Two staff calling next simultaneously produce two different tickets, never the same one.**
- [x] **The rush scenario completes within the performance budget.** It completes within the
      **provisional** budget stated in `RUSH_BUDGET`, met by any of three attempts for the reason
      above. It found a real bug on the way. The real budget is Issue 105's.
- [x] **The whole suite runs inside the CI time budget.** The concurrency suite adds about 13 s to
      the integration job's parallel run, and the rush's own step 10–30 s. The property test adds
      about 18 s to the unit job. Every job's timeout is 15 minutes, and this PR's CI run is the
      proof.

## Risk and rollback

**No migration.** There are three behaviour changes:

1. On `POST /api/v1/sites/{site_id}/tickets/{ticket_id}/transitions`, `to: cancelled` and
   `to: transferred` are now `409 ticket.transition.dedicated_route`. No client calls that route yet
   (the dashboard is M7), and the cancel and transfer routes do the same moves properly.
2. A join's analytics event commits with the ticket instead of before it. A join that fails after
   recording now records no event, which is what the report should count.
3. The snapshot write is an upsert, with the same row as the result.

Rollback is a revert. It would reopen the two states the property test found and the mid-join commit,
and the tests would fail again.

**The rush step is the one to watch on CI.** It asserts latencies on a shared runner, alone but still
on shared hardware. If it goes red without a code change, read its three logged reports before
touching the budget. The budget is there to catch a regression, not to pass.

**Follow-ups:**
- Issue 105 writes the real performance budget and runs the rush over HTTP with board and dashboard
  clients.
- The `v0.6.0` tag is cut from `main` after this merges, after the earlier untagged notes.
- M7 builds the dashboard's call, cancel and transfer actions on the routes this PR froze.

Closes #47
