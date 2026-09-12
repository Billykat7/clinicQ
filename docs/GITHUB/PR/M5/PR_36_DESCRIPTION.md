# PR: Twenty clinics' queue lengths in one read, and a cold cache that is slow, never wrong (Issue 36 / M5-36)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#36](https://github.com/Billykat7/clinicQ/issues/36) · **Builds on:** #25 (queues, merged),
#31 (PR #147), and #34, #32, #35 beneath it (PRs #148–#150)

> **Merge order:** after #31, #34, #32 and #35 (PRs #147–#150). This branch is stacked on them, so the
> Conventions check fails on their commits until they merge. Every test job passes.
>
> **Who takes it:** the spec places this issue in no sprint lane and asks the team to agree an owner
> for sprint 5 or 6. It is written to A's module boundaries (the queue engine's read model), and the
> owner should still be agreed at planning before merge.

Rendering a list of twenty clinics must not run twenty ticket counts. This PR adds the queue
snapshot: each queue's last known length, kept in Redis for fast reads with the
`site_queue_snapshot` table behind it. It is written through when a queue changes and repaired by a
scheduled sweep. Discovery now reads queue lengths through it (until now it read them directly, as #31
did). The rule that shaped every line is the one in the issue: **a cold or flushed cache is slow,
never wrong.**

## Summary

- **One read per page.** `cached_waiting_counts()` fetches every queue on a page with a single
  `MGET`. Only what Redis lacks, or holds too old, falls through to one `site_queue_snapshot` query
  and then to one recount from the source of truth. Whatever is found is written back to Redis.
- **Never stale.** Nothing older than `QUEUE_SNAPSHOT_MAX_AGE_SECONDS` (30) is served from either
  store. A cold, flushed or unreachable Redis costs a recount, not a wrong number. One failed Redis
  call opens a 30-second circuit, so an outage does not add a connection timeout to every page.
- **Write-through.** `on_queue_changed(db, queue)` is the hook joins, call-next, cancels and no-shows
  call (Issues 40–44). It recounts and writes both stores in the same request, which takes
  milliseconds.
- **Repair sweep.** `reconcile_snapshots()` recounts every active queue and repairs drift in the table
  and in Redis. It is registered in `src/core/scheduler.py` as `run_queue_snapshot_reconciliation`
  under advisory lock 336, every `QUEUE_SNAPSHOT_RECONCILE_SECONDS` (60): a `run_*` sweep, not an `arq`
  worker (open decision 1).
- **Observable.** `clinicq_queue_snapshot_reads_total{outcome="cache_hit"|"table"|"recounted"}` and
  `clinicq_queue_snapshot_repairs_total` are on `/metrics`.
- **Age in seconds.** A reader now answers the count *and when it was taken*. The list and the
  detail page show a measured length as *4 people waiting, counted 12 s ago*, and the API's queue
  shape carries `as_of`.

## Design notes

**What is counted before tickets exist.** Issue 39 brings tickets. Until then the source of truth is
`read_waiting_counts()`, which honestly says "not measured" (#31), so the snapshot stores and serves
`None` faithfully: patients see *Queue length not reported yet*, exactly as before. The whole path
still runs end to end, and the tests prove it with `TrueCounts`, a reader with real numbers that a
test changes the way a join would. When Issue 39 fills in the direct read, nothing in this module
changes.

**Why write-through rather than deleting the key.** Deleting a cached key on change leaves a window
in which a concurrent reader recounts from a transaction that has not seen the change and puts the
old figure back. Recounting and writing inside the changing request closes that window. The table
write joins the caller's transaction, so it commits with the ticket change that caused it.

**The table is the durable store, not a second cache.** Reads write only to Redis. The table is
written by the write-through hook and the sweep, and read when Redis misses. A table row older than
the bound is recounted too, so neither store can serve stale figures.

**The circuit breaker was found by a test.** The 200 ms budget test from #31 read the developer's
`.env`, whose `REDIS_URL` names the compose hostname `redis`, which does not resolve on the host.
Every search waited out a DNS failure and took **4 seconds** while still returning correct figures.
"Slower" had become unusable, so `RedisSnapshotCache` now skips Redis for 30 seconds after a failure,
logging once on entry and once on recovery like the rate-limit backend. The performance test now
also runs with no cache explicitly, so it measures the slow path on purpose: a median of 14.5 ms.

**The reader contract changed while nothing outside M5 uses it.** `WaitingCountReader` answers
`QueueReading(waiting, as_of)` instead of a bare count. That is the only way a cached figure can carry
its age to the patient, and changing it now touches only this stack's own tests.

**Guards.** The sweep's platform-wide `select(Queue)` and `select(SiteQueueSnapshot)` are listed in
`tests/unit/security/test_site_scoped_queries.py` with the reason: a scheduled system job has no clinic
to be scoped by. The discovery read uses `published_select`. `tests/integration/security/test_cross_tenant.py`
lists the new site-scoped model as pending, because no clinic-facing route reads snapshots.

**Out of scope:** performance tuning and dashboards (Issue 105), and the board's live state, which is
pushed by SSE (Issue 57).

## Changes

- **`alembic/versions/0015_site_queue_snapshot.py`** (new) and
  **`src/database/models/site_queue_snapshot.py`** (new): `queue_id` primary key, `site_id` indexed,
  nullable `waiting` and `average_wait_minutes`, `updated_at`.
- **`src/modules/queue/`** (new): `snapshot.py` (`Snapshot`, `RedisSnapshotCache`, `NoSnapshotCache`,
  `snapshot_cache`, `cached_waiting_counts`, `refresh_snapshots`, `on_queue_changed`,
  `reconcile_snapshots`, `cache_stats`, the two counters) and `__init__.py`.
- **`src/core/scheduler.py`:** `run_queue_snapshot_reconciliation`, lock key 336, the job.
- **`src/core/config.py`:** `QUEUE_SNAPSHOT_TTL_SECONDS`, `QUEUE_SNAPSHOT_MAX_AGE_SECONDS`,
  `QUEUE_SNAPSHOT_RECONCILE_SECONDS`. **`.env.example`:** regenerated. **`src/commons/enums.py`:**
  `SnapshotReadOutcome`.
- **`src/modules/queues/live.py`:** `QueueReading`, `NOT_MEASURED`, `LiveQueue.as_of`.
  **`src/modules/discovery/service.py`**, **`profile.py`:** read through the snapshot by default.
  **`schemas.py`:** `as_of`. **`src/web/discover.py`:** `counted_ago`, `measured_queue_label`.
- **`tests/integration/queue/test_queue_snapshot.py`** (new, 10 cases);
  **`tests/unit/queue/test_snapshot_cache.py`** (new, 2 cases). The discovery tests run without a
  cache, and their readers answer the new shape.
- **`docs/GITHUB/ISSUES/README.md`:** open decision 1's row names this sweep.

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (223 files).
- [x] Full suite with PostgreSQL and Redis required, in UTC: **1592 passed, 9 xfailed** (after adding
      the cross-tenant pending entry, whose absence the first run caught).
- [x] `tests/integration/queue` and `tests/unit/queue`: **12 passed**, printing:

```text
write-through: 6.2 ms
flushed 40 keys; cold search 15.6 ms, then warm 16.9 ms
```

- [x] **On the running application.** The dev server on the migrated, seeded `clinicq_m5_verify`
      database with the compose Redis, while `redis-cli MONITOR` recorded every command. Three loads
      of the list (six clinics within 50 km, 18 queues), then the cache's keys deleted (`SCAN` and
      `DEL` of `clinicq:qsnap:*`, the same as `flush()`) and one more load. Commands per load, keys
      counted:

