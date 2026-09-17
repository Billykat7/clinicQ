# Runbook: deploying, and rolling back

How a released image reaches staging and production, what to do when a deploy fails, and how to
roll back in under a minute. Images come from [`RELEASE.md`](RELEASE.md); settings from
[`ENVIRONMENTS.md`](ENVIRONMENTS.md).

## In one minute

| I want to | Do |
|-----------|----|
| Deploy to staging | **Actions → Deploy → Run workflow**: `staging`, the version (e.g. `0.2.0`). Nothing to approve; it starts at once. |
| Deploy to production | **Actions → Deploy → Run workflow**: `production`, the version (e.g. `0.2.0`). The DevOps/QA Lead approves it under the run's *Review deployments*. |
| Roll back **now** | On the host: `cd /opt/btk/clinicq && DEPLOY_ENV=production ./deploy.sh rollback`. **Measured: 9.4 s** (below). |
| Roll back to a chosen version | **Run workflow**: the environment, that version, tick **rollback** (it skips the migrations). |
| See what runs | `curl https://<host>/health` (version and commit), or `./deploy.sh status` on the host. |

## What a deploy does

**Every deploy is a manual run.** Pushing a tag publishes an image ([`RELEASE.md`](RELEASE.md)) and
stops there; a person then decides when that version reaches staging or production. Shipping a
release is two steps, not one — push the tag, then run Deploy with that version — and nothing on a
host changes unless somebody asked for it.

