# Runbook: deploying, and rolling back

How a released image reaches staging and production, what to do when a deploy fails, and how to
roll back in under a minute. Images come from [`RELEASE.md`](RELEASE.md); settings from
[`ENVIRONMENTS.md`](ENVIRONMENTS.md).

## In one minute

| I want to | Do |
|-----------|----|
| Deploy to staging | **Actions → Deploy → Run workflow**: `staging`, the version (e.g. `0.2.0`). Nothing to approve; it starts at once. |
| Deploy to production | **Actions → Deploy → Run workflow**: `production`, the version (e.g. `0.2.0`). The DevOps/QA Lead approves it under the run's *Review deployments*. |
| Roll back **now** | On the host: `cd "$DEPLOY_DIR" && DEPLOY_ENV=production ./deploy.sh rollback`. **Measured: 9.4 s** (below). |
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

The runner is the host, so the only thing the deploy has to be told is **which directory on it**
holds each environment: `DEPLOY_DIR`. The app's settings are the `.env` already in that directory.
This repository is public, so it names no path on anybody's server — `DEPLOY_DIR` is where the
layout is written down, and it lives in the GitHub Environment, not in git. The deploy masks the
value in its own log before it uses it, and never puts it in a step output.

1. **The runner.** A self-hosted GitHub Actions runner on the host, registered against this
   repository with the labels `self-hosted`, `Linux`, `X64` and **`clinicq`**. `clinicq` is what
   pins the deploy to the machine ClinicQ lives on; add `infra` too if it is the shared platform
   runner. Its user needs Docker (the `docker` group) and write access to the deploy directory.
