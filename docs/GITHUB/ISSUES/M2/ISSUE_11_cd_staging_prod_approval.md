# Issue 11: CD: staging deploy on tag, production behind manual approval

> **In short:** A tag deploys itself to staging; production waits for one named person's approval, runs migrations first, and can be rolled back in minutes.

| | |
|---|---|
| **Milestone** | [M2: CI/CD, Environments & Team Workflow](../../MILESTONES/M2_cicd_environments.md) |
| **Sprint** | 3 (weeks 5–6) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Infra / CD |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 10](../M2/ISSUE_10_container_build_ghcr_release.md): Container build and GHCR publish on tag |
| **Unblocks** | [Issue 14](../M2/ISSUE_14_monitoring_baseline_alerts.md): Monitoring baseline: uptime checks, error tracking, deploy notifications<br>[Issue 102](../M14/ISSUE_102_production_infra_tls.md): Production infrastructure, TLS, domains and edge protection |

## Context

Staging exists so that integration problems surface before a clinic sees them. Production deploys
carry a manual approval because a broken deploy during clinic hours is not an inconvenience; it is a
waiting room with no board.

## Starting point

- `infra/docker/docker-compose.prod.yml` already expects a full image reference in `IMAGE`, and `scripts/cd/` holds the server setup, env-writing and image-pruning scripts.
- Readiness at `/health/ready` already checks that migrations are at head, which makes a good smoke check.

## Scope

- `.github/workflows/deploy.yml` pulling the tagged image and running `docker compose up -d` on the target host
- Staging deploys automatically on tag; production requires a GitHub environment approval
- Database migrations run as a separate, explicit step before the app is swapped in
- Post-deploy smoke check hitting `/ready` and one real page, failing the deploy on error
- Documented rollback procedure with a target time of under 10 minutes

## Out of scope

- Server provisioning for production (Issue 102).
- Monitoring and alerting (Issue 14).

## Acceptance criteria

- [ ] A tag deploys to staging with no human action
- [ ] A production deploy waits for an approval from the DevOps/QA Lead
- [ ] Migrations run before the new image serves traffic
- [ ] A failed smoke check marks the deploy failed and leaves the previous version running
- [ ] A rollback to the previous tag has been performed at least once and timed
- [ ] Deploy results are posted to the team channel

## How to verify

1. Tag a release: staging updates with no human action and the smoke check passes.
2. Trigger a production deploy: it waits for the DevOps/QA Lead's approval.
3. Deploy a build that fails the smoke check: the previous version keeps serving.
4. Time one rollback to the previous tag and record it in the runbook.

## Files touched

- `.github/workflows/deploy.yml`
- `infra/docker/docker-compose.prod.yml`
- `scripts/cd/`
- `docs/CICD/RUNBOOK_DEPLOY.md`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #11
