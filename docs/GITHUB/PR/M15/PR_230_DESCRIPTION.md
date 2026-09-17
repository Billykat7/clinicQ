# PR: The deploy runs on the host instead of SSHing to it (Issue 230 / M15-230)

**Milestone:** [Milestone 15: Clinic Onboarding & Patient Sign-in](https://github.com/Billykat7/clinicQ/milestone/15) ·
**Issue:** [#230](https://github.com/Billykat7/clinicQ/issues/230) · **Builds on:** #11 (the deploy
workflow and `deploy.sh`), merged

Deploying `0.11.0` to production ended the same way every time:

```
production ← ghcr.io/billykat7/clinicq:0.11.0
Not provisioned (missing DEPLOY_HOST DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS APP_ENV DEPLOY_DIR):
nothing was deployed.
```

The workflow was doing exactly what it was written to do. It ran on `ubuntu-24.04`, so it had to
travel to the host, and it refused to start until it had everything needed to travel:

```yaml
runs-on: ubuntu-24.04
...
for name in DEPLOY_HOST DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS APP_ENV DEPLOY_DIR; do
  [[ -n "${!name}" ]] || missing+=("$name")
done
```

**The journey is not needed.** The BTK platform server that ClinicQ lives on hosts this repository's
own Actions runner, labelled `self-hosted, Linux, X64, clinicq, infra`. A job that runs there is
already standing in `/opt/btk/clinicq`. Four of those five secrets existed only to describe how to
reach a machine the job would never leave, and the fifth duplicated a `.env` the host already has.

That is also the shape the rest of the platform uses: BK Properties' `cd.yml` is a thin caller of
`Billykat7/infra`'s `cd-product.yml`, which runs `runs-on: [self-hosted, Linux, X64, infra]` and works
locally. The platform registry already reserves `clinicq` → `clinicq.bkatalayi.com` → `:8011` →
`/opt/btk/clinicq`. And this repository's own notes had written the change down in advance —
`docs/GITHUB/RUNNER/README.md` § *Point the deploy at it*, down to the two things that move with it.

## After this PR, a production deploy needs nothing configured

| Before | After |
|---|---|
| `DEPLOY_HOST` secret | — gone |
| `DEPLOY_USER` secret | — gone |
| `DEPLOY_SSH_KEY` secret | — gone |
| `DEPLOY_KNOWN_HOSTS` secret | — gone |
| `APP_ENV` secret, **required** | **optional** — without it the host's own `.env` is used, and left alone |
| `DEPLOY_DIR` variable, **required** | **optional** — defaults to `/opt/btk/clinicq` (`-staging` for staging) |

> **Delete those four secrets** from both Environments. A live deploy key with nothing left to check
> it is worse than no key.

## What did *not* change

`scripts/cd/deploy.sh` is untouched: it was always written to run **on the host, from its deploy
directory**, so the same five steps now run in a local shell instead of an SSH session, in the same
order, with the same rollback behaviour.

| Step | `deploy.sh` | If it fails |
|---|---|---|
| Pre-flight | `preflight <image> <env>` | nothing has changed |
| Migrations (skipped on a rollback) | `run-new <image> ./scripts/db/deploy-sequence.sh` | the old version keeps serving |
| Candidate on a side port | `candidate <image>` | the old version never stopped serving |
| Swap, smoke-checked live | `swap <image>` | it rolls back by itself |
| Team channel | `notify_deploy.py` | nothing |

Also unchanged: the production approval gate, one deploy per environment at a time never cancelled
mid-flight, the version input and the rollback tick.

## Why not call infra's `cd-product.yml`

It is the obvious move and it would be wrong here, for three reasons that all cost silently:

1. **It leaves schema migrations to an operator.** ClinicQ's sequence runs them in the new image
   *before it serves anything*, and CI runs the same script on every pull request (Issue 9).
2. **It has no candidate-on-a-side-port step.** ClinicQ smoke-tests the new image on a port nothing
   routes to before the swap, then rolls back by itself if the live check fails.
3. **Its `.env` comes from `write-prod-env.sh`, whose key list is shared across BTK products** and
   contains none of ClinicQ's ~179 settings — every `SMS_*`, `QUEUE_*`, `DISPLAY_*`, `PATIENT_*` and
   `VAPID_*` value would quietly fall back to its default.

So ClinicQ keeps its own sequence and moves only *where it runs*. Nothing in `Billykat7/infra`
changes.

## Summary

- **`.github/workflows/deploy.yml`**
  - `runs-on: ${{ fromJSON(vars.DEPLOY_RUNNER_LABELS || '["self-hosted","Linux","X64","clinicq"]') }}`
    — a repository variable moves the deploy to another runner without editing the file.
  - The SSH setup step, the `scp` and six `ssh target "cd $DEPLOY_DIR && …"` wrappers are gone;
    the steps are the commands, with `working-directory`.
  - *Where this environment lives*: derives the directory (`DEPLOY_DIR` → the gateway's
    `PRODUCTION_DEPLOY_PATH` → `/opt/btk/clinicq[-staging]`), and if it does not exist writes a
    summary saying what to create rather than listing five secrets.
  - *The app's settings*: `APP_ENV` when set (mode 600, never echoed), otherwise the host's `.env`,
    used as it is. Only "neither" is an error, and it names both ways to fix it.
  - `docker login ghcr.io` with `GITHUB_TOKEN`, because the pull now happens on the host.
  - `permissions: packages: read`.
- **Two new guard tests** in `tests/unit/platform/test_workflow_guardrails.py`:
  `test_the_deploy_runs_on_the_host_rather_than_reaching_it_over_ssh` (no `ssh`, `scp`,
  `ssh-keyscan`, `known_hosts` or `DEPLOY_*` credential may come back, and the labels must include
  `self-hosted` and `clinicq`) and `test_a_deploy_needs_no_configuration_on_a_platform_host`.
- **Docs:** `RUNBOOK_DEPLOY.md` (what a deploy does, and a "setting up a host" section that is now
  three short steps with a note to delete the four secrets), `ENVIRONMENTS.md`,
  `docs/GITHUB/RUNNER/README.md` and `clinicq.md` — including the label, which the notes had as
  `clinicq-deploy` and the runner that actually exists carries as **`clinicq`**.

## Verification

- [x] `actionlint .github/workflows/deploy.yml` — clean.
- [x] `TZ=UTC pytest tests/unit/platform` — **288 passed**, 3 xfailed, including every existing
  deploy guard: the deploy sequence is the one CI runs, migrations precede the candidate and the
  swap, a rollback skips them, the concurrency group is ref-independent and never cancels, every job
  has a timeout, every action is SHA-pinned.
- [x] `ruff check`, `ruff format --check`, `mypy src/` clean.
- [x] `python scripts/update_milestone_progress.py --assume-closed 229,230`.

**Not runnable here, and not claimed:** a deploy itself. It needs the platform host's runner, which
is not this machine. The first real run is the acceptance test — and if the deploy directory is not
there yet, it will now say so in one line instead of listing five secrets.

## Acceptance criteria

- [x] **A dispatched deploy on a platform host needs no Environment secret and no variable** — the
  directory is derived and the host's `.env` is the default source of settings.
- [x] **No step SSHes, `scp`s, writes a key or reads the four `DEPLOY_*` credentials** — guard test.
- [x] **The directory defaults to `/opt/btk/clinicq`, `-staging` for staging; `DEPLOY_DIR` overrides.**
- [x] **`APP_ENV` absent → the host's `.env` is used and not rewritten; present → mode 600, never echoed**
  — and the existing `test_the_app_settings_reach_the_host_privately` still passes.
- [x] **A missing deploy directory reports what to create, changes nothing, does not fail the run.**
- [x] **Every existing workflow guard passes.**
- [x] **`actionlint` is clean.**

## Risk and rollback

- **The first deploy after this merges needs the runner to be online and its user in the `docker`
  group.** If the runner is offline the run waits for one rather than failing, which is visible in
  the Actions list.
- **`ci.yml` and `release.yml` stay on GitHub-hosted runners** deliberately: they build and test
  pull-request code, which must never execute on the deploy host.
- **Rollback:** revert. The four secrets would have to be recreated; nothing on any host changes
  either way, because no deploy has succeeded through the old path.

Closes #230
