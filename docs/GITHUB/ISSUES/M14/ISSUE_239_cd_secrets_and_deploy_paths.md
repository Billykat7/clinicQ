# Issue 239: Where a public repository keeps its deployment secrets, and its host's paths

> **In short:** The deployed `.env` was one write-only GitHub secret and the repository wrote down where the app lives on the server. Split the file — configuration in git and reviewed, credentials encrypted in the private infra repository — and let `DEPLOY_DIR` be the only thing that knows the host's layout.

| | |
|---|---|
| **Milestone** | [M14: Production Readiness, Pilot & Go-live](../../MILESTONES/M14_production_pilot_golive.md) |
| **Sprint** | 11 |
| **Owner** | E, DevOps/QA |
| **Area** | CI/CD / Platform |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 230](../M15/ISSUE_230_deploy_on_self_hosted_runner.md): Deploy on the self-hosted runner<br>[Issue 11](../M2/ISSUE_11_cd_staging_prod_approval.md): Deploy to staging and production with approval |

## Context

[Issue 230](../M15/ISSUE_230_deploy_on_self_hosted_runner.md) made the runner *be* the host, which
removed SSH and four secrets with it. What it left behind is the question this issue answers: with
no transport problem, where should the app's settings actually live?

Two answers are in the tree today and both are wrong for a **public** repository.

**The settings.** `APP_ENV` — the whole `.env` as one GitHub Environment secret. GitHub secrets are
write-only: you cannot read one back to diff it, there is no version history, and no audit of reads,
so *"what was production running last release"* has no answer. They are also bounded: 48 KB per
secret, 64 KB for all of a repository's secrets together, 100 secrets. The file is 238 keys.

**The paths.** `/opt/btk/clinicq` and `/opt/btk/gateway` are hard-coded defaults in `deploy.yml`, the
production compose file, both runbooks, the runner notes and the CD scripts. This repository is
public, so that publishes the layout of the machine the app runs on — and it has to be edited in git
the day that machine moves.

## Starting point

- Of the app's 182 settings, **21 hold a credential and 161 do not**. Treating all 182 as secret is
  what filled the GitHub budget with feature flags, timeouts and OAuth endpoint URLs.
- There is no machine-readable marker for which is which. `.env.example`'s generator has a
  name-based guess that misses `DATABASE_URL`, `REDIS_URL`, `SENTRY_DSN`, `SMTP_USER` and
  `TEAM_WEBHOOK_URL` — every one of which hides a credential in a value that looks ordinary — and
  wrongly flags six policy knobs whose names merely contain a marker word.
- Actions logs on a public repository are public too, and GitHub masks secrets but not `vars`.
- `deploy.sh` already takes its directory from its own location, so only the workflow needs telling.

## Scope

- One classifier for what a secret is, beside the settings, shared by every reader of it
- `deploy/env/<environment>.env`: the non-secret half, committed, only what differs from the default
- The secret half encrypted with SOPS + age in the private `Billykat7/infra`, composed on the host
- `DEPLOY_DIR` as the only source of the deploy directory, masked whatever kind it is
- `GATEWAY_COMPOSE_DIR` with no fallback: a clear failure rather than a path nobody chose
- Guards: no tracked file names a host path; no key in the committed half is a credential

## Out of scope

- Generating the age identity or checking `Billykat7/infra` out on the host (an operator action,
  documented in the runbook)
- Moving the *other* BTK products to the same arrangement
- A secret manager as a service (AWS Secrets Manager, Vault): considered and not chosen — the
  encrypted-file-in-a-private-repo shape needs no new service and gives review and history

## Acceptance criteria

- [ ] No tracked file names an absolute path on the deploy host, and a test fails if one returns
- [ ] `DEPLOY_DIR` is the only source of the deploy directory; unset, the deploy changes nothing and
      says what to set
- [ ] The deploy directory is masked in the run log and is never a step output
- [ ] `deploy/env/{staging,production}.env` are committed, and a test fails if any key in them is one
      the classifier calls a secret
- [ ] Every key in those files is a setting the app reads, or a documented compose key
- [ ] `scripts/cd/compose-env.sh` builds `$DEPLOY_DIR/.env` (mode 600) from both halves, prints no
      value, and leaves no half-written file on failure
- [ ] `APP_ENV` is gone, and a host still holding its own `.env` keeps deploying
- [ ] `.env.example` and the guard test agree about what a secret is, because they share the code

## How to verify

1. `grep -rn "/opt/btk"` over the tracked tree: only the merged pull-request records.
2. `pytest tests/unit/platform/test_deploy_env.py tests/unit/platform/test_workflow_guardrails.py`.
3. Add `DATABASE_URL=` to `deploy/env/production.env`: the guard fails and names it.
4. With real `sops` and `age`: encrypt a file, run `compose-env.sh production`, and check the result
   with `scripts/check_config.py --environment production`.

## Files touched

- `.github/workflows/deploy.yml`, `infra/docker/docker-compose.prod.yml`
- `deploy/env/{README.md,staging.env,production.env}`, `scripts/cd/compose-env.sh`
- `src/core/config.py`, `scripts/generate_env_example.py`, `.env.example`, `.gitignore`
- `scripts/cd/deploy.sh`, `scripts/cd/write-prod-env.sh`, `scripts/check_config.py`
- `docs/CICD/ENVIRONMENTS.md`, `docs/CICD/RUNBOOK_DEPLOY.md`, `docs/CICD/RUNBOOK_ALERTS.md`
- `tests/unit/platform/test_deploy_env.py`, `tests/unit/platform/test_workflow_guardrails.py`

---

**Refs:** [M14 milestone](../../MILESTONES/M14_production_pilot_golive.md) · [environments](../../../CICD/ENVIRONMENTS.md) · [deploy runbook](../../../CICD/RUNBOOK_DEPLOY.md) · [how to read this spec](../README.md)

Closes #239
