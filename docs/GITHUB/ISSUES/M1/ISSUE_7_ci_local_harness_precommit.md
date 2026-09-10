# Issue 7: `ci-local.sh` harness (ruff, mypy, pytest, docker build) + pre-commit

> **In short:** One script every teammate runs before pushing, so a red CI run is a surprise rather than a habit.

| | |
|---|---|
| **Milestone** | [M1: Foundation & Local CI](../../MILESTONES/M1_foundation_local_ci.md) |
| **Sprint** | 2 (weeks 3–4) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Infra / Quality |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 1](../M1/ISSUE_1_repo_scaffold_app_factory.md): Repository scaffold, Python 3.14 + FastAPI app factory, typed settings<br>[Issue 3](../M1/ISSUE_3_sqlalchemy_alembic_baseline.md): Async SQLAlchemy 2.x + Alembic baseline (PostGIS extension enabled) |
| **Unblocks** | [Issue 9](../M2/ISSUE_9_actions_ci_lint_type_test.md): GitHub Actions CI: lint, type-check, test with PostGIS + Redis services |

## Context

GitHub Actions minutes are limited on the Free plan, and a broken `main` blocks five other people.
The primary quality gate is therefore local: one script that runs everything CI would run, fast enough
that developers actually use it before pushing.

## Starting point

- `scripts/ci-local.sh` already runs ruff, mypy, pytest and a Docker build, with optional pip-audit, Trivy and a secret scan (`make check`, `make check-fast`).
- `make hooks` expects a `.pre-commit-config.yaml` that is not in the repository yet, and `tests/unit/platform/test_scanning_config.py` expects a `.gitleaks.toml` (the secret-scan config), which is also missing and makes that test fail today.
- Still to agree: the coverage threshold and the `unit` / `integration` / `slow` pytest markers.

## Scope

- `scripts/ci-local.sh`: ruff → mypy → pytest (with coverage) → docker build, failing fast with clear output
- Pre-commit hooks for formatting, trailing whitespace, large files and secret detection
- A pytest configuration with markers (`unit`, `integration`, `slow`) and a default fast selection
- Coverage threshold set to a level the team agrees to hold, enforced in the script
- Documented usage in `CONTRIBUTING.md`, including how to run one module's tests only

## Out of scope

- The GitHub Actions workflow (Issue 9).
- Test factories (Issue 8).

## Acceptance criteria

- [ ] `./scripts/ci-local.sh` completes in under 3 minutes on a mid-range laptop
- [ ] The script exits non-zero on the first failing stage and prints which stage failed
- [ ] Pre-commit blocks a commit containing an obvious secret
- [ ] `pytest -m unit` runs in under 30 seconds
- [ ] Coverage below the agreed threshold fails the script
- [ ] `CONTRIBUTING.md` documents the pre-push workflow every team member follows

## How to verify

1. `make check` on a clean checkout is green and finishes in under 3 minutes.
2. Break one test: the script stops at the pytest stage and says so.
3. Commit a file containing a fake AWS key: pre-commit blocks it.

## Files touched

- `scripts/ci-local.sh`
- `.pre-commit-config.yaml`
- `pyproject.toml`
- `CONTRIBUTING.md`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #7
