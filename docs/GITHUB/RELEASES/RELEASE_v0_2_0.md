# Release v0.2.0: CI/CD, Environments & Team Workflow

**Date:** 2026-09-11 · **Milestone:** M2 · **Issues closed:** 9–14

A pre-release. It adds no clinic feature; it changes how every later feature reaches a server.
Nothing merges into `main` without a pull request whose CI gate is green. A version tag builds one
checked image, which a deploy can put on staging, and on production after one person approves.
The app refuses to start with an unsafe configuration, and names every problem at once. When
staging goes down, or an exception is raised, the team hears about it with what it needs to act.

## What shipped

- **CI on every pull request** (Issue 9, PR #120). `.github/workflows/ci.yml` runs the stages of
  `ci-local.sh`: ruff, mypy, gitleaks over the whole history, and pytest in three shards against
  real PostGIS 18 and Redis 8 containers (the PostgreSQL and Redis tests are required there, never
  skipped). Then one coverage floor over the shards, a PR comment, the image build when its inputs
  change, and one required check, **CI gate**. A superseded push cancels its run, and every job
  has a timeout. A typical run takes about 2 minutes and 9 job-minutes, about 890 a month against
  the free 2,000 (the repository is public, so it costs nothing today). Its first run found that
  `src/static/vendor/` (htmx, Leaflet) had never been committed; it is now.
- **One validation path for configuration** (Issue 12, PR #122). `Settings.configuration_problems()`
  is the one list of rules; the boot guard raises with all of them, and `scripts/check_config.py`
  (`make check-config`) prints the same list for any env file, without values. New rules refuse
  `DEBUG`, `CORS_ORIGINS=*` and contradictory `DB_*` parts outside development. `.env.example` is
  generated from the class (all 111 settings at this release), with a drift test. The matrix is in
  `docs/CICD/ENVIRONMENTS.md`.
- **Release images in GHCR** (Issue 10, PR #123). A `v*.*.*` tag builds once, checks the exact digest
  (a size ceiling of 1,000 MB, non-root, no toolchain, boots as production and reports its version
  and commit at `/health`), then publishes `:<version>`, `:latest` and `:<sha>` as one manifest,
  with signed SLSA provenance. The image went from 1,071 MB to 828 MB. A rollback is a deploy of
  an older tag, with no rebuild.
- **Team workflow** (Issue 13, PR #124). `CODEOWNERS` from `WORKLOAD_SPLIT.md` §2; the PR template
  GitHub pre-fills; feature, bug and spike issue forms; `labels.yml` synced (42 labels). Two rulesets
  kept as code (`make gh-sync-rulesets`): no direct push, force-push or deletion and a green CI gate
  for everyone, plus one code-owner approval that only an administrator may bypass, in a PR. A
  `conventions` job checks branch names, commit prefixes, `Closes #N` and screenshots for UI
  changes.
- **Deploys** (Issue 11, PR #126). `.github/workflows/deploy.yml` deploys staging after every
  successful release, and production only on a manual run the `production` Environment holds for
  the DevOps/QA Lead's approval (`make gh-sync-environments`). `scripts/cd/deploy.sh` runs the
  deploy on the host, a step at a time: the new image checks the settings, the migrations run
  before any traffic, a candidate is smoke-tested on a port nothing routes to, and the swap rolls
  back by itself on a failed smoke check. A rollback to the previous tag took 9.4 s
  (`docs/CICD/RUNBOOK_DEPLOY.md`). Each deploy posts its version, result and release notes link.
- **Monitoring** (Issue 14, PR #127). Gatus (`infra/monitoring/gatus.yaml`) checks `/health` on staging
  and production every minute and sends one alert per incident, naming the responsible role, and
  one on recovery (shown: one alert 122 s into an outage, and nothing more for its 6.5 minutes).
  Unhandled exceptions go to `SENTRY_DSN` (Sentry, or GlitchTip from
  `infra/docker/docker-compose.monitoring.yml`) tagged with the request id, the release and the
  commit, without cookies, bodies or frame locals. `/metrics` is opt-in, behind a token. The
  runbook is one page: `docs/CICD/RUNBOOK_ALERTS.md`.

## Migrations

- None. M2 adds no Alembic revision; the schema is still `0001_baseline` from v0.1.0. What changed is
  *when* migrations run: a deploy runs `scripts/db/deploy-sequence.sh` (upgrade, RBAC seed, its
  check) inside the new image before it serves traffic, and CI runs the same script on every pull
  request. From now on, a migration must work with the release before it (add, then use, then
  remove, in separate releases), because the previous release keeps serving on the new schema
  during a deploy, after a failed one and after a rollback.

## Upgrade notes

- **Everything reaches `main` through a pull request** with a green **CI gate** and one code-owner
  approval. `git push origin main` is rejected, for administrators too. Name branches
  `Issue/<N>/<slug>`, start commits `Issue <N>: `, and end the description with `Closes #<N>`: CI
  checks all three (CONTRIBUTING.md).
- **Reinstall:** `pip install -r requirements.txt` adds `sentry-sdk`.
- **Check your `.env`:** `make check-config` (or `… ENV_FILE=… ARGS='--environment staging'`). A
  staging or production file is now refused for `DEBUG=true`, `CORS_ORIGINS=*`, a `DB_HOST` or
  `DB_NAME` contradicting `DATABASE_URL`, `METRICS_ENABLED` without `METRICS_TOKEN`, and (production)
  `ERROR_TRACKING_TEST_ROUTE`. `DB_HOST` and `DB_NAME` without `DATABASE_URL` now build the URL; they
  used to be ignored for `localhost`.
- **`.env.example` changed shape:** every setting is listed, commented, with its default; only the
  two connection URLs are active. Copying it again is optional; `make env-example` regenerates it.
- **Never set `VERSION` or `GIT_SHA`** in an env file: the image sets them and `/health` reports them.
- **`make test-services`** runs the PostgreSQL and Redis tests against the compose stack
  (`TEST_REDIS_URL` is new).
- **`scripts/run-local-platform.sh` needs `DB_PASSWORD` in `.env`**: it no longer has a fallback.
- **If you ignore `vendor/` globally**, pull again: `src/static/vendor/` is now tracked, and a clean
  checkout of v0.1.0 lacked it (the shell served htmx as a 404).
- **Releasing:** tag `vX.Y.Z` on `main` (docs/CICD/RELEASE.md). Deploying production: **Actions →
  Deploy → Run workflow**, then approve (docs/CICD/RUNBOOK_DEPLOY.md).

## Known issues

- **A real credential is in the repository's history.** The shared local platform database's
  password was the fallback of `DB_PASSWORD` in `scripts/run-local-platform.sh` from the Issue 1
  scaffold (`5001133`) until Issue 12 removed it. The repository is public, and the value is also
  used in the maintainer's other env files. **It must be rotated** wherever it is used; until then,
  Issue 12's criterion "no secret value anywhere in the history" is not met.
- **Nothing is provisioned yet.** There is no staging or production host, no team channel webhook,
  no error-tracking DSN, and no machine running the uptime monitor. So a release's staging deploy
  reports "not provisioned" and changes nothing. The deploy, the outage alert and the error
  tracking were proven on a local stand-in (Docker Desktop, linux/amd64 under emulation, so real
  timings will be better). Hosts are Issue 102; the webhook, the DSN and the monitor's machine are
  for the team to create.
- **One person reviews everything for now.** `CODEOWNERS` routes every path to the DevOps/QA Lead
  until the team fills in `WORKLOAD_SPLIT.md` §1. The three M2 pull requests merged after the
  approval rule existed (#124, #126, #127) went through the administrator bypass, logged under
  Settings → Rules → Insights. The team walkthrough of an issue, and the team review of the
  environment matrix, have not happened.
- **Nine strict expected failures remain** in `PENDING_ON_LATER_ISSUES`: five wait for Issue 97's
  scheduled scan workflow (pip-audit and Trivy stay local-only until then), and four for
  `docs/SECURITY/` documents no issue creates yet. That is down from 23.
- **A swap or rollback is a short outage:** one app container, so about 5.6 s without the app while it
  is replaced. A deploy with no gap needs two containers behind the gateway (Issue 102).
- **Most tests use in-memory SQLite by design** (Issue 3): CI's database is PostGIS, and the 14
  PostgreSQL tests and 2 Redis tests are required there, but the ORM-level tests still run on
  SQLite.
- **The `production` GitHub Environment holds 93 secrets from the project the kernel came from.**
  `deploy.yml` reads none of them; delete or keep them deliberately.
- **Throwaway artefacts:** tags `v0.0.0-test` to `v0.0.5-test` and their GHCR images, and the demo
  branches `Issue/9/demo-failing-test` and `Issue/13/demo-conventions`, are still on GitHub.