```text
== cold (no snapshot keys)        200 in 0.075 s
"MGET" keys=18
"SET"  keys=1   × 18              ← one pipeline writing the recounted snapshots back
== warm                           200 in 0.016 s
"MGET" keys=18
== warm                           200 in 0.018 s
"MGET" keys=18
== snapshot keys deleted (18)     200 in 0.018 s
"MGET" keys=18
"SET"  keys=1   × 18
```

- [ ] Screenshot: nothing on screen changes until tickets exist (every length is still *not reported
      yet*); no template or stylesheet is touched.

## Acceptance criteria

- [x] **A discovery list of 20 clinics issues one snapshot read, not 20 counts.**
      `test_a_list_of_twenty_clinics_issues_one_snapshot_read_not_twenty_counts`: warm, exactly one
      `MGET`, zero calls to the source and zero SQL statements touching `site_queue_snapshot`. Cold,
      one source call for all 40 queues together. The `MONITOR` transcript shows the same on the
      running app.
- [x] **Joining a queue updates the snapshot within 1 second.** *Proven through the hook the join
      will call; the join itself is Issue 40.* `test_joining_a_queue_updates_the_snapshot_within_one_second`
      changes the true count, calls `on_queue_changed`, and asserts under a second (6.2 ms). The very
      next read gets the new figure from the cache without recounting, and the table row matches.
- [x] **A cold or flushed cache produces correct (slower) results, never stale ones.**
      `test_a_flushed_cache_gives_correct_results_never_stale_ones` warms the cache, changes every
      count, deletes the cache's keys and searches: the result equals a no-cache count and differs
      from the warm one. `test_an_unreachable_redis_gives_correct_results` and
      `test_a_snapshot_older_than_the_bound_is_never_served` cover a dead server and a 31-second-old
      entry.
- [x] **The reconciliation job corrects deliberately corrupted snapshots in a test.**
      `test_the_reconciliation_job_repairs_deliberately_corrupted_snapshots` sets a table row to 999
      and a Redis key to 42 by hand; one sweep reports 2 repairs and restores both, and a second finds
      0. `test_the_scheduled_sweep_runs_under_the_advisory_lock_and_repairs` runs the scheduler's
      entry point: it does nothing while another connection holds lock 336, then repairs.
- [x] **Cache hit rate is observable.** `test_the_cache_hit_rate_is_observable`: the recount and
      hit counters rise by exactly 40 each over a cold and a warm search, and `/metrics` exposes both
      series.
- [x] **Snapshot staleness is bounded and displayed in seconds where the patient can see it.**
      *The bound is enforced now; the display appears once lengths are measured.* Nothing older than
      30 s is served (tested). A measured length is shown with its age,
      `test_a_measured_length_is_shown_with_its_age_in_seconds`; until Issue 39 there is no measured
      length to show, so no age is displayed.

## Risk and rollback

**Migration `0015`** adds one table and changes nothing existing, and it is reversible. Discovery now
reads through the snapshot, but the fallback chain ends at the same direct read as before, and a
deployment without `REDIS_URL` uses no cache at all. The new scheduler job recounts queues every
60 seconds, which today is a read of queue ids. Rollback is a revert; #37, #33 and #38 are stacked on
this.

**Follow-ups:** Issues 40–44 must call `on_queue_changed` wherever a ticket's status changes (worth a
guard test once tickets exist); `average_wait_minutes` waits for Issue 42; `.env`'s `REDIS_URL`
names the compose hostname, which does not resolve from the host (the same stale-`.env` family as the
database password).

Closes #36
