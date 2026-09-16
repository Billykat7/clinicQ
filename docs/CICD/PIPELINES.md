# Pipelines: what runs, when, and what it costs

ClinicQ has one quality gate written twice: `./scripts/ci-local.sh` (`make check`), which every
developer runs before pushing, and `.github/workflows/ci.yml`, which runs the same checks on every
pull request to `main` and blocks the merge when one of them fails. Deployment happens on tags only
(Issues 10 and 11). Nothing runs on a push to a branch.

| Trigger | What runs | Where |
|---------|-----------|-------|
| Before every push | `make check`: quality, pip-audit, secrets, tests, coverage, docker + Trivy | your machine |
| Pull request to `main` | **CI**: the same stages minus pip-audit, Trivy and the compose smoke | GitHub Actions |
| Push to any branch, including `main` | nothing | — |
| Tag `v*.*.*` | `release.yml`: build once, check, publish to GHCR ([`RELEASE.md`](RELEASE.md)); then `deploy.yml` deploys staging ([`RUNBOOK_DEPLOY.md`](RUNBOOK_DEPLOY.md)) | GitHub Actions |
| Manual (`Run workflow` on Deploy) | production (after the DevOps/QA Lead approves), or a rollback | GitHub Actions |
| Manual (`Run workflow` on CI) | the whole CI run, image build included | GitHub Actions |

## The CI workflow, job by job

Each stage of `ci-local.sh` has a home in `ci.yml`, or is local-only on purpose:

| `ci-local.sh` stage | CI job | What it runs | Timeout |
|---------------------|--------|--------------|---------|
| — | `changes` | classifies the pull request: draft, prose-only, image inputs touched | 3 min |
| — | `conventions` | branch name, commit prefixes, `Closes #N`, a screenshot for UI changes (Issue 13) | 3 min |
| quality | `quality` | `ruff check .`, `ruff format --check .`, `mypy src/`, `scripts/check-ruff-pin.sh` | 10 min |
| secrets | `quality` | `gitleaks detect` over the whole history, the exact command `ci-local.sh` runs | (same job) |
| tests | `test` × 16 shards | `pytest -n auto --dist loadscope --cov`, in parallel; the deploy sequence first on `int-platform` | 10 min |
| coverage | `report` | combines the shards, checks `fail_under`, posts the summary | 5 min |
| docker | `docker` | `docker build -f infra/docker/Dockerfile .`, thrown away, never pushed | 15 min |
| pip-audit | — | **local-only** (`scripts/README.md`); Issue 97 schedules it | — |
| docker (Trivy) | — | **local-only**; Issue 97 schedules it | — |
| compose | — | **local-only**, the optional `--compose` smoke test | — |
| — | `gate` | **CI gate**: green only if every job above passed or was skipped on purpose | 2 min |

`tests/unit/platform/test_workflow_guardrails.py` fails if a stage is added to `ci-local.sh` without
a place in this table, if a command stops appearing on both sides, or if pip-audit or Trivy appear
in `ci.yml`.

### The sixteen test shards

The suite runs as sixteen jobs side by side, so a pull request waits for the slowest shard rather
than for the sum. Four shards used to mean waiting **7m45s** for `integration` alone, and **8m42s**
for the run.

