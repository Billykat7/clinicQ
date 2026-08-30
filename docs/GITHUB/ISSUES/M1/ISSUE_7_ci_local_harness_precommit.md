# Issue 7: `ci-local.sh` harness (ruff, mypy, pytest, docker build) + pre-commit

**Area:** Infra / Quality
**Milestone:** M1 - Foundation & Local CI
**Owner role:** DevOps/QA Lead
**Depends on:** Issues 1, 3
**Estimate:** 1 day
**Status:** Planned

## Context

GitHub Actions minutes are limited on the Free plan, and a broken `main` blocks five other people.
The primary quality gate is therefore local: one script that runs everything CI would run, fast enough
that developers actually use it before pushing.

## Scope

- `scripts/ci-local.sh`: ruff → mypy → pytest (with coverage) → docker build, failing fast with clear output
- Pre-commit hooks for formatting, trailing whitespace, large files and secret detection
- A pytest configuration with markers (`unit`, `integration`, `slow`) and a default fast selection
- Coverage threshold set to a level the team agrees to hold, enforced in the script
- Documented usage in `CONTRIBUTING.md`, including how to run one module's tests only

## Acceptance criteria

- [ ] `./scripts/ci-local.sh` completes in under 3 minutes on a mid-range laptop
- [ ] The script exits non-zero on the first failing stage and prints which stage failed
- [ ] Pre-commit blocks a commit containing an obvious secret
- [ ] `pytest -m unit` runs in under 30 seconds
- [ ] Coverage below the agreed threshold fails the script
- [ ] `CONTRIBUTING.md` documents the pre-push workflow every team member follows

## Files touched

- `scripts/ci-local.sh`
- `.pre-commit-config.yaml`
- `pyproject.toml [tool.pytest.ini_options]`
- `CONTRIBUTING.md`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #7
