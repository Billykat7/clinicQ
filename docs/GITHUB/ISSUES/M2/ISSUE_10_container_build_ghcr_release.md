# Issue 10: Container build and GHCR publish on tag

> **In short:** Tagging a release produces one small, non-root image in GHCR that any environment can run and any rollback can reuse.

| | |
|---|---|
| **Milestone** | [M2: CI/CD, Environments & Team Workflow](../../MILESTONES/M2_cicd_environments.md) |
| **Sprint** | 2 (weeks 3–4) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Infra / CD |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 9](../M2/ISSUE_9_actions_ci_lint_type_test.md): GitHub Actions CI: lint, type-check, test with PostGIS + Redis services |
| **Unblocks** | [Issue 11](../M2/ISSUE_11_cd_staging_prod_approval.md): CD: staging deploy on tag, production behind manual approval |

## Context

Deployment needs an immutable artefact. Building the image on tag, not on every commit, keeps the
minute budget intact while giving every release a byte-identical thing to deploy and roll back to.

## Starting point

- `infra/docker/Dockerfile` is already a multi-stage build that ends as a non-root `appuser`.
- `/health` already reports the app version (`src/main.py`); the git sha still needs adding.

## Scope

- Multi-stage `Dockerfile` producing a small non-root runtime image
- `.github/workflows/release.yml` building and pushing to GHCR on `v*.*.*` tags
- Three tags per release pointing at one manifest: `:<version>`, `:latest`, `:<sha>`
- Build provenance and an image label carrying the git sha and build time
- A documented `docker run` command that boots the image against a local database

## Out of scope

- Deploying the image (Issue 11).
- The pull-request CI workflow (Issue 9).

## Acceptance criteria

- [ ] Pushing `v0.2.0` publishes an image to GHCR within 6 minutes
- [ ] The image runs as a non-root user and contains no build toolchain
- [ ] `docker run` of the published image serves `/health` successfully
- [ ] The image reports its version and git sha at `/health`
- [ ] Image size stays under an agreed ceiling, checked in the workflow
- [ ] Rolling back means deploying a previous tag, with no rebuild required

## How to verify

1. Push a throwaway `v0.0.0-test` tag: the image appears in GHCR with `:<version>`, `:latest` and `:<sha>`.
2. `docker run` the published image against a local database: `/health` shows the version and sha.
3. `docker run --rm <image> id` prints a non-root user.

## Files touched

- `infra/docker/Dockerfile`
- `.dockerignore`
- `.github/workflows/release.yml`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #10