| Shard | Collects | Test time | Job |
|-------|----------|----------:|----:|
| `int-discovery` | `tests/integration/discovery` | 166 s | 98 s |
| `int-queue` | `tests/integration/queue`; then the 07:30 rush, alone | 156 s | 117 s |
| `int-sites` | `tests/integration/sites`, `auth`, `alerts`, `public` | 169 s | 99 s |
| `int-platform` | `tests/integration/platform`, `database`; the deploy sequence first | 164 s | 108 s |
| `int-appointments` | `tests/integration/appointments`, `patients`, `staff` | 158 s | 92 s |
| `int-notifications` | `tests/integration/notifications`, `contracts` | 116 s | 111 s |
| `int-admin` | `tests/integration/admin`, `security` | 120 s | 105 s |
| `int-dashboard` | `tests/integration/dashboard`, `display` | 67 s | 85 s |
| `unit-queue` | `tests/unit/queue` | 47 s | 112 s |
| `unit-security` | `tests/unit/security` | 54 s | 89 s |
| `unit-rest` | the other eleven `tests/unit/` directories | 25 s | 68 s |
| `flows` | `tests --ignore=tests/unit --ignore=tests/integration --ignore=tests/e2e` | 4 s | 57 s |
| `board-resilience` | `tests/e2e/display/test_board_resilience.py` bar the leak test, by test | 235 s | 195 s |
| `board-measured` | the board's contrast and leak tests, **one worker** | 164 s | 200 s |
| `board-pages` | the rest of `tests/e2e/display` | 104 s | 92 s |
| `browser-app` | `tests/e2e/patient`, `tests/e2e/dashboard` | 101 s | 104 s |

**Test time** is what the shard's own JUnit file reports, added up; it is what the balance is based
on, and it is larger than the job because four workers run at once. **Job** is the wall clock,
measured on run
[35079919283](https://github.com/Billykat7/clinicQ/actions/runs/35079919283).

The gap between them is the fixed cost of a job: **about 35 seconds** of service containers (20),
checkout, Python, uv and install, before pytest starts. That is why directories are grouped rather
than given a job each: under about 20 seconds of tests, a shard is mostly overhead.

**Two guards keep the matrix honest.** `test_every_test_file_runs_in_exactly_one_shard` expands
every shard's paths (directories to files, `--ignore=` subtracted) and fails unless they partition
the suite exactly: a test directory added without a shard to claim it would otherwise stop running
the day it was created, silently and greenly, and a file in two shards costs minutes and doubles its
coverage data. `test_the_shards_default_to_the_local_gates_distribution` holds every shard to
`ci-local.sh`'s own `-n auto --dist loadscope` unless it is a browser shard.

Sixteen is not a floor but a ceiling: it is what fits under the Free plan's **20 concurrent jobs**
beside `changes`, `conventions`, `quality` and `docker`. Queue time at sixteen is 2–3 seconds a job.

### What the run costs now

| | Before (4 shards) | Now (16 shards) |
|---|---:|---:|
| Whole run, wall clock | 8m42s | **4m07s** |
| Slowest test job | 7m45s | 3m20s |
| Jobs in the run | 9 | 21 |
| Billable **if this repository went private** | 23 min | 39 min |

Sharding buys wall clock with minutes, and GitHub bills none of them while the repository is public
(the run-timing API reports 0 billable milliseconds). The last row is the one to watch if that ever
changes: at 39 minutes a run the projection below no longer fits the Free plan, and the answer would
be to group the shards back up — the matrix is one edit, and the guard tests will hold it together.

**What is left, and why.** The floor is the browser board suite: `board-resilience` spends about
270 seconds of test time waiting out the product's own budgets — a stream dropped, backed off, and
live again inside the 30-second recovery budget (Issue 57) — and four cores can only overlap so
much of that. Taking those two board shards off the pull-request gate (nightly, or on demand) would
put the gate at about **1m20s**; it would also mean a board regression is found the next morning
rather than on the pull request, which is a decision for the team rather than a tuning knob.

### A database copy, not a migration run

Most of the integration suite's time used to be Alembic. Every test that asked for a schema
(`migrated_database`, `migrated_engine`, and the `e2e_database` each browser module builds on) ran
the whole migration history first: about two seconds a test.

The migrations now run once per xdist worker, into a template database, and each test's database is
a `CREATE DATABASE ... TEMPLATE` copy of it — a file copy PostgreSQL does in a fraction of a second.
The contract is unchanged: a test still gets its own brand-new database at `head`, dropped
afterwards, sharing nothing with anything else. One template per worker, because PostgreSQL refuses
to copy a template another session is connected to.

Measured on `tests/integration/discovery/test_nearby_search.py` (20 tests, one process, local
PostgreSQL): **61.8 s to 36.4 s**, with per-test setup falling from 3.0 s to 0.73 s. In CI the
discovery directory went from 318 s of test time to 168 s. Directories that mostly use the in-memory
SQLite `session_factory` (Issue 3) barely moved, which is the expected shape of the win.

### The browser shard (Issue 55)

`tests/e2e` holds what only a browser can show: that a press changes the page at once, that the
dashboard says *Offline* within ten seconds and comes back live without a reload, that an action pressed
offline is sent on reconnect or reported as not sent, that a walk-in can be issued by keyboard alone.
Each module starts the application in a background thread on a free port, against a PostgreSQL database
migrated for it, and drives it with one headless Chromium per worker. `tests/e2e/conftest.py` holds
the two fixtures (`browser`, `serve`). The waiting-room board's suite (`tests/e2e/display`, Issue 56
onwards) reuses them: its layouts at 1080p and 720p, the cap height of every number in millimetres on a
32-inch screen, the new-call highlight with and without reduced motion, and an eight-hour day on the
page's own clock. From Issue 57 it also covers a call reaching the board within 2 seconds, a server
restarted under an open board, a silent stream noticed within 30 seconds, a ten-minute outage, and
streams that vanish without closing.

