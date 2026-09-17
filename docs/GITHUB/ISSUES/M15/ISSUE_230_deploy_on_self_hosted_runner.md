# Issue 230: Deploy from the self-hosted runner, not over SSH

> **In short:** ClinicQ runs on the BTK platform server, and that server hosts this repository's own Actions runner. The deploy still asks for an address, a key and a `known_hosts` line so it can SSH to the machine it is already on — and refuses to deploy until all five secrets exist.

| | |
|---|---|
| **Milestone** | [M15: Clinic Onboarding & Patient Sign-in](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) |
| **Sprint** | 16 (weeks 31–32) |
| **Owner** | E, DevOps/QA (backup: A, Backend Lead) |
| **Area** | CI/CD / Infrastructure |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 11](../M2/ISSUE_11_cd_staging_prod_approval.md): the deploy workflow and `deploy.sh` |
| **Unblocks** | [Issue 102](../M14/ISSUE_102_production_infra_tls.md): production infrastructure, TLS and domains |

## Context

Deploying `0.11.0` to production ends like this, every time:

```
production ← ghcr.io/billykat7/clinicq:0.11.0
Not provisioned (missing DEPLOY_HOST DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS APP_ENV DEPLOY_DIR):
nothing was deployed.
```

The workflow is doing what it was written to do. `deploy.yml` runs on `ubuntu-24.04`, so it has to
travel to the host, and it refuses to start until it has everything it needs to travel:

```yaml
runs-on: ubuntu-24.04
...
for name in DEPLOY_HOST DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS APP_ENV DEPLOY_DIR; do
  [[ -n "${!name}" ]] || missing+=("$name")
done
```

**But the journey is not needed.** The BTK platform server hosts a self-hosted runner registered
against this repository, labelled `self-hosted, Linux, X64, clinicq, infra`. A job that runs there is
already standing in `/opt/btk/clinicq`. Four of the five secrets exist only to let it reach a machine
it never left, and the fifth (`APP_ENV`) duplicates a `.env` the host already has.

This is also the shape the rest of the platform uses. BK Properties' `cd.yml` is a thin caller of
`Billykat7/infra`'s `cd-product.yml`, which runs `runs-on: [self-hosted, Linux, X64, infra]` and does
its work locally; the platform registry already reserves `clinicq` → `clinicq.bkatalayi.com` → port
`8011` → `/opt/btk/clinicq`. And the repository's own notes predicted this change:
`docs/GITHUB/RUNNER/README.md` § *Point the deploy at it* describes it as the next step, down to the
two things that have to move with it.

**What this issue does not do is call `cd-product.yml`.** That workflow deliberately leaves schema
migrations to an operator, has no candidate-on-a-side-port step, and writes `.env` from
`write-prod-env.sh`, whose key list is shared across BTK products and contains none of ClinicQ's ~179
settings — so every `SMS_*`, `QUEUE_*`, `DISPLAY_*`, `PATIENT_*` and `VAPID_*` value would silently
fall back to its default. ClinicQ keeps its own sequence, which is tested, and moves *where it runs*.

## Starting point

- `.github/workflows/deploy.yml`: the five-secret provisioning guard, the SSH setup step, the `scp`,
  and six `ssh target "cd $DEPLOY_DIR && ./deploy.sh …"` invocations.
- `scripts/cd/deploy.sh`: already written to run **on the host, from its deploy directory** — every
  step works unchanged when the shell is local instead of an SSH session.
- `docs/GITHUB/RUNNER/README.md` and `clinicq.md`: the install notes, which recommend the label
  `clinicq-deploy`; the runner that actually exists carries `clinicq`.
- `tests/unit/platform/test_workflow_guardrails.py`: the guards the new shape must still satisfy —
  the deploy calls `scripts/db/deploy-sequence.sh`, migrations precede the candidate and the swap, a
  rollback skips them, the concurrency group is ref-independent, every job has a timeout, every
  action is SHA-pinned.

## Scope

- `runs-on` becomes the self-hosted runner, through a variable so a different machine needs no edit:
  `${{ fromJSON(vars.DEPLOY_RUNNER_LABELS || '["self-hosted","Linux","X64","clinicq"]') }}`.
- Delete the SSH key/`known_hosts` step, the `scp`, and every `ssh target` wrapper; the steps become
  the local commands with `working-directory`.
- The deploy directory is **derived**, not configured: `/opt/btk/clinicq` (`-staging` for staging),
  overridable by `DEPLOY_DIR` or by the gateway's `PRODUCTION_DEPLOY_PATH`.
- `APP_ENV` becomes **optional**: written when set, otherwise the host's own `.env` is used and left
  untouched. Only "no `.env` and no `APP_ENV`" is an error, and it says what to do.
- "Not provisioned" now means one thing a person can act on: the deploy directory does not exist.
- A `docker login ghcr.io` step, since the pull now happens on the host with `GITHUB_TOKEN`.
- Guard tests: no `ssh`/`scp`/`DEPLOY_SSH_KEY` may come back, and the defaults must stay.
- The runbook, the runner notes and `ENVIRONMENTS.md` say the new shape, including "delete these
  four secrets".

## Out of scope

- Calling infra's `cd-product.yml` (see above), and any change to `Billykat7/infra`.
- The nginx/registry/gateway integration the other products get from `cd-product.yml`;
  [Issue 102](../M14/ISSUE_102_production_infra_tls.md) owns the edge.
- `ci.yml` and `release.yml`, which stay on GitHub-hosted runners: they build and test untrusted pull
  request code, which must never touch the deploy host.
- Installing or registering the runner. It exists; `docs/GITHUB/RUNNER/clinicq.md` covers it.

## Acceptance criteria

- [ ] A dispatched deploy on a platform host runs with **no Environment secret and no variable set**
- [ ] No step SSHes, `scp`s, writes a key or reads `DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_SSH_KEY` /
      `DEPLOY_KNOWN_HOSTS`
- [ ] The deploy directory defaults to `/opt/btk/clinicq`, `/opt/btk/clinicq-staging` for staging,
      and `DEPLOY_DIR` still overrides it
- [ ] With no `APP_ENV`, the host's `.env` is used and is not rewritten; with `APP_ENV`, it is
      written mode 600 and never echoed
- [ ] A missing deploy directory reports what to create, changes nothing and does not fail the run
- [ ] Every existing workflow guard still passes: the deploy sequence, the migration order, the
      rollback skip, the concurrency group, the timeouts, the pinned actions
- [ ] `actionlint .github/workflows/deploy.yml` is clean

## How to verify

1. `actionlint .github/workflows/*.yml`
2. `TZ=UTC pytest tests/unit/platform`
3. `make check`
4. On the host, the same steps by hand: `cd /opt/btk/clinicq && DEPLOY_ENV=production ./deploy.sh status`

## Files touched

- `.github/workflows/deploy.yml`
- `docs/CICD/RUNBOOK_DEPLOY.md`, `docs/CICD/ENVIRONMENTS.md`
- `docs/GITHUB/RUNNER/README.md`, `docs/GITHUB/RUNNER/clinicq.md`
- `tests/unit/platform/test_workflow_guardrails.py`

---

**Refs:** [M15 milestone](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) · [how to read this spec](../README.md)

Closes #230
