# Issue 12: Environment matrix, `.env.example` and fail-fast config validation

**Area:** Infra / Config
**Milestone:** M2 - CI/CD, Environments & Team Workflow
**Owner role:** DevOps/QA Lead
**Depends on:** Issue 1
**Estimate:** 1 day
**Status:** Planned

## Context

Three environments with slightly different settings is where student projects leak credentials and
ship debug mode to production. Config validation at startup turns that class of mistake into a failed
boot instead of a live incident.

## Scope

- Documented environment matrix (local, staging, production): what differs and why
- `.env.example` covering every setting with a comment and a safe default
- Startup validation refusing to boot with a default secret, debug mode or an open CORS policy outside local
- Secrets stored in GitHub Environments and on the host, never in the repository
- A `scripts/check_config.py` that validates an env file without starting the app

## Acceptance criteria

- [ ] The app refuses to start in production with the development `JWT_SECRET`
- [ ] Debug mode cannot be enabled in staging or production
- [ ] `.env.example` lists every setting the app reads, checked by a test against `Settings`
- [ ] No secret value appears anywhere in the repository history
- [ ] `scripts/check_config.py` reports every missing or invalid value in one pass
- [ ] The environment matrix is documented and reviewed by the whole team

## Files touched

- `.env.example`
- `app/core/config.py`
- `scripts/check_config.py`
- `docs/CICD/ENVIRONMENTS.md`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #12