- **Locally:** `pip install -r requirements.txt`, then `python -m playwright install chromium` once, then
  `pytest tests/e2e/dashboard` (with `TEST_DATABASE_URL`, like every PostgreSQL test). Without Chromium
  the tests skip and say how to install it.
- **In CI:** the shard caches `~/.cache/ms-playwright` by the `requirements.txt` hash and runs
  `python -m playwright install --with-deps chromium`, then the same pytest command as every shard.
  `REQUIRE_BROWSER_TESTS=1` turns "no browser" into a failure, as `REQUIRE_POSTGRES_TESTS` does for the
  database.
- **Time budget:** these are the slowest shards in CI, and they are slow for a reason that no amount
  of sharding removes: they wait out the product's own budgets. The board's seven resilience tests
  spend about 250 seconds between them watching a stream drop, back off, and come back inside the
  30-second recovery budget (Issue 57). The `board-measured` shard runs one test at a time because
  its tests measure the board rather than exercise it — the contrast of every text as drawn, and
  whether a day offline leaked listeners or heap — and a busy machine changes the answer. The leak
  test proved it: run beside its six neighbours it reported 45 listeners where it had 39.
  A browser test that needs to wait uses Playwright's `expect` with a timeout, never a fixed sleep,
  except where waiting *is* the test.
- **Distribution:** the browser shards hand out tests one by one (`--dist load`) rather than by
  module. Each xdist worker is its own process and builds its own module fixtures — its own
  database, its own server on a free port, its own browser — so nothing is shared and the waits
  overlap. Four workers, not more: seven on a four-core runner made every resilience test slower
  than it is alone (312 seconds of test time against 250).

Playwright and its `pyee` dependency are test-time only: the image build removes them with the other
test tooling, so they add nothing to the shipped image.

### Real PostgreSQL and Redis, never a stand-in

Every shard gets two service containers, the exact images the dev stack pins:
`postgis/postgis:18-3.6` and `redis:8-alpine` (decision 4). The tests reach them through
`TEST_DATABASE_URL` and `TEST_REDIS_URL`, and CI sets `REQUIRE_POSTGRES_TESTS=1` and
`REQUIRE_REDIS_TESTS=1`, which turn "no server" from a skip into a failure: a container that never
started cannot pass as green. `make test-services` runs the same markers locally against the
compose stack.

The `int-platform` shard also runs `scripts/db/deploy-sequence.sh` on the service database before
its tests: `alembic upgrade head`, the RBAC seed and its `--check`. The deploy (Issue 11) runs the same
script inside the new image, so a migration or seed that breaks fails on the pull request, not
during a release.

After its parallel run, the `int-queue` shard runs one more step, **the 07:30 rush** (Issue 47), on
its own. It asserts a latency budget, so the parallel run skips it: measured while every CPU is busy
with other tests, it would measure the runner rather than the queue engine. It adds about 10–30
seconds.

