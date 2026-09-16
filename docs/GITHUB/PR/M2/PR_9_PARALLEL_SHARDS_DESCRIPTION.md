# PR: Run the tests as parallel shards, and stop migrating a database for every test (Issue 9 follow-up / M2-9)

**Milestone:** [Milestone 2: CI/CD, Environments & Team Workflow](https://github.com/Billykat7/clinicQ/milestone/2) ·
**Issue:** [#9](https://github.com/Billykat7/clinicQ/issues/9) (Actions CI: lint, type-check, tests), closed by PR #122

A pull request waited **8m42s** for CI, and **7m45s** of that was one job: the `integration` shard,
1,444 seconds of test time on four cores. This PR does what BK Properties does — group the tests
into shards that run as parallel jobs — and removes the reason the integration tests were slow
before sharding them: **every test that wanted a schema ran the whole Alembic migration history
first**, about two seconds each.

A run is now **4m07s**, measured end to end on this branch, green.

## Scope

- **In:** the `test` job's matrix in `ci.yml`, two new guard tests, the database fixtures in
  `tests/conftest.py` and `tests/e2e/dashboard/conftest.py`, and `docs/CICD/PIPELINES.md`.
- **Out:** no test was changed, skipped, reordered or weakened. The same 2,562 tests run, with the
  same coverage floor, against the same PostGIS and Redis containers.

## Summary

- **Sixteen shards instead of four,** balanced by each directory's *measured* cost — taken from the
  JUnit files CI uploads itself, not guessed. Eight integration shards, three unit, one flows, four
  browser.
- **A database copy instead of a migration run.** The migrations run once per xdist worker into a
  template database; each test's database is a `CREATE DATABASE ... TEMPLATE` copy. The contract is
  unchanged — own database, at `head`, dropped afterwards, shared with nothing — and
  `tests/integration/discovery` went from 318 s of test time to 166 s.
- **Two guard tests** hold the matrix together (below).
- **The browser shards hand out tests one by one** (`--dist load`) where their time is spent
  waiting on a page rather than on a CPU — except `board-measured`, which runs one test at a time
  because its tests measure the board rather than exercise it. **`--with-deps` is gone** from the
  Chromium install: it spent 22 seconds a shard on apt, on a runner image that already ships Chrome.
- **`timeout-minutes` on a test shard is 10, not 15**, now that the slowest measures 2m46s.

## Design notes

**Balanced by measurement, grouped by domain.** A bin-packer produces perfectly even shards, and
they are unreadable: seven unrelated directories in each. These shards are whole domain directories
grouped until they are worth a job, which is what makes them reviewable — and the balance is good
enough, since the fixed cost of a job (about 35 seconds: service containers 20, then checkout,
Python, uv, install) dominates any residual imbalance.

**The partition guard is the point of the exercise.** Sharding by hand-written paths has one failure
mode that matters: a directory nobody claims stops running, silently, and CI stays green.
`test_every_test_file_runs_in_exactly_one_shard` expands every shard's paths — directories to files,
`--ignore=` subtracted — and fails unless they partition the suite exactly. A file claimed twice
fails too: it costs minutes and doubles its coverage data.

**A flake, found by the change itself.** The third CI run went red: the board's leak test
(`test_a_day_offline_leaves_the_heap_the_listeners_and_the_layout_where_they_were`) counted 45
listeners where it had 39, and did it again on the next run with the same two numbers. It measures
the page after a simulated day offline: one extra reconnect on a slower machine attaches a fixed six
listeners, and the assertion carries no tolerance — unlike its siblings in the same test, which
allow heap 10% + 512 KB and nodes 5%. It now sits beside the contrast tests in `board-measured`,
which runs on **one worker**, the same rule stated once: **a test that measures the board does not
share a machine.** The six that merely wait still overlap.

The test passed three times and failed twice today across shard layouts that were otherwise
identical, so this is a pre-existing sensitivity that a faster pipeline exposes rather than
something the sharding caused. If it fails again, the fix is a tolerance on that one assertion to
match the two beside it — a decision for whoever owns the board, not something to slip into a CI
pull request.

**The shards are not allowed to quietly run a different suite.**
`test_the_shards_default_to_the_local_gates_distribution` checks that a shard saying nothing runs
pytest exactly as `ci-local.sh` does (`-n auto --dist loadscope`), and that the only shards
deviating are browser shards.

**Why the browser tests stay slow, and what would change it.** `board-resilience` spends about 270
seconds waiting out the product's own budgets: a stream dropped, backed off, and live again inside
the 30-second recovery budget (Issue 57). No amount of sharding removes that, and four cores overlap
only so much of it — seven workers made it *worse* (312 s of test time against 250, each test slowed
by contention), which is why it runs on four. Those tests are the floor under the whole run.

## Changes

- **`.github/workflows/ci.yml`:** the matrix (4 → 16 shards, with `dist`/`workers`/`browser` per
  shard), the deploy sequence moved to `int-platform` and the 07:30 rush to `int-queue`, the
  Chromium install without `--with-deps`, `timeout-minutes: 10`.
- **`tests/conftest.py`:** `migration_template` (session-scoped, one per worker) and
  `migrated_database` rewritten as a copy of it.
- **`tests/e2e/dashboard/conftest.py`:** `e2e_database` copies the same template.
- **`tests/unit/platform/test_workflow_guardrails.py`:** the two guards above, plus `STAGE_COMMANDS`
  updated where the distribution flags became per-shard. The partition guard works test by test, not
  file by file: it reads each file's tests with `ast` (never importing them — it runs in the unit
  shard, which has no browser and no database) and understands `--deselect` beside `--ignore=`.
- **`docs/CICD/PIPELINES.md`:** the shard table with measured numbers, what a run costs now, the
  database-copy section, and the minutes projection.

## Testing

**The whole suite, locally, with the new fixtures** (PostgreSQL 18 + PostGIS in Docker, `TZ=UTC`):

```text
2562 passed, 1 skipped, 9 xfailed in 205.48s
```

**The fixture change, measured on one file** (20 tests, one process, local PostgreSQL):

```text
before: 20 passed in 61.82s   slowest setup 3.44s, typical setup ~3.0s
after:  20 passed in 36.37s   slowest setup 3.44s, typical setup 0.73s
```

**CI, end to end, on this branch** — every run green:

| Run | Shards | Wall clock | Slowest test job | Billable if private |
|-----|-------:|-----------:|-----------------:|--------------------:|
| [35071125042](https://github.com/Billykat7/clinicQ/actions/runs/35071125042) (before, on `main`) | 4 | 8m42s | 7m45s `integration` | 23 min |
| [35077177536](https://github.com/Billykat7/clinicQ/actions/runs/35077177536) | 14 | 3m57s | 3m11s `board-resilience` | 38 min |
| [35078037257](https://github.com/Billykat7/clinicQ/actions/runs/35078037257) | 16 | 3m40s | 2m46s `board-resilience` | 39 min |
| [35078698525](https://github.com/Billykat7/clinicQ/actions/runs/35078698525) | 16 | 3m30s | **red**: the board's leak test, 45 listeners against 39 | 39 min |
| [35079359435](https://github.com/Billykat7/clinicQ/actions/runs/35079359435) | 17 | 3m31s | **red**: the same test, the same two numbers | 40 min |
| [35079919283](https://github.com/Billykat7/clinicQ/actions/runs/35079919283) | 17 | **4m07s** | 3m20s `board-measured`, one worker | 41 min |

Every job in the green run, in order:

```text
200s board-measured     195s board-resilience  117s int-queue     112s unit-queue
111s int-notifications  108s int-platform      105s int-admin     104s browser-app
 99s int-sites           98s int-discovery      92s int-appointments  92s board-pages
 89s unit-security       85s int-dashboard      68s unit-rest     57s flows
 25s quality             21s report              8s conventions    8s changes   4s gate
```

The last two runs cost 36 seconds: `board-measured` on one worker is slower than the same tests
spread over four, and it is the difference between a gate that is green and a gate that is usually
green.

**Queue time is not hiding anything:** with sixteen shards the jobs waited 2–3 seconds each for a
runner (one waited 38 s), so the matrix is inside the Free plan's 20 concurrent jobs.

**The trade is minutes for wall clock,** and GitHub bills none of them for a public repository
(0 billable milliseconds). `PIPELINES.md` now carries the if-private projection — 3,400 minutes a
month against a 2,000 allowance — and says what to do about it if that ever becomes real.

- [x] `./scripts/ci-local.sh`'s stages pass on the branch (quality, secrets, tests, coverage in CI;
      the whole suite locally as above)
- [x] CI green on this pull request, three times, at three different matrix shapes
- [x] New guard tests cover the new failure mode (a shard that claims nothing, or claims twice)
- [ ] **Not done:** the gate is 4m07s, not the one minute asked for. The browser board shards are
      the floor; taking them off the pull-request gate would put it at about **1m20s**, and that is
      a decision about when a board regression is found, not a tuning knob. Left for the team.

## Risk and rollback

- **The template database is the one behavioural change.** If a test depended on the migrations
  *running* rather than on the schema existing, it would notice; none did (whole suite green, three
  CI runs). The migration tests under `tests/integration/database` still run Alembic themselves —
  they use `empty_database`, which is untouched.
- **`--with-deps` removed:** if a future runner image drops Chrome's libraries, Chromium fails to
  launch and the browser shards fail loudly (`REQUIRE_BROWSER_TESTS=1`), never silently skip.
- **Rollback is the matrix.** Grouping the shards back up is one edit to `ci.yml`; the guard tests
  fail if that edit loses a directory.

Refs #9
