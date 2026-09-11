# scripts/

Scripts for **BK ClinicQ**.

- **`run-mypy.sh`** – used by the pre-commit `mypy` hook; runs `.venv/bin/mypy` (falling back to `venv/`, then a bare `mypy` on `PATH` with a warning) so the type check always resolves the project's pinned Python 3.14 environment, even from a GUI git client or a shell where the venv was never activated.
- **`ci-local.sh`** – local pre-push/pre-tag gate: code quality (ruff, mypy), **pip-audit**, tests (pytest), Docker build, and **Trivy**. pip-audit, Trivy and the `--compose` smoke test are **local-only** (not on GitHub Actions); every other stage is mirrored by `.github/workflows/ci.yml` on each pull request (see [docs/CICD/PIPELINES.md](../docs/CICD/PIPELINES.md)). Use before pushing to **preserve your monthly Actions quota**. From repo root: `./scripts/ci-local.sh`. Options: `--install` (pip install deps first), `--fix` (ruff `--fix` and format in place), `--no-docker` (skip Docker build and Trivy), `--compose` (after Docker, bring the full compose stack up as its own `clinicq-smoke` project on separate ports, hit `/health/live`, check PostGIS and Redis, then `down -v`; your dev stack and its data are untouched).
- **`dev.sh`** – `make dev`: start PostgreSQL 18/PostGIS and Redis (`infra/docker/docker-compose.yml`), wait for both health checks, then run the API on the host with reload (`--port PORT`). Requires a `.env` in the repo root (`cp .env.example .env`).
- **`run-local.sh`** – start PostgreSQL/PostGIS and Redis the same way, wait for them, and print next steps (migrations, seed, run). Requires a `.env` in the repo root.
- **`run-local-platform.sh`** – join the local BK Platform platform: migrate the product schema on the shared DB `btk`, run app-only on `127.0.0.1:<app_port>`, register → **live**. Prerequisite: `cd ../infra && ./scripts/run-local-platform.sh`.
- **`check_config.py`** – `make check-config`: checks an env file against the settings without starting the app, listing every missing or invalid value in one pass (names only, never values). `--environment production` checks it as that environment. See [docs/CICD/ENVIRONMENTS.md](../docs/CICD/ENVIRONMENTS.md).
- **`generate_env_example.py`** – `make env-example`: writes `.env.example` from the `Settings` class (`--check` reports drift); the drift test runs it.
- **`ci_test_summary.py`** – used by CI's `report` job: renders the shards' pytest JUnit reports as the Markdown test summary on the run page and in the pull-request comment.
- **`db/deploy-sequence.sh`** – the deploy sequence (`alembic upgrade head`, the RBAC seed and its `--check`), defined once: CI runs it on the PostGIS service of every PR, and the deploy (Issue 11) runs it inside the new image before the swap.
- **`check_pr_conventions.py`** – CI's `conventions` job (Issue 13): the branch name, every commit's `Issue N: ` prefix, `Closes #N` (or `Refs #N`) and a screenshot for UI changes, read live from the pull request.
- **`gh_sync_rulesets.py`** – `make gh-sync-rulesets`: applies `.github/rulesets/*.json` to `main`, matched by name, idempotent (`--dry-run`).
- **`gh_sync_environments.py`** – `make gh-sync-environments`: applies `.github/environments/*.json` (required reviewers, the branches that may deploy); never touches secrets.
- **`cd/deploy.sh`** – runs on the deploy host (Issue 11): `preflight`, `run-new` (the migrations), `candidate` (smoke-tested on a side port), `swap` (live smoke, rolls back by itself), `rollback`, `status`. Called step by step by `.github/workflows/deploy.yml`; see [docs/CICD/RUNBOOK_DEPLOY.md](../docs/CICD/RUNBOOK_DEPLOY.md).
- **`cd/notify_deploy.py`** – posts a deploy's environment, version, result and release-notes link to the team channel (Slack or Discord webhook, `TEAM_WEBHOOK_URL`).
- **`gh_sync_docs.py`** – create **and update** GitHub milestones and issues from `docs/GITHUB/MILESTONES/*.md` and `docs/GITHUB/ISSUES/M*/ISSUE_*.md` (`--dry-run`, `--milestone M4`). Preferred over the create-only helper.
- **`gh_sync_issues.py`** – legacy create-only helper using the `gh` CLI (`--dry-run`, `--milestone M4`). See [docs/GITHUB/README.md](../docs/GITHUB/README.md).
- **`db/alembic-upgrade.sh`**, **`db/alembic-downgrade.sh`**, **`db/alembic-revision.sh`** – Alembic migration helpers (activate `.venv` automatically).
- **`db/seed-dev-user.sh`** – idempotent dev/admin user bootstrap in the configured PostgreSQL schema.
- **`cd/create-hetzner-server.sh`** – create a Hetzner Cloud server via API and print its public IP; requires `HETZNER_API_TOKEN` in env.
- **`cd/setup-server.sh`**, **`cd/server-initial-setup.sh`**, **`cd/server_setup.sh`** – provision a fresh Ubuntu/Debian host: system updates, Docker, base packages, and the Actions runner as a systemd service when `GITHUB_RUNNER_TOKEN` is set (see [docs/GITHUB/RUNNER/README.md](../docs/GITHUB/RUNNER/README.md)).
- **`cd/write-prod-env.sh`** – legacy helper (refuses unless `BTK_ALLOW_LEGACY_WRITE_PROD_ENV=1`). **Production CD** uses `/opt/btk/gateway/scripts/cd/write-prod-env.sh` via infra `cd-product.yml`.
- **`cd/free-http-ports-before-compose.sh`** – **retired** (exits 1). It would free host 80/443 and take down the gateway's edge nginx.
- **`cd/prune-old-app-images.sh`** – after a successful deploy, remove local GHCR app images whose semver tag is **strictly older** than the deployed tag; skips images still used by running containers. Production CD runs the gateway prune script instead.
- **`deploy/install-argocd.sh`**, **`install-fluxcd.sh`**, **`install-gitops.sh`** – optional GitOps installers; not used by the current gateway-based CD.
- Product TLS/certbot hooks were removed — the gateway owns HTTPS for the public domain.
- Add other one-off or dev scripts here.
