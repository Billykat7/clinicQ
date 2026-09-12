# PR: Deploy released images to staging on a release and to production on approval (Issue 11 / M2-11)

**Milestone:** [Milestone 2: CI/CD, Environments & Team Workflow](https://github.com/Billykat7/clinicQ/milestone/2) ·
**Issue:** [#11](https://github.com/Billykat7/clinicQ/issues/11)

A released image now has a way onto a server that cannot leave the server with nothing working.
`.github/workflows/deploy.yml` deploys staging after every successful release, and production only
on a manual run that waits for the DevOps/QA Lead's approval. The work happens on the host, in
`scripts/cd/deploy.sh`, as separate visible steps. The new image checks the host's settings by its
own rules. Migrations run as their own step before the image serves anything. The image is then
smoke-tested on a side port that nothing routes to, and only then swapped in, with a live smoke
check that rolls back by itself. No staging host exists yet, so every step was run for real
against a local stand-in: two releases deployed, a broken build refused while the old version kept
serving, a live failure rolled back automatically, and a rollback to the previous tag took 9.4 seconds.

## Summary

- **`deploy.yml`.** Triggers: `workflow_run` on a successful Release from a tag push (staging), and
  `workflow_dispatch` (environment, version, rollback). One job per run, in the GitHub Environment
  being deployed, with one deploy per environment at a time, never cancelled. An environment
  without its secrets reports "not provisioned", changes nothing and succeeds.
- **`scripts/cd/deploy.sh`,** on the host: `preflight`, `run-new` (the migrations), `candidate`, `swap`,
  `rollback`, `smoke`, `status`. It logs every step to `.deploy/history.log` and records `current`
  and `previous`. The smoke check requires `/health` to report the image's *own* version and commit
  (from its labels), `/health/ready` to answer 200, and the landing page and its htmx to load.
- **Production behind approval.** `.github/environments/{staging,production}.json`, applied by `make
  gh-sync-environments`: production requires the DevOps/QA Lead (@Billykat7), and both deploy only
  from workflows running on `main`. Applied; the existing `PRODUCTION` environment's 93 secrets
  are untouched.
- **The one deploy sequence.** The migrate step runs `./scripts/db/deploy-sequence.sh` inside the new
  image: the script CI runs on every pull request (Issue 9). Issue 9's guard,
  `test_the_deploy_sequence_is_identical_in_both_workflows`, now passes instead of waiting.
- **The team channel** gets one message per deploy: environment, version, commit, result, the release
  notes link and the run (`scripts/cd/notify_deploy.py`, Slack or Discord webhook).
- **The compose file and image:** the `migrate` service runs the deploy sequence (its old command
  called a script the image did not contain until Issue 10). The health check probes every 2 s
  while starting, so a swap's `--wait` returns in seconds. The image carries
  `scripts/check_config.py`, which now also reads a settings file from stdin.
- **`docs/CICD/RUNBOOK_DEPLOY.md`:** the steps, approvals, host setup, failures, rollback and the timing.

## Design notes

**Test the new image before it takes traffic, not after.** "A failed smoke check leaves the
previous version running" is only true if the check runs before the swap. So the candidate step
starts the new image with the host's real settings on `127.0.0.1:<APP_PORT+1000>`, which the
gateway never routes to, smoke-checks it and removes it. Only an image that passed is swapped in,
and the live check after the swap is a second line: if it fails, `swap` restores the image it
replaced, by itself.

**Migrations first, and forward-compatible.** The migrate step runs before the candidate, so the
previous release keeps serving on the new schema (during the check, after a failed deploy, after
a rollback). The runbook states the rule that makes this safe (add, then use, then remove, in
separate releases), and a rollback skips migrations entirely.

**Staging on a release, production by hand.** Chaining production onto the release run would hold the
release's concurrency slot for as long as an approval waits, blocking the next tag. So a
successful release triggers a staging deploy (`workflow_run`), and production is a manual run that
the Environment holds for approval, in its own concurrency group.

**The host's settings come from GitHub, whole.** `APP_ENV`, one Environment secret holding the entire
`.env`, is written to the host with `umask 077` on every deploy. Rotating a secret therefore means
editing it in GitHub and redeploying. The pre-flight then runs the *new* image's
`check_config.py` over that file on stdin (it is mode 600, and the container does not run as its
owner), so a setting the new release refuses stops the deploy before anything changes.

**Only from `main`.** Both environments allow deploys only from workflows running on `main`. A
`deploy.yml` changed on a branch can never reach an environment's secrets, whatever it says.

**Not provisioned is not a failure.** Until a host and its secrets exist, a deploy succeeds with a
notice and a summary line, so releases stay green while the infrastructure (Issue 102) is pending.

**Out of scope:** provisioning the hosts (Issue 102), monitoring (Issue 14), and a swap with no gap at
all, which needs two app containers behind the gateway.

## Changes

- **`.github/workflows/deploy.yml`**, **`scripts/cd/deploy.sh`**, **`scripts/cd/notify_deploy.py`**,
  **`scripts/cd/__init__.py`** (new).
- **`.github/environments/`** (`staging.json`, `production.json`, `README.md`) and
  **`scripts/gh_sync_environments.py`** (new); **`Makefile`:** `make gh-sync-environments`.
- **`infra/docker/docker-compose.prod.yml`:** header, the `migrate` command, `start_interval: 2s`.
- **`infra/docker/Dockerfile`**, **`.dockerignore`:** `scripts/check_config.py` in the image.
- **`scripts/check_config.py`:** `-` reads stdin.
- **`tests/unit/platform/test_workflow_guardrails.py`:** `deploy.yml` leaves `NOT_YET_CREATED`, and
  three guards: only staging deploys by itself, and only after a successful Release; migrations
  run before the candidate and the swap, and never on a rollback; `APP_ENV` is written with
  `umask 077` and never echoed. **`tests/conftest.py`:** the two deploy guards leave
  `PENDING_ON_LATER_ISSUES` (they pass).
- **`tests/unit/platform/test_notify_deploy.py`** (new): the message, the release-notes link, each
  service's body, a real post to a local stand-in, and a dead channel not failing the deploy.
  **`tests/unit/security/test_config_guards.py`:** `check_config` on stdin.
- **`docs/CICD/RUNBOOK_DEPLOY.md`** (new); **`docs/CICD/PIPELINES.md`**, **`scripts/README.md`**.

## Testing

Every host step below ran for real, with this PR's `deploy.sh` (its two state fixes were made during
the run and re-run, below), on a local stand-in for the staging
host: a directory laid out as the workflow leaves `/opt/btk/clinicq-staging` (the compose file, the
scripts and an `ENVIRONMENT=staging` `.env` with mode 600), its own database on the local PostGIS,
Redis database 1, and a self-signed certificate where the gateway's would be. The images are the
releases `v0.0.2-test` (`f17c129`) and `v0.0.3-test` (`a3be05c`) this branch published, run as
linux/amd64 under emulation on an Apple Silicon Mac (`DOCKER_DEFAULT_PLATFORM=linux/amd64`), so a
real host is faster.

- [x] **A first deploy, step by step** (35 s in all):

      ```text
      preflight: checking .env as staging with the new image's rules
      OK: the app would start with this file (1 warning(s)).                     ⏱ 6 s
      run-new: ./scripts/db/deploy-sequence.sh in …:0.0.2-test
      Running upgrade  -> 0001 · rbac seed: … permissions +107 … · rbac seed --check: in sync   ⏱ 12 s
      candidate: starting …:0.0.2-test on 127.0.0.1:19090, which nothing routes to
      smoke: :19090 serves 0.0.2-test (f17c129); /health, /health/ready, / and /static/vendor/htmx-2.0.0.min.js answer 200
      candidate: passed; it never served traffic                                  ⏱ 9 s
      swap: (nothing) -> …:0.0.2-test
      smoke: :18090 serves 0.0.2-test (f17c129); …
      deployed: …:0.0.2-test                                                      ⏱ 8 s
      ```

- [x] **The next release over it** (37 s): the migrations were a no-op (`rbac seed: roles +0 …`), and
      `swap: …:0.0.2-test -> …:0.0.3-test`; `status` then read `previous: …:0.0.2-test`.
- [x] **A failed smoke check leaves the previous version serving.** A build that cannot start (a local
      image from `0.0.3-test` with a crashing command, never pushed), deployed with a 30 s smoke
      timeout. The live port, probed every 2 s throughout, answered 200 with `0.0.3-test` all 25
      times:

      ```text
      candidate: starting clinicq-broken:0.0.4 on 127.0.0.1:19090, which nothing routes to
      smoke: :19090 never reported version 0.0.4-broken (000…) at /health
      a build that cannot start
      candidate: failed; ghcr.io/billykat7/clinicq:0.0.3-test keeps serving, untouched      exit 1
      ```

      With the candidate step skipped on purpose, the same image failed **live** and the swap rolled
      itself back: `swap: clinicq-broken:0.0.4 failed live` → `rollback: …:0.0.3-test serves again`
      (8 s later), exit 1, with `previous` still `0.0.2-test`.
- [x] **The rollback to the previous tag, timed**: `./deploy.sh rollback` with `0.0.3-test` serving
      and `0.0.2-test` recorded as previous.

      ```text
      rollback: restoring ghcr.io/billykat7/clinicq:0.0.2-test
      smoke: :18090 serves 0.0.2-test (f17c129); …
      rollback command, start to smoke-checked: 9.4 s
      live port not answering 200: 5.6 s (probed every 0.5 s)
      versions the live port reported, in order: 0.0.3-test → (none) → 0.0.2-test
      ```

      Recorded in `docs/CICD/RUNBOOK_DEPLOY.md`, with the conditions.
- [x] **Two bugs this run found and fixed:** a rollback left `previous` equal to `current`, and a failed
      swap's automatic rollback forgot the version before the one still serving. Both are fixed and
      re-run; the second's re-run is the "previous still `0.0.2-test`" above.
- [x] **The team-channel message**, posted by `notify_deploy.py` to a local stand-in webhook (HTTP 204):

      ```text
      ✅ deployed: ClinicQ 0.2.0 (a3be05c) → staging
      Release notes: https://github.com/Billykat7/clinicQ/blob/v0.2.0/docs/GITHUB/RELEASES/RELEASE_v0_2_0.md
      Run: https://github.com/Billykat7/clinicQ/actions/runs/123
      ❌ deploy FAILED (the previous version keeps serving): ClinicQ 0.0.4-broken (a3be05c) → staging
      ```

- [x] **The environments on GitHub:**
      `PRODUCTION: rules=required_reviewers(Billykat7), branch_policy` and `staging: rules=branch_policy`,
      both `custom_branch_policies: true` with `main`; `make gh-sync-environments ARGS=--dry-run`
      → both `unchanged`; `PRODUCTION` still holds its 93 secrets.
- [x] **The releases that built these images** went green with the new Dockerfile: `v0.0.2-test`
      (828 MB, `/health` → `0.0.2-test`, `f17c129`) and `v0.0.3-test`.
- [x] **The suite:** 1024 passed, 16 skipped, 9 xfailed (the two deploy guards no longer pending).
- [ ] **After merge** (GitHub runs `workflow_run` and `workflow_dispatch` from `main` only): a tag
      starting the staging deploy by itself, and a production run waiting for approval. The
      evidence is posted on this PR once merged.

## Acceptance criteria

- [ ] A tag deploys to staging with no human action: **met in its parts, not yet end to end.** The
      trigger (a successful Release) is shown after merge, and every host step ran on the local
      stand-in. There is no real staging host to deploy to until one is provisioned (Issue 102); until
      then a release's staging deploy reports "not provisioned" and changes nothing.
- [x] A production deploy waits for an approval from the DevOps/QA Lead: the `production` Environment
      requires @Billykat7 (above), and the waiting run is shown after merge.
- [x] Migrations run before the new image serves traffic: step order in the workflow (guarded) and in
      the runs above (`run-new` before `candidate` and `swap`).
- [x] A failed smoke check marks the deploy failed and leaves the previous version running (25 of 25
      probes on `0.0.3-test` while the broken candidate failed; exit 1).
- [x] A rollback to the previous tag has been performed at least once and timed: 9.4 s, in the runbook.
- [ ] Deploy results are posted to the team channel. The message and the posting are built and were
      shown against a local stand-in. **No team channel is configured**: the Environment secret
      `TEAM_WEBHOOK_URL` needs a real Slack or Discord webhook, which only the team can create.

## Risk and rollback

Nothing deploys until an environment has a host and its secrets, so merging this changes no running
system. The `production` Environment now requires an approval and allows deploys only from `main`;
if an older process deployed from elsewhere with its secrets, it will now wait or be refused (none
exists in this repository). Undo the settings by editing `.github/environments/production.json` and
running `make gh-sync-environments`; revert the PR to remove the workflow.

**Left in place:** the throwaway tags and images `v0.0.2-test` and `v0.0.3-test` (with `v0.0.0-test`
and `v0.0.1-test` from Issue 10). Deleting them needs your go-ahead.

Closes #11
