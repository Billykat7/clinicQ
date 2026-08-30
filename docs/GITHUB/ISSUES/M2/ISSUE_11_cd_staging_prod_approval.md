# Issue 11: CD: staging deploy on tag, production behind manual approval

**Area:** Infra / CD
**Milestone:** M2 - CI/CD, Environments & Team Workflow
**Owner role:** DevOps/QA Lead
**Depends on:** Issue 10
**Estimate:** 2 days
**Status:** Planned

## Context

Staging exists so that integration problems surface before a clinic sees them. Production deploys
carry a manual approval because a broken deploy during clinic hours is not an inconvenience; it is a
waiting room with no board.

## Scope

- `.github/workflows/deploy.yml` pulling the tagged image and running `docker compose up -d` on the target host
- Staging deploys automatically on tag; production requires a GitHub environment approval
- Database migrations run as a separate, explicit step before the app is swapped in
- Post-deploy smoke check hitting `/ready` and one real page, failing the deploy on error
- Documented rollback procedure with a target time of under 10 minutes

## Acceptance criteria

- [ ] A tag deploys to staging with no human action
- [ ] A production deploy waits for an approval from the DevOps/QA Lead
- [ ] Migrations run before the new image serves traffic
- [ ] A failed smoke check marks the deploy failed and leaves the previous version running
- [ ] A rollback to the previous tag has been performed at least once and timed
- [ ] Deploy results are posted to the team channel

## Files touched

- `.github/workflows/deploy.yml`
- `infra/docker-compose.prod.yml`
- `docs/CICD/RUNBOOK_DEPLOY.md`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #11