2. **The deploy directory.** One per environment. The deploy writes its `.env` ([Secrets](#secrets)); on a host still keeping its own, check it first (
   `python scripts/check_config.py <file> --environment production`, and it needs `APP_PORT`,
   `DOMAIN` and `GATEWAY_COMPOSE_DIR` for the compose file). `scripts/cd/setup-server.sh` does the
   base install; `btk-platform-layout.sh` in `Billykat7/infra` creates the layout.
3. **In the GitHub Environment** (**Settings → Environments → staging / production**):

   | Kind | Name | Required | What it is |
   |------|------|----------|------------|
   | **secret** | `DEPLOY_DIR` | **yes** | the absolute directory on the runner that holds this environment. Without it the deploy reports "not provisioned", changes nothing and succeeds. A *variable* also works and the deploy masks the value either way (`::add-mask::`), but a secret is masked by GitHub itself — and this repository is public, so its Actions logs are too. |
   | variable | `SECRETS_DIR` | no | where `Billykat7/infra` is checked out on this runner, if not `/etc/btk/secrets` ([Secrets](#secrets)) |
   | secret | `SOPS_AGE_KEY` | no | the age identity, **only** for a host that cannot hold its own. Prefer the key on the host: see [Secrets](#secrets). |
   | secret | `TEAM_WEBHOOK_URL` | no | to post the result to Slack or Discord |
   | variable | `PUBLIC_URL` | no | the environment's URL, shown on the run |
   | variable | `TEAM_WEBHOOK_KIND` | no | `slack` (default) or `discord` |

   Repository-wide, `DEPLOY_RUNNER_LABELS` (a JSON array, e.g. `["self-hosted","Linux","X64","clinicq"]`)
   moves the deploy to a different runner without editing the workflow.

If `DEPLOY_DIR` is unset, or names a directory that does not exist, the deploy reports "not
provisioned", says exactly what to set or create, changes nothing and succeeds.

> **Deploying over SSH is gone (Issue 230).** `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` and
> `DEPLOY_KNOWN_HOSTS` are no longer read by `deploy.yml` and can be deleted from both Environments.
> A run that reported *"Not provisioned (missing DEPLOY_HOST DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS
> APP_ENV DEPLOY_DIR): nothing was deployed."* was asking for credentials to reach a host the job
> was never going to be on.

Staging and production can share one host: `DEPLOY_ENV` makes them separate compose projects
(`btk-clinicq-staging` and `btk-clinicq`) on different `APP_PORT`s.

## Secrets

The deployed `.env` is composed on the host, on every deploy, from two halves:

```
deploy/env/<environment>.env          in this repository, reviewed, no credential ever
+ clinicq/<environment>.env           in Billykat7/infra (private), SOPS-encrypted with age
= $DEPLOY_DIR/.env                    mode 600, written by scripts/cd/compose-env.sh
```

**Why not GitHub secrets.** They are write-only — you cannot read one back to see what is in it,
diff it, or tell when it changed. There is no audit of reads and no rotation story. And the whole
`.env` as one `APP_ENV` secret was pressing against GitHub's limits: 48 KB per secret, 64 KB for
all of a repository's secrets together, 100 secrets. `APP_ENV` has been removed.

**Why not encrypted files in this repository.** It is public. Committing ciphertext here would
publish it permanently, so a future compromise of the age key would read every value this project
has ever held, retroactively. In a private repository, the ciphertext is a second lock rather than
the only one.

### Setting the host up (once)

1. Install [`sops`](https://github.com/getsops/sops) and [`age`](https://github.com/FiloSottile/age).
2. Generate the host's identity and keep the **private** half on the host only:
   ```bash
   sudo install -d -m 700 /etc/btk/secrets
   sudo sh -c 'age-keygen -o /etc/btk/secrets/age.key && chmod 600 /etc/btk/secrets/age.key'
   sudo grep "public key:" /etc/btk/secrets/age.key   # the recipient; safe to share
   ```
3. Add that public key to `.sops.yaml` in `Billykat7/infra` and re-encrypt, so the host can read
   the files. Check the repository out at `/etc/btk/secrets` (or anywhere, and set the
   `SECRETS_DIR` variable on the GitHub Environment to it).
4. Make both readable by the runner's user and nobody else.

The age *public* key is not a secret — it only encrypts. The private key never leaves the host, so
a compromise of this repository's GitHub Environments does not reach production's secrets.

`SOPS_AGE_KEY` (an Environment secret holding the identity itself) is the alternative for a host
that cannot keep its own. It works, and `compose-env.sh` writes it to a private temporary file and
removes it — but it puts the key back into GitHub, which is the thing this arrangement avoids.

### Adding or rotating a secret

```bash
cd <Billykat7/infra checkout>
sops clinicq/production.env          # opens decrypted in $EDITOR, re-encrypts on save
git commit -am "clinicq: rotate JWT_SECRET" && git push
```

Then deploy. The next run composes the new value; there is no box in a web UI to remember. `git
log -p` on that file shows when a value last changed (the ciphertext changes, not the value), and
rolling one back is `git revert`.

**A new setting that holds a credential** belongs here, not in `deploy/env/`. If you put it in the
committed half, `tests/unit/platform/test_deploy_env.py` fails by name before it can be pushed.

### When it goes wrong

| Symptom in the run log | Cause |
|---|---|
| `no secrets file for <environment>` | `Billykat7/infra` is not checked out at `SECRETS_DIR` on this runner, or `SECRETS_DIR` points elsewhere. |
| `no age identity to decrypt` | `/etc/btk/secrets/age.key` is missing or the runner's user cannot read it. |
| `sops is not installed on this host` | step 1 above. |
| `Secrets not composed` **warning**, deploy continues | none of the above worked, so the `.env` already on the host was used. The deploy is fine; fix the cause before the next one. |

A failure here never leaves a half-written `.env`: `compose-env.sh` builds the file in a temporary
directory and replaces the live one only once it is whole.

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
cd "$DEPLOY_DIR" && export DEPLOY_ENV=staging       # the staging Environment's DEPLOY_DIR
./deploy.sh status                                   # what serves, what a rollback restores
./deploy.sh rollback                                 # back one release, smoke-checked
./deploy.sh smoke 8012 ghcr.io/billykat7/clinicq:0.2.0   # the smoke check alone, against a port
tail -n 50 .deploy/history.log                       # every step, timestamped
docker compose -f docker-compose.prod.yml --project-directory . -p btk-clinicq-staging logs --tail 100 app
```
