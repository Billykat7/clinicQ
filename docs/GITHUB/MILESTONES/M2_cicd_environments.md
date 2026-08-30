# Milestone 2: CI/CD, Environments & Team Workflow

**Status:** 📋 planned · **Phase:** Semester 1 · Sprint 2 · **Suggested tag:** `v0.2.0`
**Primary owner:** DevOps/QA Lead
**Depends on:** M1
**Blocks:** Nothing functionally, but running it late means every branch merges without a gate. Deliver it inside Sprint 2 or the team pays for it in integration pain from M5 onwards.

## Goal

Automate the quality gate and the deployment path, and encode the team's collaboration rules (branch names, PR template, CODEOWNERS, labels) so that six people merging into one `main` stays cheap and predictable.

## Why this milestone exists

This is a **six-person student project on the GitHub Free plan**, which means two constraints shape
the pipeline: Actions minutes are finite, and nobody is available to babysit a broken `main` at
23:00 the night before a demo. The answer is the same one the sibling `properties` project landed
on: a **local** gate that every developer runs before pushing (`./scripts/ci-local.sh`), a **cheap**
CI on pull requests, and **deployment only on tags**.

CODEOWNERS is not bureaucracy here; it is the anti-blocking mechanism. It routes a review to the
person who owns that module so nobody waits on the one teammate who happens to be online.

## Scope

- GitHub Actions CI: ruff, mypy, pytest with Postgres/PostGIS + Redis services, timeouts and caching
- Container image build and publish to GHCR on tag
- CD workflow deploying to the staging VPS on tag, with a manual production approval step
- `.env.example`, environment matrix (local / staging / production), fail-fast config validation
- Branch protection, PR template, issue templates, CODEOWNERS, `labels.yml` sync
- Monitoring baseline: uptime checks, error tracking, deploy notifications

## Issues

| # | Title |
|---|-------|
| 9 | GitHub Actions CI: lint, type-check, test with PostGIS + Redis services |
| 10 | Container build and GHCR publish on tag |
| 11 | CD: staging deploy on tag, production behind manual approval |
| 12 | Environment matrix, `.env.example` and fail-fast config validation |
| 13 | Team workflow: branch protection, PR/issue templates, CODEOWNERS, labels sync |
| 14 | Monitoring baseline: uptime checks, error tracking, deploy notifications |

## Exit criteria

- [ ] A pull request runs lint + type-check + tests and blocks merge on failure
- [ ] Pushing a `v*.*.*` tag builds an image, publishes it to GHCR and deploys it to staging
- [ ] Production deploys require an explicit approval from the DevOps/QA Lead
- [ ] The app refuses to boot with a missing or default secret outside local development
- [ ] Every issue and PR carries a milestone, an area label and a CODEOWNERS-routed reviewer
- [ ] Staging downtime raises an alert in the team channel within 5 minutes

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M2/)
