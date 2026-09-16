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
one deploy per environment at a time, never cancelled half-way. It copies
`docker-compose.prod.yml`, `scripts/cd/deploy.sh` and the settings (the Environment's `APP_ENV`
secret, written with mode 600) to the deploy directory, then calls `deploy.sh` over SSH, one step
at a time:

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

1. A Linux host with Docker and the compose plugin, `curl`, and a `deploy` user in the `docker` group
   (`scripts/cd/setup-server.sh` does the base install). The deploy directory: `/opt/btk/clinicq` for
   production, `/opt/btk/clinicq-staging` for staging, owned by `deploy`.
2. In the GitHub Environment (**Settings → Environments → staging / production**):

   | Kind | Name | Value |
   |------|------|-------|
   | secret | `DEPLOY_HOST` | the host's name or address |
   | secret | `DEPLOY_USER` | `deploy` |
   | secret | `DEPLOY_SSH_KEY` | a private key made for this deploy only (`ssh-keygen -t ed25519`); its public half in `~deploy/.ssh/authorized_keys` |
   | secret | `DEPLOY_KNOWN_HOSTS` | the host's key line, checked against the host itself (`ssh-keyscan <host>`, verified out of band) |
   | secret | `APP_ENV` | the whole `.env`: the settings (check it first: `python scripts/check_config.py <file> --environment production`), plus `APP_PORT`, `DOMAIN` and `GATEWAY_COMPOSE_DIR` for the compose file |
   | secret | `TEAM_WEBHOOK_URL` | optional: the Slack or Discord incoming webhook for deploy messages |
   | variable | `DEPLOY_DIR` | `/opt/btk/clinicq` or `/opt/btk/clinicq-staging` |
   | variable | `PUBLIC_URL` | the environment's URL, shown on the run |
   | variable | `TEAM_WEBHOOK_KIND` | `slack` (default) or `discord` |

Until those exist, a deploy reports "not provisioned", changes nothing and succeeds. The secrets
already in the `production` Environment (per-setting values from the project the kernel came from)
are not read by `deploy.yml`.

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
