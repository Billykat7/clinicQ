# Issue 12: Environment matrix, `.env.example` and fail-fast config validation

> **In short:** Every setting is documented in one example file, and the app refuses to boot anywhere but a laptop with an unsafe value.

| | |
|---|---|
| **Milestone** | [M2: CI/CD, Environments & Team Workflow](../../MILESTONES/M2_cicd_environments.md) |
| **Sprint** | 3 (weeks 5–6) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Infra / Config |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 1](../M1/ISSUE_1_repo_scaffold_app_factory.md): Repository scaffold, Python 3.14 + FastAPI app factory, typed settings |
| **Unblocks** | No other issue waits on this one. |

## Context

Three environments with slightly different settings is where student projects leak credentials and
ship debug mode to production. Config validation at startup turns that class of mistake into a failed
boot instead of a live incident.

## Starting point

- `src/core/config.py` already has production guards (for example, the development JWT secret and live payment keys).
- There is no tracked `.env.example` at the repository root; `scripts/cd/setup.env.example` covers server setup only. The README's *Getting started* already tells people to copy `.env.example`, so it is missing in practice.

## Scope

- Documented environment matrix (local, staging, production): what differs and why
- `.env.example` covering every setting with a comment and a safe default
- Startup validation refusing to boot with a default secret, debug mode or an open CORS policy outside local
- Secrets stored in GitHub Environments and on the host, never in the repository
- A `scripts/check_config.py` that validates an env file without starting the app

## Out of scope

- Deploy workflows (Issue 11).
- Secrets rotation procedures (Issue 98).

## Acceptance criteria

- [ ] The app refuses to start in production with the development `JWT_SECRET`
- [ ] Debug mode cannot be enabled in staging or production
- [ ] `.env.example` lists every setting the app reads, checked by a test against `Settings`
- [ ] No secret value appears anywhere in the repository history
- [ ] `scripts/check_config.py` reports every missing or invalid value in one pass
- [ ] The environment matrix is documented and reviewed by the whole team

## How to verify

1. `cp .env.example .env` and `make run`: the app boots locally with no other edits.
2. Set `ENVIRONMENT=production` with the default secret: startup fails with a clear message.
3. Run `scripts/check_config.py` on a broken file: every problem is listed in one pass.

## Files touched

- `.env.example`
- `src/core/config.py`
- `scripts/check_config.py`
- `docs/CICD/ENVIRONMENTS.md`

---

**Refs:** [M2 milestone](../../MILESTONES/M2_cicd_environments.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #12
