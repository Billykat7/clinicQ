# Issue 9: GitHub Actions CI: lint, type-check, test with PostGIS + Redis services

> **In short:** Every pull request is tested against a real PostGIS and Redis before it can merge, inside the free Actions allowance.

| | |
|---|---|
| **Milestone** | [M2: CI/CD, Environments & Team Workflow](../../MILESTONES/M2_cicd_environments.md) |
| **Sprint** | 2 (weeks 3–4) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Infra / CI |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 7](../M1/ISSUE_7_ci_local_harness_precommit.md): `ci-local.sh` harness (ruff, mypy, pytest, docker build) + pre-commit<br>[Issue 8](../M1/ISSUE_8_test_factories_seed_data.md): Test factories and `seed_dev_data.py` demo dataset |
| **Unblocks** | [Issue 10](../M2/ISSUE_10_container_build_ghcr_release.md): Container build and GHCR publish on tag<br>[Issue 13](../M2/ISSUE_13_team_workflow_templates_codeowners.md): Team workflow: branch protection, PR/issue templates, CODEOWNERS, labels sync |

## Context

The local gate catches most problems; CI is what makes the gate non-optional at merge time. It must
run the same checks as `ci-local.sh` against a real PostGIS and Redis, and it must be cheap enough that
the team never rations pull requests to save minutes.

## Starting point

- There is no `.github/` directory yet, so this issue creates the first workflow.
- `tests/unit/platform/test_workflow_guardrails.py` already exists and parses `.github/workflows/`: it is the guard test the acceptance criteria ask for. **Until this issue lands it fails** (12 of the unit-test failures in `make test` today are this file, plus the workflow half of `test_scanning_config.py`), so do not read those red tests as someone else's breakage.
- Mirror the stages of `scripts/ci-local.sh` (ruff, mypy, pytest) rather than inventing new ones; pip-audit and Trivy stay local-only by design (see `scripts/README.md`).

## Scope

- `.github/workflows/ci.yml` running ruff, mypy and pytest on pull requests to `main`
- PostgreSQL 18 + PostGIS and Redis service containers wired to the test settings, using the images the dev stack pins (`postgis/postgis:18-3.6` and `redis:8-alpine`, decision 4); `tests/unit/platform/test_dev_stack_compose.py` fails if the workflow's PostgreSQL image differs from the compose file's
- `timeout-minutes` on every job, dependency caching for `uv`, ruff and mypy
- Concurrency group with `cancel-in-progress` so superseded pushes stop early
- A published test summary and coverage comment on the pull request

## Out of scope

- Building and publishing images (Issue 10).
- Deploying anything (Issue 11).
- Branch protection rules (Issue 13).

## Acceptance criteria

- [ ] A pull request runs the full suite against PostGIS and blocks merge on failure
- [ ] A typical run completes in under 5 minutes
- [ ] Pushing twice in a minute cancels the first run
- [ ] Every job has an explicit timeout well below the GitHub default
- [ ] The workflow is the same set of checks as `ci-local.sh`, verified by a guard test
- [ ] Monthly Actions consumption is projected in the PR description and stays inside the free allowance

## How to verify

1. Open a PR with a deliberately failing test: CI goes red and the merge button is blocked.
2. Push twice within a minute: the first run is cancelled.
3. `make test` passes locally, including the workflow guardrail tests.

## Files touched

- `.github/workflows/ci.yml`
- `tests/unit/platform/test_workflow_guardrails.py`
- `docs/CICD/PIPELINES.md`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #9
