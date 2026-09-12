# PR: Run the ci-local.sh checks on every pull request against PostGIS and Redis (Issue 9 / M2-09)

**Milestone:** [Milestone 2: CI/CD, Environments & Team Workflow](https://github.com/Billykat7/clinicQ/milestone/2) ·
**Issue:** [#9](https://github.com/Billykat7/clinicQ/issues/9)

Until now the only gate was `make check`, and nothing stopped a pull request that skipped it. This
PR adds `.github/workflows/ci.yml`: every pull request to `main` runs the stages of
`scripts/ci-local.sh` against real `postgis/postgis:18-3.6` and `redis:8-alpine` service
containers, and a ruleset on `main` requires its one aggregate check, **CI gate**, before anything
merges. A typical run takes 2 minutes 20 seconds. Its first run earned its keep: it found that
`src/static/vendor/` (htmx and Leaflet) had never been committed, so the Issue 5 shell has served
its htmx as a 404 on every clean checkout. That is fixed here too.

## Summary

- **The merge gate.** Jobs `quality` (ruff, mypy, the ruff pin, gitleaks over the whole history),
  `test` (three pytest shards with coverage, running the deploy sequence first), `report` (one
  coverage floor over all shards, a test summary on the run page and one PR comment kept up to
  date), `docker` (the image builds, only when its inputs change) and `gate`. The ruleset
  `main: CI gate` requires `gate`, pinned to the GitHub Actions app, with no bypass.
- **Real services, and tests that cannot skip past them.** A new `redis` marker mirrors Issue 3's
  `postgres` one: CI sets `REQUIRE_POSTGRES_TESTS=1` and `REQUIRE_REDIS_TESTS=1`, so a service
  container that did not start fails the run instead of skipping 16 tests. Two new tests use a real
  Redis; `make test-services` runs the same markers locally.
- **Cheap by construction.** A timeout on every job (15 minutes at most), a concurrency group that
  cancels a superseded push, no trigger on any branch push, and a fast path that skips the heavy
  jobs for a draft or a prose-only change while still reporting the required check.
- **The guards.** The inherited guard test described the source project's tag-only CI, which
  contradicts this issue. Only the assertions that encoded "CI never runs on a pull request" were
  rewritten (listed below); every other guard is kept, and ten new ones pin what the gate must keep
  true. Each was shown to fail when its rule is broken.
- **One deploy sequence.** `scripts/db/deploy-sequence.sh` (`alembic upgrade head`, the RBAC seed,
  its `--check`) runs on the PostGIS service in CI; Issue 11 runs the same script inside the new
  image before the swap.

## Design notes

**Which design won, and what changed in the guard tests.** `tests/unit/platform/test_workflow_guardrails.py`
and `tests/integration/security/test_m30_exit_criteria.py` came from the sibling project, whose CI
ran on tags only. They asserted that `ci.yml` never runs on a pull request, required its six
workflow files (`cd`, `smoke`, `deploy-sequence`, `sync-issues`…), and required pip-audit and
Trivy in Actions. Read literally, no workflow could gate a pull request, which is this issue's first
acceptance criterion and the milestone's stated shape (`docs/GITHUB/README.md`, "Pipeline
strategy"). The team chose the PR-gated design; this is every assertion that changed:

| Inherited assertion | Now | Why |
|---------------------|-----|-----|
| `test_ci_itself_never_runs_on_a_pull_request` | `test_ci_runs_on_pull_requests_to_main_and_on_nothing_else_automatic` | CI is the merge gate; still no push trigger, no path filter |
| `test_nothing_runs_unfiltered_on_an_ordinary_push_or_pull_request` | `test_nothing_runs_on_an_ordinary_push_and_only_the_gate_runs_on_every_pull_request` | `ci.yml` is the one unfiltered PR workflow; the `sync-issues` branch-push exception is gone (stricter) |
| `test_ci_never_cancels_a_run_that_can_publish_an_image` | `test_ci_cancels_only_superseded_pull_request_runs` and `test_ci_can_never_publish_an_image` | CI cannot publish here (no `packages: write`, no login, no push), so the invariant holds structurally |
| `test_the_deploy_sequence_is_identical_in_both_workflows` | same name, `ci.yml` versus `deploy.yml`, through one script | the sequence lives in `scripts/db/deploy-sequence.sh`; pending on Issue 11 |
| `test_deploys_are_never_cancelled_mid_flight` | same body, `cd.yml` renamed `deploy.yml` | Issue 11's file name; pending on Issue 11 |
| `test_every_workflow_file_is_covered` | same assertion, over ClinicQ's list, minus `NOT_YET_CREATED` | the list is `ci`, `release` (Issue 10), `deploy` (Issue 11), `vulnerability-scan` (Issue 97); a listed file that exists fails the test |
| m30 `test_ci_itself_is_still_tag_only` | `test_ci_gates_every_pull_request_to_main` | as above |
| m30 `test_the_deploy_sequence_runs_on_migration_changes` | reads `ci.yml`, which runs the sequence on every PR | there is no `deploy-sequence.yml`; `alembic/` and `scripts/db/` can never take the prose fast path |

Kept word for word: the concurrency group, runner timeouts, reusable-workflow timeouts, the uv-cache
rule, the top-level flow shard, the disabled-workflow rule and the weekly scan rule (pending on
Issue 97). pip-audit and Trivy stay local-only (`scripts/README.md`); their four tests stay strict
expected failures, now reasoned as "waits for Issue 97" instead of "waits for Issue 9".

**One required check, not seven.** The ruleset requires `CI gate`, and `gate` needs every other job
and runs `if: always()`. Adding or renaming a job then needs no settings change, and
`test_the_merge_gate_needs_every_other_job` fails if a job is left out of `needs`. The check is
pinned to the Actions app (integration 15368), so no other integration can report it green.

**Skip inside the run, never at the trigger.** A path-filtered `pull_request` trigger would leave a
docs-only PR's required check pending for ever. So the trigger is unfiltered, and the `changes` job
decides: a draft, or a change made only of `PROSE_PATHS` (folders no test reads), runs two
one-minute jobs and passes. `test_the_prose_only_fast_path_skips_nothing_a_test_reads` parses every
test file for the paths it names and fails if one reads a prose path; `docs/SECURITY/` and
`docs/CICD/` are not prose for exactly that reason.

**"Never SQLite", precisely.** CI's database is the PostGIS service; every database URL CI sets is
`postgresql://`, the migrations and seed run against it, and the 14 PostgreSQL tests cannot skip. The
many tests that use the in-memory SQLite `session_factory` fixture keep doing so: that is Issue 3's
documented design (real database only for what PostgreSQL does and SQLite does not), and rewriting
them is a change of its own, not a CI change.

**Three shards cost minutes; they are kept because the guard requires the `flows` collector.** It
exists so the top-level flow tests cannot fall out of CI. The shards also run in parallel, which is
what keeps the wall clock near two minutes. GitHub rounds each job up to a minute, so the price is
about 8 billable minutes a run instead of 5 or so for one job.

**Supply chain.** Every action is pinned to a commit SHA, with a guard. gitleaks runs at the
pre-commit hook's version (8.27.2) after a SHA-256 check, with a guard that the two versions match.
The token is read-only except `pull-requests: write` on the `report` job, for its one comment.

**The vendored assets.** A personal `~/.gitignore_global` line (`vendor/`) had kept
`src/static/vendor/` out of git since Issue 5. The repository `.gitignore` now re-includes it, which
wins over any global file, and the files are committed byte for byte (their `.gitattributes` keeps
them `-text`). The v0.1.0 image was built from a working tree that had them, but a clean clone never
did.

**Out of scope:** publishing images (Issue 10), deploying (Issue 11), the approval and push rules
on `main` (Issue 13), and scheduled pip-audit and Trivy (Issue 97).

## Changes

- **`.github/workflows/ci.yml`** (new): the workflow described above.
- **`.github/rulesets/main-ci-gate.json`** (new): the ruleset applied to `main` (ruleset id 22938115).
- **`tests/unit/platform/test_workflow_guardrails.py`:** the rewrites in the table, ClinicQ's workflow
  list, and ten new guards: the stage mirror, the same commands, local-only scans, the gitleaks
  version, PostGIS and Redis services, the gate's `needs`, the CI timeout ceiling, action pins, the
  publish ban and the prose fast path.
- **`tests/integration/security/test_m30_exit_criteria.py`:** the two retargeted tests.
- **`tests/conftest.py`:** `PENDING_ON_LATER_ISSUES` down from 23 entries to 11 (Issue 11: 2, Issue 97:
  5, the security documents: 4, unchanged); the `redis_server_url` fixture.
- **`tests/integration/platform/test_redis_service.py`** (new): readiness gets `PONG` from a real
  server; two limiters spend one budget in the real store.
- **`scripts/db/deploy-sequence.sh`** (new), **`scripts/ci_test_summary.py`** (new): the deploy
  sequence, and the JUnit-to-Markdown summary the `report` job posts.
- **`Makefile`:** `make test-services`. **`pyproject.toml`:** the `redis` marker.
- **`.gitignore`**, **`src/static/vendor/`**: the re-include and the ten vendored files.
- **`docs/CICD/PIPELINES.md`** (new): jobs, the required check, the fast paths, caching, the
  minutes, how to read a red run and how to change the pipeline.
- **`CONTRIBUTING.md`**, **`README.md`**, **`scripts/README.md`**, **`scripts/ci-local.sh`**,
  **`docs/GITHUB/README.md`:** say what CI now does; the claims that pip-audit blocks in CI, that a
  `vulnerability-scan.yml` exists and that "Smoke is Actions → Smoke" were wrong and are gone.

## Testing

- [x] **`./scripts/ci-local.sh`** green before the first push (quality 1 s, pip-audit 28 s, secrets
      1 s, tests 23 s, coverage 1 s, docker + Trivy 5 s cached).
- [x] **The suite, locally:** `pytest tests/ -n auto`: **957 passed, 16 skipped, 11 xfailed** (was 935
      passed, 14 skipped, 23 xfailed). The 16 skips are the server tests without URLs;
      `make test-services DB_PORT=5433 REDIS_PORT=6380` runs them: **16 passed** (14 PostgreSQL, 2 Redis).
- [x] **CI's first run caught a real defect** (run 34619998276): the integration shard failed on
      `test_the_shells_code_styles_and_fonts_are_served_by_this_app[/static/vendor/htmx-2.0.0.min.js]`,
      `assert 404 == 200`, and `CI gate` went red. `git check-ignore -v` named the cause:
      `~/.gitignore_global:81:vendor/`. After the fix (run 34620349984), every job passed:

      ```text
      Changed paths         success  6 s        Tests (flows)        success  47 s
      Quality and secrets   success  19 s       Test report          success  23 s
      Tests (unit)          success  58 s       Image builds         skipped  (inputs unchanged)
      Tests (integration)   success  1 min 33   CI gate              success  3 s
      run: 2 min 20 s wall clock
      ```

      The PR comment it posted: flows 11 passed, integration 338 passed and 6 xfailed, unit 624
      passed and 5 xfailed; coverage **77.3%** of `src/` (floor 75%).
- [x] **A deliberately failing test blocks the merge** (demo PR #121, closed unmerged): one test
      asserting `1 + 1 == 3`. CI: `Tests (unit)` failure, `CI gate` failure, and

      ```text
      $ gh pr view 121 --json mergeable,mergeStateStatus
      mergeable=MERGEABLE mergeStateStatus=BLOCKED
      ```

      (no conflict; blocked by the required check). Removing the test on the same PR: run success,
      `mergeStateStatus=CLEAN`.
- [x] **Two pushes within a minute cancel the first run** (PR #121): run 34620724455 for `20fff6c`
      started 16:13:16 UTC; the push of `30a1492` at 16:13:43 started run 34620767849, and the first
      run ended `cancelled` (its unit shard, started 16:13:29, was cancelled at 16:14:18). Every
      job of the first run shows `cancelled`, apart from `changes` (already done), the skipped
      image build and the gate (red, as it should be for a cancelled run).
- [x] **The image job works** (manual runs): cold Buildx cache, `Image builds` 4 min 50 s and the run
      5 min 12 s (34621481690); warm cache, 19 s and 2 min 6 s (34622010850, `#8 CACHED` …).
- [x] **Each new guard fails when its rule is broken:** eight one-line mutations of `ci.yml` in a
      scratch worktree, each caught:

      | Mutation | Fails |
      |----------|-------|
      | `quality` loses `timeout-minutes` | `test_runner_jobs_cap_their_own_runtime`, `test_ci_jobs_stay_well_inside_the_default_timeout` |
      | `push: branches: [main]` added | both trigger tests |
      | `actions/checkout@v7` instead of the SHA | `test_every_action_is_pinned_to_a_commit` |
      | `docker` dropped from `gate.needs` | `test_the_merge_gate_needs_every_other_job` |
      | `pip-audit` added to a step | `test_the_local_only_scans_stay_out_of_ci` |
      | CI image `postgis/postgis:17-3.5` | `test_ci_tests_run_against_postgis_and_redis_never_sqlite`, `test_one_postgresql_image_is_in_play…` |
      | a test reads `docs/TEAM/WORKLOAD_SPLIT.md` | `test_the_prose_only_fast_path_skips_nothing_a_test_reads` |
      | `cancel-in-progress: true` | `test_ci_cancels_only_superseded_pull_request_runs` |

- [x] **The deploy sequence** on a throwaway database on the local PostGIS: `Running upgrade -> 0001`,
      `rbac seed: roles +2, resources +26, …, permissions +107`, `rbac seed --check: in sync`, 3 s.
- [ ] **Not shown:** a prose-only pull request taking the fast path. This PR changes code, and PR #121
      did too; the branch logic is in the `changes` job and the guard test holds its folder list.
- [ ] **Not shown:** a screenshot of the merge box. The Chrome extension did not respond; the
      `mergeStateStatus` above is the API's reading of the same button.

## Acceptance criteria

- [x] A pull request runs the full suite against PostGIS and blocks merge on failure: PR #121,
      `BLOCKED` while `CI gate` was red, `CLEAN` once fixed; the PostgreSQL and Redis tests are
      required, not skippable, in CI.
- [x] A typical run completes in under 5 minutes: 2 min 20 s (code change), 2 min 6 s with a warm
      image build; only a cold image build reaches 5 min 12 s.
- [x] Pushing twice in a minute cancels the first run: runs 34620724455 → `cancelled` and
      34620767849, 27 s apart.
- [x] Every job has an explicit timeout well below the GitHub default: 2 to 15 minutes against 360,
      asserted by two tests.
- [x] The workflow is the same set of checks as `ci-local.sh`, verified by a guard test:
      `test_every_ci_local_stage_is_mirrored_or_declared_local_only` and
      `test_ci_runs_the_same_commands_as_ci_local`.
- [x] Monthly Actions consumption is projected here and stays inside the free allowance:

      | | Minutes |
      |---|---:|
      | A code-change run, billable if private (each job rounded up) | 8 |
      | A draft or prose-only run | 2 |
      | A month: 20 PRs × 4 pushes, image and cancelled runs, 15 prose pushes, 2 releases | **≈ 790 of 2,000 (40%)** |
      | The same with twice the pushes | ≈ 1,450 |

      The repository is public, and GitHub reports **0 billable ms** for these runs
      (`gh api …/actions/runs/34620349984/timing`), so today it costs nothing; the projection is
      the cost if it became private. The working is in `docs/CICD/PIPELINES.md`.

## Risk and rollback

CI adds a gate; it changes no runtime code. The one runtime-visible change is that
`src/static/vendor/` is now in the repository, so a clean checkout and the image serve htmx and
Leaflet instead of a 404. The ruleset is the change people will notice: nothing merges into `main`
without a green `CI gate`, administrators included. If CI itself breaks (a GitHub outage, a bad
action release), disable the ruleset under **Settings → Rules → Rulesets → main: CI gate**, merge the
fix, and enable it again; do not delete it. Rolling back the PR is a revert, followed by deleting the
ruleset, or every later PR waits for a check that no longer exists.

**Follow-ups:** the demo branch `Issue/9/demo-failing-test` is still on the remote (PR #121 is closed),
and deleting it needs your go-ahead. The v0.1.0 release note should say that a clean checkout lacked
`src/static/vendor/` until this PR (for the v0.2.0 note). Issue 13 adds the approval, no-direct-push
and CODEOWNERS rules beside this ruleset.

Closes #9