[`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml) runs one job for one environment,
one deploy per environment at a time, never cancelled half-way. **It runs on the host.** The BTK
platform server hosts ClinicQ's own GitHub Actions runner (labels `self-hosted, Linux, X64, clinicq,
infra`), so the job is already on the machine it is deploying to: there is nothing to SSH into, no
key to hold and no host address to configure. It copies `docker-compose.prod.yml` and
`scripts/cd/deploy.sh` into the deploy directory and calls `deploy.sh` there, one step at a time:

| Step | `deploy.sh` | What it proves | If it fails |
|------|-------------|----------------|-------------|
| 1. Pre-flight | `preflight <image> <env>` | the image pulls, and the host's `.env` passes **the new image's** settings check (`check_config.py`) | nothing has changed |
| 2. Migrations | `run-new <image> ./scripts/db/deploy-sequence.sh` | `alembic upgrade head`, the RBAC seed and `--check`, in the new image, **before it serves anything** | the old version keeps serving; a failed migration rolls back its transaction |
| 3. Candidate | `candidate <image>` | the new image, on a side port nothing routes to, answers the smoke check | the candidate is removed; **the old version never stopped serving** |
| 4. Swap | `swap <image>` | the new image replaces the serving container and passes the smoke check live | **it rolls back by itself** to the image it replaced |
| 5. Team channel | `notify_deploy.py` | a message with the environment, version, commit, result and release notes link | nothing (a chat outage never fails a deploy) |

**The smoke check**: `/health` reports the image's own version and commit (read from its labels,
so a stale container cannot pass as the new one), `/health/ready` is 200 (database, migrations at
head, Redis), and the landing page and its htmx answer 200.

A rollback (`rollback` ticked) skips step 2: migrations only move forward.

### Migrations must not break the previous release

Steps 2–4 mean the previous release keeps running against the *new* schema: while the candidate is
checked, after a failed deploy, and after a rollback. So a migration must work with the code before
it: **add, then use, then remove, in separate releases**. Add a nullable column or a new table now,
and drop the old one in a later release, once nothing reads it. A migration that cannot be done that
way needs a maintenance window, agreed in its pull request.

## Approvals

Production waits for the **DevOps/QA Lead** (the `production` Environment's required reviewer,
[`.github/environments/production.json`](../../.github/environments/production.json)): the run shows
*Waiting for review*, and nothing, not even the pre-flight, runs until someone approves it. Staging
needs no approval. Both deploy only from workflows running on `main`, so a workflow edited on a
branch can never reach an environment's secrets. `make gh-sync-environments` applies those settings.

## Setting up a host (once per environment)

**On a host laid out by the platform, there is nothing to configure.** The runner is the host, the
deploy directory defaults to `/opt/btk/clinicq` (`/opt/btk/clinicq-staging` for staging), and the
app's settings are the `.env` already in it.

1. **The runner.** A self-hosted GitHub Actions runner on the host, registered against this
   repository with the labels `self-hosted`, `Linux`, `X64` and **`clinicq`**. `clinicq` is what
   pins the deploy to the machine ClinicQ lives on; add `infra` too if it is the shared platform
   runner. Its user needs Docker (the `docker` group) and write access to the deploy directory.
2. **The deploy directory.** `/opt/btk/clinicq` for production, `/opt/btk/clinicq-staging` for
   staging, holding the app's `.env` (check it first:
   `python scripts/check_config.py <file> --environment production`, and it needs `APP_PORT`,
   `DOMAIN` and `GATEWAY_COMPOSE_DIR` for the compose file). `scripts/cd/setup-server.sh` does the
   base install; `btk-platform-layout.sh` in `Billykat7/infra` creates the layout.
3. **Optional**, in the GitHub Environment (**Settings → Environments → staging / production**):

   | Kind | Name | When you need it |
   |------|------|------------------|
   | secret | `APP_ENV` | to keep the settings in GitHub instead of on the host: the whole `.env`, rewritten (mode 600) on every deploy. Without it the host's own `.env` is used and never touched. |
   | secret | `TEAM_WEBHOOK_URL` | to post the result to Slack or Discord |
   | variable | `DEPLOY_DIR` | a deploy directory that is not `/opt/btk/clinicq[-staging]` |
   | variable | `PUBLIC_URL` | the environment's URL, shown on the run |
   | variable | `TEAM_WEBHOOK_KIND` | `slack` (default) or `discord` |

   Repository-wide, `DEPLOY_RUNNER_LABELS` (a JSON array, e.g. `["self-hosted","Linux","X64","clinicq"]`)
   moves the deploy to a different runner without editing the workflow.

If the deploy directory does not exist, the deploy reports "not provisioned", says exactly what to
create, changes nothing and succeeds.

> **Deploying over SSH is gone (Issue 230).** `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` and
> `DEPLOY_KNOWN_HOSTS` are no longer read by `deploy.yml` and can be deleted from both Environments.
> A run that reported *"Not provisioned (missing DEPLOY_HOST DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS
> APP_ENV DEPLOY_DIR): nothing was deployed."* was asking for credentials to reach a host the job
> was never going to be on.

Staging and production can share one host: `DEPLOY_ENV` makes them separate compose projects
(`btk-clinicq-staging` and `btk-clinicq`) on different `APP_PORT`s.

## When a deploy fails

1. Open the run: the failed step names what broke, and `deploy.sh` has logged every step to
   `.deploy/history.log` on the host.
2. **Nothing to undo** for a failed pre-flight, migration or candidate: the previous version is
   still serving. A failed swap has already rolled back; check with `./deploy.sh status`.
3. Fix forward: a new commit, a new tag. Re-running a failed deploy re-deploys the same image.

## Rollback

**On the host, fastest:** `./deploy.sh rollback` puts back the image the last swap replaced (named in
`.deploy/previous`), waits until it is healthy and smoke-checks it. No build, no pull if the image
is still there (a deploy keeps it), no migrations. One step back only: after a rollback, going
further back means choosing a version.

**From GitHub, for a chosen version:** **Run workflow** with that version and **rollback** ticked. It
runs every step but the migrations, so the chosen version is still candidate-checked first.

### The timed rollback

Performed on 2026-09-11 for Issue 11, on a local stand-in for the staging host (Docker Desktop on an
Apple Silicon Mac, running the linux/amd64 images under emulation, so a real host is faster).
Staging was serving `0.0.3-test`; the previous tag was `0.0.2-test`.

| Measured | Time |
|----------|-----:|
| `./deploy.sh rollback`, from the command to "serves again" (smoke check passed) | **9.4 s** |
| The live port not answering, probed every 0.5 s (the container swap) | 5.6 s |
| A full deploy for comparison (pre-flight, migrations, candidate, swap) | 35–37 s |

The target is under 10 minutes; the measured rollback is under 10 seconds. Measure it again on the
first real host and add a row here with the date.

**Known limit:** one app container, so a swap or rollback is a few seconds without the app (5.6 s
above). A deploy with no gap needs two containers behind the gateway, which is production
infrastructure work (Issue 102).

## Commands on the host

```bash
cd /opt/btk/clinicq-staging && export DEPLOY_ENV=staging
./deploy.sh status                                   # what serves, what a rollback restores
./deploy.sh rollback                                 # back one release, smoke-checked
./deploy.sh smoke 8012 ghcr.io/billykat7/clinicq:0.2.0   # the smoke check alone, against a port
tail -n 50 .deploy/history.log                       # every step, timestamped
docker compose -f docker-compose.prod.yml --project-directory . -p btk-clinicq-staging logs --tail 100 app
```
