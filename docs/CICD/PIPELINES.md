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
| tests | `test` × 4 shards | `pytest -n auto --dist loadscope --cov`, after the deploy sequence (integration shard) | 15 min |
| coverage | `report` | combines the shards, checks `fail_under`, posts the summary | 5 min |
| docker | `docker` | `docker build -f infra/docker/Dockerfile .`, thrown away, never pushed | 15 min |
| pip-audit | — | **local-only** (`scripts/README.md`); Issue 97 schedules it | — |
| docker (Trivy) | — | **local-only**; Issue 97 schedules it | — |
| compose | — | **local-only**, the optional `--compose` smoke test | — |
| — | `gate` | **CI gate**: green only if every job above passed or was skipped on purpose | 2 min |

`tests/unit/platform/test_workflow_guardrails.py` fails if a stage is added to `ci-local.sh` without
a place in this table, if a command stops appearing on both sides, or if pip-audit or Trivy appear
in `ci.yml`.

### The four test shards

| Shard | Collects | Why separate |
|-------|----------|--------------|
| `unit` | `tests/unit` | fast, no server needed |
| `integration` | `tests/integration` | HTTP, the database layer, Redis; runs the deploy sequence first |
| `flows` | `tests --ignore=tests/unit --ignore=tests/integration --ignore=tests/e2e` | the top-level cross-domain flow files, and any added later |
| `browser` | `tests/e2e` | a real server and Chromium driven by Playwright (Issue 55): it installs the browser first |

The shards run in parallel, so the slowest one (integration, about 1.5 minutes) sets the wall
clock. The guard test fails if the `flows` collector disappears, because those files sit in neither
layer folder and would silently drop out of CI.

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
- **Time budget:** the eight dashboard tests take about 30 seconds under `-n auto` (22 seconds in one
  process), and the thirteen board tests about 40 seconds under `-n auto`. Installing Chromium with its system libraries about a minute, so the shard stays well
  inside its 15-minute timeout and adds one to two billable minutes to a run. A browser test that needs
  to wait uses Playwright's `expect` with a timeout, never a fixed sleep, except where waiting *is*
  the test (an action held longer than the outbox allows, set to 8 seconds for the suite).

Playwright and its `pyee` dependency are test-time only: the image build removes them with the other
test tooling, so they add nothing to the shipped image.

### Real PostgreSQL and Redis, never a stand-in

Every shard gets two service containers, the exact images the dev stack pins:
`postgis/postgis:18-3.6` and `redis:8-alpine` (decision 4). The tests reach them through
`TEST_DATABASE_URL` and `TEST_REDIS_URL`, and CI sets `REQUIRE_POSTGRES_TESTS=1` and
`REQUIRE_REDIS_TESTS=1`, which turn "no server" from a skip into a failure: a container that never
started cannot pass as green. `make test-services` runs the same markers locally against the
compose stack.

The integration shard also runs `scripts/db/deploy-sequence.sh` on the service database before the
tests: `alembic upgrade head`, the RBAC seed and its `--check`. The deploy (Issue 11) runs the same
script inside the new image, so a migration or seed that breaks fails on the pull request, not
during a release.

After the parallel run, the integration shard runs one more step, **the 07:30 rush** (Issue 47), on
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
| Code changed | `changes`, `conventions`, `quality`, `test` × 3, `report` | green when all pass |
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

Measured on PR #120 and its manual runs:

| Run | Wall clock | Jobs (rounded up) | Billable if private |
|-----|-----------:|-------------------|--------------------:|
| Code change, image not touched | 2 min 20 s | changes 1, conventions 1, quality 1, unit 1, integration 2, flows 1, report 1, gate 1 | **9 min** |
| Code change touching the image inputs, warm cache | 2 min 6 s | the above + docker 1 | **10 min** |
| The same, cold Docker cache | 5 min 12 s | the above + docker 5 | **14 min** |
| Draft or prose-only pull request | under 30 s | changes 1, conventions 1, gate 1 | **3 min** |
| Superseded push, cancelled | partial | about half a run | **~4 min** |

A typical run finishes in under five minutes (the acceptance criterion); only a cold image build
comes close to it.

Projection for a month, for six people and about 20 pull requests (109 issues over 28 weeks is
about 17, plus follow-ups):

| Item | Assumption | Minutes |
|------|------------|--------:|
| Code pushes | 20 PRs × 4 pushes that reach CI (each after `make check`) × 9 min | 720 |
| Image-input pushes | 10% of those again, at the cold price: 8 × (14 − 9) | 40 |
| Superseded pushes | 1 in 10 pushes cancelled part-way: 8 × 4 min | 32 |
| Prose-only and draft pushes | 15 pushes × 3 min | 45 |
| Releases and deploys | 2 tags × (release, measured 4 min cold, + deploy, reserved for Issue 11) | 50 |
| **Total** | | **≈ 890 (45% of 2,000)** |

Doubling the pushes (8 a pull request, nobody running `make check`) gives about 1,700 minutes,
still inside the allowance. (Issue 13's `conventions` job added a minute to every run: 790 became
890.) What protects the budget is written down as a test, not a habit: the
timeouts (`timeout-minutes` on every job, 15 at most in CI, so a wedged job costs 15 minutes rather
than 360), the concurrency group, the prose and draft fast paths, and the absence of any branch-push
trigger.

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
