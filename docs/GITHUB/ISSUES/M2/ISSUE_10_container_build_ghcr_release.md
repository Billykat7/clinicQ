# Issue 10: Container build and GHCR publish on tag

**Area:** Infra / CD
**Milestone:** M2 - CI/CD, Environments & Team Workflow
**Owner role:** DevOps/QA Lead
**Depends on:** Issue 9
**Estimate:** 1 day
**Status:** Planned

## Context

Deployment needs an immutable artefact. Building the image on tag, not on every commit, keeps the
minute budget intact while giving every release a byte-identical thing to deploy and roll back to.

## Scope

- Multi-stage `Dockerfile` producing a small non-root runtime image
- `.github/workflows/release.yml` building and pushing to GHCR on `v*.*.*` tags
- Three tags per release pointing at one manifest: `:<version>`, `:latest`, `:<sha>`
- Build provenance and an image label carrying the git sha and build time
- A documented `docker run` command that boots the image against a local database

## Acceptance criteria

- [ ] Pushing `v0.2.0` publishes an image to GHCR within 6 minutes
- [ ] The image runs as a non-root user and contains no build toolchain
- [ ] `docker run` of the published image serves `/health` successfully
- [ ] The image reports its version and git sha at `/health`
- [ ] Image size stays under an agreed ceiling, checked in the workflow
- [ ] Rolling back means deploying a previous tag, with no rebuild required

## Files touched

- `Dockerfile`
- `.dockerignore`
- `.github/workflows/release.yml`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #10