Most tests still use the in-memory SQLite `session_factory` fixture by design (Issue 3: reach for the
real database only for what PostgreSQL does and SQLite does not). CI never points the application
at SQLite: every database URL it sets is a `postgresql://` URL, and the guard test checks that.

## The required check and the merge button

Branch protection requires exactly one check: **CI gate**. The ruleset is kept as code in
[`.github/rulesets/main-ci-gate.json`](../../.github/rulesets/main-ci-gate.json), with the check
pinned to the GitHub Actions app so no other integration can report it. It has no bypass, for
administrators either. Issue 13 adds the approval and push rules beside it.

Requiring one aggregate check, rather than each job by name, keeps the ruleset stable when jobs are
added or renamed: `gate` lists every other job in `needs`, and the guard test fails if one is left
out. `gate` runs with `if: always()`, so a failed or cancelled job turns it red rather than leaving
it pending.

"Require branches to be up to date before merging" is **off**. Turning it on would force every open
pull request to rebase and re-run after each merge, which multiplies the minutes by the number of
open PRs; the daily-rebase rule in `CONTRIBUTING.md` covers the risk more cheaply.

## What skips, and why the gate still passes

| Pull request | Jobs that run | Gate |
|--------------|---------------|------|
| Draft | `changes` and `conventions` | green, with "runs when marked ready for review" on the summary |
| Only prose files changed | `changes` and `conventions` | green |
| Code changed | `changes`, `conventions`, `quality`, `test` × 16, `report` | green when all pass |
| `infra/docker/Dockerfile`, `requirements.txt` or `.dockerignore` changed | the above plus `docker` | green when all pass |

Marking a draft ready for review starts a full run. Prose means the folders and files in
`PROSE_PATHS` in `ci.yml`: the issue specs, milestones, release notes and runner notes under
`docs/GITHUB/`, `docs/GITHUB/README.md`, `docs/PLAN/`, `docs/PROJECTS/`,
`docs/DEMO/`, `docs/IDE/`, `README.md` and `CONTRIBUTING.md`. No test reads any of them, and
`test_the_prose_only_fast_path_skips_nothing_a_test_reads` keeps it that way: if a test starts
reading one, the fix is to drop that entry from `PROSE_PATHS`. That happened once already: Issue
13's tests read the PR template, `labels.yml` and `docs/TEAM/WORKLOAD_SPLIT.md`, so
`docs/GITHUB/PR/`, `docs/GITHUB/LABELS/` and `docs/TEAM/` left the list. It happened again in Issue
41: the ticket lifecycle's state diagram in `docs/PRODUCT/03-booking-and-queue.md` is checked against
the transition table, so `docs/PRODUCT/` left the list too. `docs/SECURITY/` and
`docs/CICD/` are not prose either, because tests read them.

The trigger itself is never path-filtered. A path filter would stop the workflow from starting at
all, and a required check that never reports leaves the pull request blocked for good.

## Cancelling superseded runs

```yaml
concurrency:
  group: ci-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

A new push to a pull request cancels the run still going for the previous push (measured on
PR #121: the second push, 27 seconds after the first, cancelled the first run's jobs). Two pull
requests never cancel each other, and a manual run is never cut short.

## Caching

| Cache | Key | Saves |
|-------|-----|-------|
| uv downloads (`~/.cache/uv`, about 230 MB) | `requirements.txt` + `pyproject.toml` | the install step drops to a few seconds |
| ruff and mypy (`.ruff_cache`, `.mypy_cache`) | requirements + the hash of `src/**/*.py` | incremental mypy |
| Docker layers (`type=gha`) | Buildx scope `clinicq-image` | the image build: 4 min 50 s cold, 19 s warm |

CI installs `requirements.txt` with `uv pip install`, the same file `ci-local.sh` and the Docker
image install; uv is only faster. The guard test fails if a job caches pip beside a uv install, or
the reverse.

## Supply chain

Every third-party action is pinned to a full commit SHA with its release in a comment
(`actions/checkout@3d3c42e… # v7.0.1`). A tag can be moved to different code after review; a
commit cannot. The guard test fails on an unpinned reference. gitleaks is downloaded at the version
the pre-commit hook pins (8.27.2) and checked against the release's SHA-256 before it runs. The
workflow token is read-only (`contents: read`); only the `report` job may write, and only a
pull-request comment. CI holds no `packages: write` and cannot publish an image.

