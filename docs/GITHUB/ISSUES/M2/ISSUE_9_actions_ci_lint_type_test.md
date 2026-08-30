# Issue 9: GitHub Actions CI: lint, type-check, test with PostGIS + Redis services

**Area:** Infra / CI
**Milestone:** M2 - CI/CD, Environments & Team Workflow
**Owner role:** DevOps/QA Lead
**Depends on:** Issues 7, 8
**Estimate:** 2 days
**Status:** Planned

## Context

The local gate catches most problems; CI is what makes the gate non-optional at merge time. It must
run the same checks as `ci-local.sh` against a real PostGIS and Redis, and it must be cheap enough that
the team never rations pull requests to save minutes.

## Scope

- `.github/workflows/ci.yml` running ruff, mypy and pytest on pull requests to `main`
- PostgreSQL 18 + PostGIS and Redis service containers wired to the test settings
- `timeout-minutes` on every job, dependency caching for `uv`, ruff and mypy
- Concurrency group with `cancel-in-progress` so superseded pushes stop early
- A published test summary and coverage comment on the pull request

## Acceptance criteria

- [ ] A pull request runs the full suite against PostGIS and blocks merge on failure
- [ ] A typical run completes in under 5 minutes
- [ ] Pushing twice in a minute cancels the first run
- [ ] Every job has an explicit timeout well below the GitHub default
- [ ] The workflow is the same set of checks as `ci-local.sh`, verified by a guard test
- [ ] Monthly Actions consumption is projected in the PR description and stays inside the free allowance

## Files touched

- `.github/workflows/ci.yml`
- `tests/unit/test_workflow_guardrails.py`
- `docs/CICD/PIPELINES.md`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #9
