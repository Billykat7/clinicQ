# Issue 1: Repository scaffold, Python 3.14 + FastAPI app factory, typed settings

> **In short:** The empty-but-running application every other issue builds inside: one app factory, one typed settings object, and a layout the whole team agrees on.

| | |
|---|---|
| **Milestone** | [M1: Foundation & Local CI](../../MILESTONES/M1_foundation_local_ci.md) |
| **Sprint** | 1 (weeks 1–2) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Backend / Foundation |
| **Estimate** | 2 days |
| **Status** | In progress (branch `Issue/1/repo-scaffold-initial-setup`) |
| **Depends on** | Nothing: this can start on day one. |
| **Unblocks** | [Issue 2](../M1/ISSUE_2_docker_dev_stack_postgis_redis.md): Docker Compose dev stack: PostgreSQL 18 + PostGIS, Redis, API<br>[Issue 4](../M1/ISSUE_4_shared_kernel_enums_time_errors.md): Shared kernel: enums, error envelope, IDs, Africa/Johannesburg datetimes<br>[Issue 5](../M1/ISSUE_5_base_ui_shell_tailwind_htmx.md): Base UI shell: Jinja2 layout, Tailwind build, htmx + Alpine wiring<br>[Issue 6](../M1/ISSUE_6_logging_request_context_health.md): Structured logging, request-context middleware, health & readiness probes<br>[Issue 7](../M1/ISSUE_7_ci_local_harness_precommit.md): `ci-local.sh` harness (ruff, mypy, pytest, docker build) + pre-commit<br>[Issue 12](../M2/ISSUE_12_env_matrix_config_validation.md): Environment matrix, `.env.example` and fail-fast config validation |

## Context

Nothing can be built until the project boots the same way on six laptops. This issue creates the
Python 3.14 project, the FastAPI application factory, typed settings loaded from the environment,
and the lint/type-check configuration every later issue is measured against.

## Starting point

- The scaffold is already committed: `src/main.py` (app factory), `src/core/config.py` (Pydantic settings with production guards), `pyproject.toml` (ruff, mypy and pytest config) and the `src/` layout.
- The spec was first written for `uv` and an `app/` package. **Decided in this issue** (decision 3 in [open decisions](../README.md#open-decisions)): the project keeps `requirements.txt` with setuptools and the `src/` package, and the scope and criteria below describe that layout.
- What is left is mostly checking the acceptance criteria against what exists and documenting the day-one commands in `README.md`.

## Scope

- `pyproject.toml` pinned to Python 3.14 (`requires-python`), with dependencies in `requirements.txt`
- `src/main.py` application factory (`create_app()`) mounting routers, middleware and static files
- `src/core/config.py`: a Pydantic `Settings` object with typed, documented fields and no bare `os.getenv` anywhere else
- `ruff` and `mypy` configuration in strict-enough mode to be useful without blocking a student team
- Repository layout: `src/`, `alembic/`, `tests/`, `scripts/`, `infra/` (see *Where code goes* in the [issues guide](../README.md#where-code-goes-in-this-repository))

## Out of scope

- Docker and the database stack (Issue 2).
- Database sessions and migrations (Issue 3).
- Domain enums and the error envelope (Issue 4).

## Acceptance criteria

- [ ] `pip install -r requirements.txt` installs cleanly into a fresh Python 3.14 virtualenv on macOS, Linux and WSL
- [ ] `make run` (`uvicorn src.main:app`) starts and serves a placeholder route
- [ ] `create_app()` is importable and used by both the server and the test client
- [ ] `ruff check .` and `mypy src/` both pass
- [ ] Settings raise a clear startup error when a required variable is missing
- [ ] `README.md` documents the five commands a new team member needs on day one

## How to verify

1. Follow *Getting started* in `README.md` on a fresh clone: `pip install -r requirements.txt` into a new Python 3.14 virtualenv, then `make run` serves `/health`.
2. Start with `ENVIRONMENT=production` and no `JWT_SECRET`: startup fails with a message naming `JWT_SECRET` (in development every setting has a safe default, so nothing else is required).
3. `make lint` and `mypy src/` pass.

## Files touched

- `src/main.py`
- `src/core/config.py`
- `pyproject.toml`
- `requirements.txt`
- `README.md`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #1