## Minutes: measured, and projected against 2,000 a month

**The repository is public, and GitHub does not bill standard runners for public repositories:**
the run-timing API reports 0 billable milliseconds for every run on PR #120. The projection below
is what the same usage would cost if the repository became private, measured against the Free
plan's 2,000 minutes. GitHub rounds each job **up to a whole minute**, which is why the short
`changes` and `gate` jobs count as a minute each.

Measured on PR #217's runs, which changed the shape of the matrix (four shards to sixteen):

| Run | Wall clock | Jobs | Billable if private |
|-----|-----------:|-----:|--------------------:|
| Code change, four shards (before) | 8 min 42 s | 9 | **23 min** |
| Code change, sixteen shards (now) | 4 min 07 s | 21 | **41 min** |
| Draft or prose-only pull request | under 30 s | 3 | **3 min** |
| Superseded push, cancelled | partial | — | about half a run |

A cold image build adds a `docker` job of about 5 minutes to either shape.

Projection for a month, for six people and about 20 pull requests (109 issues over 28 weeks is
about 17, plus follow-ups):

| Item | Assumption | Minutes |
|------|------------|--------:|
| Code pushes | 20 PRs × 4 pushes that reach CI (each after `make check`) × 41 min | 3,280 |
| Image-input pushes | 10% of those again, at the cold price: 8 × 5 | 40 |
| Superseded pushes | 1 in 10 pushes cancelled part-way: 8 × 20 min | 160 |
| Prose-only and draft pushes | 15 pushes × 3 min | 45 |
| Releases and deploys | 2 tags × (release, measured 4 min cold, + deploy) | 50 |
| **Total** | | **≈ 3,575** |

**That is above the 2,000-minute Free-plan allowance, and it does not matter while the repository is
public**, because GitHub bills none of it. It is written down so the trade is explicit: the same
work was 890 minutes over four shards and 8m42s a run. If ClinicQ ever goes private, the matrix is
where to look first — grouping the sixteen shards back into six or seven would put the projection
back inside the allowance at about six minutes a run.

What still protects the budget is written down as a test, not a habit: the timeouts
(`timeout-minutes` on every job, 10 at most on a test shard, so a wedged job costs 10 minutes rather
than 360), the concurrency group, the prose and draft fast paths, and the absence of any
branch-push trigger.

Check the real figure at any time under **Settings → Billing → Usage**, or per run:

```bash
gh api repos/Billykat7/clinicQ/actions/runs/<run-id>/timing
```

## Reading a red run

1. Open the pull request's **Checks** tab. The PR comment ("CI report for `<sha>`") lists each
   shard's counts and every failing test by name; the run's summary page carries the same.
2. **CI gate** red and another job red: fix that job. The gate only reports.
3. **Quality and secrets** red: run `make check-fast` locally; the step name is the command that
   failed, and ruff's findings are annotated on the diff.
4. **Tests** red in one shard only: `pytest <that shard's paths>` locally, with `make test-services`
   if it is the integration shard.
5. **Test report and coverage** red while the tests passed: coverage fell below `fail_under`. Test
   the code you added; never lower the floor to get a change through.

## Changing the pipeline

- Add a stage to `ci-local.sh`: add it to `STAGE_JOBS` and `STAGE_COMMANDS` in the guard test, and
  a job or step here.
- Add a job to `ci.yml`: give it `timeout-minutes` (15 at most) and add it to `gate.needs`.
- Add a workflow file: add a member to `Workflow` in the guard test (the file is not covered
  until you do), and remove its entry from `NOT_YET_CREATED` if a later issue planned it.
- Bump an action: pin the new release's commit SHA and update the version comment.
- Bump gitleaks: change `.pre-commit-config.yaml`, then the version and SHA-256 in `ci.yml`; the
  guard test fails while they differ.
