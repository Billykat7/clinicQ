# Contributing to ClinicQ

How the six of us work in one repository without breaking `main` for the other five. **Setting up
for the first time? Start with [the Quickstart](docs/QUICKSTART.md)**, which covers Windows, macOS
and Linux. The rules behind this page are in
[the engineering non-negotiables](docs/guideline.md); how to pick up an issue is in
[the issues guide](docs/GITHUB/ISSUES/README.md).

## Once per clone

```bash
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"          # pre-commit
brew install gitleaks trivy      # the secret scan (required) and the image scan (optional)
make hooks                       # install the git hooks from .pre-commit-config.yaml
cp .env.example .env
make db-up && make migrate-up    # PostgreSQL 18 + PostGIS and Redis, then the schema
make seed-rbac && make seed-dev-data   # the kernel's roles, then the ClinicQ demo world
```

On Linux, install gitleaks from its [releases page](https://github.com/gitleaks/gitleaks/releases)
(8.25 or newer). Without it, every commit fails the secret-scan hook: that is deliberate, since a
scan that quietly does not run protects nobody.

## The loop, from first edit to push

| When | Run | Takes | What it catches |
|------|-----|-------|-----------------|
| While coding | `make test-fast` (or one module's tests, below) | about 15 s | the tests that do not need a server |
| On `git commit` | the hooks, automatically | a few seconds | formatting, whitespace, large files, merge markers, private keys, **secrets** |
| **Before every push** | **`make check`** | under 3 min | everything CI runs: see below |
| No Docker running | `make check-fast` | about 45 s | the same, without the image build |

`make check` runs `scripts/ci-local.sh`, stage by stage, and stops at the first stage that fails,
naming it (`✗ ci-local.sh failed at stage: coverage`). Fix that stage and run it again. Push only
on green:

1. **quality**: `ruff check`, `ruff format --check`, `mypy src/`, and the ruff version pin
2. **pip-audit**: known CVEs in `requirements.txt` (a warning, and local-only: CI does not run it)
3. **secrets**: gitleaks over the whole git history
4. **tests**: the whole suite in parallel, coverage collected
5. **coverage**: at least the floor in `pyproject.toml` (see below)
6. **docker**: the production image builds, and Trivy scans it (Trivy warns only, and is local-only)

`make check-compose` adds the compose smoke test (up, `/health/live`, PostGIS, Redis, down).

The pull request then runs the same stages in GitHub Actions (`.github/workflows/ci.yml`), minus
pip-audit, Trivy and the compose smoke test, against real PostgreSQL + PostGIS and Redis
containers. Its one required check, **CI gate**, must be green to merge. What each job does, why a
docs-only or draft pull request skips the tests, and what it all costs in Actions minutes:
[`docs/CICD/PIPELINES.md`](docs/CICD/PIPELINES.md).

Never push with `--no-verify`, and never commit with it. If a hook is wrong, fix the hook in its own
PR.

## Running some of the tests

```bash
pytest tests/unit/commons                             # one area
pytest tests/integration/platform/test_health.py      # one file
pytest tests/integration/platform/test_health.py -k ready   # tests whose name matches
pytest -m unit                                        # all unit tests (under 10 s)
pytest -m "not slow"                                  # make test-fast
pytest -x --lf                                        # stop at the first failure; rerun last failures
```

Build test data with the factories in `tests/factories.py` rather than by hand: one call, no
arguments, any field overridden (`StaffFactory.create(db, role=UserRole.CLINIC_MANAGER)`,
`SiteFactory.create(db, sector=SiteSector.PRIVATE)`,
`TicketFactory.build_batch(5, queue=QueueFactory.build())`).

Markers are applied by location in `tests/conftest.py`, so you rarely write one yourself:

| Marker | Means | Applied |
|--------|-------|---------|
| `unit` | essential logic, no server | everything under `tests/unit/` |
| `integration` | HTTP, the database, or several modules together | everything else |
| `slow` | seconds rather than milliseconds | PostgreSQL tests, and timing tests mark themselves |
| `postgres` | needs a real PostgreSQL + PostGIS server | written on the test module (`pytestmark`) |
| `redis` | needs a real Redis server | written on the test module (`pytestmark`) |

An unknown marker is an error (`--strict-markers`), so a typo cannot silently select nothing.

### The database tests

Tests marked `postgres` each get a throwaway database, created and dropped on the server named by
`TEST_DATABASE_URL`. With the stack up (`make db-up`):

```bash
make test-postgres                                    # the compose server on localhost:5432
make test-postgres DB_PORT=5433                       # if you moved it
```

Without `TEST_DATABASE_URL`, `make test` and `make check` skip them and say why. The URL is never
guessed: whatever answers on port 5432 on your laptop may be another project's database.

Tests marked `redis` follow the same rule with `TEST_REDIS_URL`. `make test-services` runs both
kinds against the compose stack, with a missing server a failure rather than a skip, which is
exactly what CI's service containers do:

```bash
make test-services                                    # localhost:5432 and localhost:6379
make test-services DB_PORT=5433 REDIS_PORT=6380       # if you moved them
```

## Coverage

At least **75% of the lines in `src/`** must run under the suite, set in `pyproject.toml`
(`[tool.coverage.report] fail_under`) and enforced by the coverage stage of `make check`. It was
76% when the team agreed the floor. The report lists every file that is not fully covered, with the
missing lines. To see it for your own change:

```bash
pytest --cov --cov-report=term-missing tests/unit/commons
```

New code comes with its tests. The floor goes up as ClinicQ's modules land, and it is never lowered
to get a change through: that is a team decision in its own issue.

## If a commit is blocked for a secret

1. **A real credential:** rotate it now, at the provider, then remove it from the file. It has not
   reached `main`, but assume anything that touched your disk may have been copied; rotation is the
   only fix that holds.
2. **Not a secret** (a test fixture, a documented placeholder): say so in review, and if it cannot
   be written differently, add it to `.gitleaks-baseline.json` after the commit lands:

   ```bash
   gitleaks git --config .gitleaks.toml --redact --report-format json \
     --report-path .gitleaks-baseline.json .
   ```

   Review the diff of that file: it should gain exactly your finding, redacted.

`.gitleaks.toml` narrows only by the *shape of a value* (a time-zone name, the compose default
password). Never add a `paths` entry: gitleaks would then skip every rule on those files, real AWS
keys included, and `tests/unit/platform/test_scanning_config.py` fails if one appears.

## Tests that wait for a later issue

Some kernel guard tests check files that later issues create: the deploy and scan workflows
(Issues 11 and 97) and the security documents. They are listed in `PENDING_ON_LATER_ISSUES` in `tests/conftest.py` as
strict expected failures. When your issue creates the file, the test starts passing, and strict
xfail reports that as a failure: delete its entry in the same PR.

## Adding a setting

Settings live in `src/core/config.py`. Give the new `Field` a description and a default that is safe
on a laptop, then run `make env-example` and commit `.env.example` with it:
`tests/unit/platform/test_env_example.py` fails while the two disagree. A value staging or
production must never run with gets a rule in `Settings.configuration_problems()`, the one place
the boot guard and `scripts/check_config.py` both read ([`docs/CICD/ENVIRONMENTS.md`](docs/CICD/ENVIRONMENTS.md)).

## Branches, commits and pull requests

- Branch `Issue/<N>/<short-slug>` (two to four lower-case words); every commit message starts
  `Issue <N>: ` and says what it does. A release note's branch is `Release/v<X.Y.Z>`, its commits
  `Release v<X.Y.Z>: `.
- One issue, one pull request, merged within three days of starting. Rebase onto `main` daily.
- The PR description lives in `docs/GITHUB/PR/M<milestone>/PR_<N>_DESCRIPTION.md`, follows
  [the template](docs/GITHUB/PR/PR_TEMPLATE.md) (the same one GitHub pre-fills), ends with
  `Closes #<N>`, and proves each acceptance criterion by showing it working. A change to a
  template, stylesheet or script under `src/` shows a screenshot. A follow-up to an issue an
  earlier PR closed says `Refs #<N>` instead.

- **The same pull request updates the docs and the milestone, as they will read once it merges.**
  Run `make milestone-progress ARGS='--assume-closed <N>'`. It reads the issue states from GitHub,
  counts the issue this pull request closes as closed, and writes the green progress bar and
  percentage into the milestone document, the README's delivery table and the milestone summary in
  `docs/GITHUB/README.md`. A **stacked** pull request names every issue its branch closes,
  including those of the branches below it (`--assume-closed 32,35,36`). A follow-up that closes
  nothing runs it with no assumption. `make milestone-progress-check ARGS=...` with the same flag says
  whether they are stale. The hand-written issue count in the README's Status block must match the
  generated total. When the pull request closes a milestone's **last** issue, it also ticks the exit
  criteria (leaving anything met only in part unticked, with the reason beside it), marks the
  milestone done and closes it on GitHub with
  `make milestone-progress ARGS='--close-completed'`. Editors that read
  `.cursor/rules/` pick the same rule up from `milestone-progress.mdc`.

CI checks all four: the branch name, every commit's prefix, the closing line and the screenshot
(`scripts/check_pr_conventions.py`). A slip fails the **CI gate** without stopping the tests; fix the
description (or the branch) and re-run the failed job.

## What `main` accepts

Two rulesets protect `main` ([`.github/rulesets/`](.github/rulesets/README.md), applied with
`make gh-sync-rulesets`):

- **Everyone, administrators included:** no direct push, no force-push, no deletion; changes arrive
  through a pull request whose **CI gate** check is green.
- **One approval from the code owner** of the paths the pull request changes. A new push dismisses
  an approval given before it.

## Reviews

GitHub requests a review from the owner of every path you change: `.github/CODEOWNERS`, which
encodes the ownership map in [`docs/TEAM/WORKLOAD_SPLIT.md`](docs/TEAM/WORKLOAD_SPLIT.md) §2 with
one owner per path. Review within 24 hours on a weekday; after that, the owner's backup (§1) may
approve instead. Approve only what you have read and could explain.

Until the team fills in the handles in §1, every path routes to the DevOps/QA Lead, who reassigns
the review. The code owner cannot approve their own pull request, so a repository administrator
may merge without an approval, through the pull request only (the ruleset never lets anyone push
past it). Use that for your own PRs only when no other code owner exists for the paths, never to
skip a review someone else owes you; each bypass is logged under **Settings → Rules → Insights**.

## Issues

New issues open from a form: **Feature**, **Bug** or **Spike** (`.github/ISSUE_TEMPLATE/`), each
asking the questions that make it reviewable without a conversation, with its type label applied.
Planned milestone work is not opened by hand: it is written as a spec in `docs/GITHUB/ISSUES/` and
synced with `make gh-sync`. Labels come from `docs/GITHUB/LABELS/labels.yml` only
(`make gh-sync-labels`); a label made in the GitHub UI is removed by the next `--prune`.
