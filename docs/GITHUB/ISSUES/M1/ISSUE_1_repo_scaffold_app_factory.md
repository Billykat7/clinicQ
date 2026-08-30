# Issue 1: Repository scaffold, Python 3.14 + FastAPI app factory, typed settings

**Area:** Backend / Foundation
**Milestone:** M1 - Foundation & Local CI
**Owner role:** DevOps/QA Lead
**Depends on:** None
**Estimate:** 2 days
**Status:** Planned

## Context

Nothing can be built until the project boots the same way on six laptops. This issue creates the
`uv`-managed Python 3.14 project, the FastAPI application factory, typed settings loaded from the
environment, and the lint/type-check configuration every later issue is measured against.

## Scope

- `pyproject.toml` managed by `uv`, pinned to Python 3.14, with runtime and dev dependency groups
- `app/main.py` application factory (`create_app()`) mounting routers, middleware and static files
- `app/core/config.py`: a Pydantic `Settings` object with typed, documented fields and no bare `os.getenv` anywhere else
- `ruff` and `mypy` configuration in strict-enough mode to be useful without blocking a student team
- Repository layout as specified in the tech-implementation doc (`app/`, `channels/`, `workers/`, `migrations/`, `tests/`, `scripts/`)

## Acceptance criteria

- [ ] `uv sync` installs cleanly on macOS, Linux and WSL with Python 3.14
- [ ] `uv run uvicorn app.main:app` starts and serves a placeholder route
- [ ] `create_app()` is importable and used by both the server and the test client
- [ ] `ruff check` and `mypy app` both pass on the empty skeleton
- [ ] Settings raise a clear startup error when a required variable is missing
- [ ] `README.md` documents the five commands a new team member needs on day one

## Files touched

- `pyproject.toml`
- `app/main.py`
- `app/core/config.py`
- `ruff.toml / pyproject [tool.ruff]`
- `README.md`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #1
