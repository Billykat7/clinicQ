# Milestone 1: Foundation & Local CI

**Status:** 📋 planned · **Phase:** Semester 1 · Sprint 1–2 · **Suggested tag:** `v0.1.0`
**Primary owner:** DevOps/QA Lead (primary) · Backend Lead (support)
**Depends on:** None
**Blocks:** Everything. No other milestone can start until the skeleton, database and test harness exist.

## Goal

Stand up the repository, the Python 3.14 / FastAPI skeleton, the PostgreSQL 18 + PostGIS + Redis dev stack, the migration baseline, the shared UI shell and a fast local test loop, so every later milestone is built on the same foundation instead of six different ones.

## Why this milestone exists

Six people cannot work in parallel on a codebase that does not yet agree on how it boots, where
models live, how migrations run, or what "green" means. M1 is deliberately small in features and
large in conventions: one app factory, one settings object, one enum module, one base template, one
`ci-local.sh`. Everything after this milestone assumes a clinic row can be inserted, a page can be
rendered, and a test can run in under a minute on a student laptop.

This milestone also front-loads the two decisions that are expensive to reverse: **PostGIS in the
baseline migration** (discovery in M5 depends on a `geography(Point, 4326)` column and a GiST index)
and **timezone discipline** (all business datetimes are `Africa/Johannesburg`, stored as UTC).

## Scope

- `uv`-managed Python 3.14 project, `pyproject.toml`, ruff + mypy configuration, app factory and typed settings
- Docker Compose dev stack: PostgreSQL 18 with PostGIS, Redis, and the API container
- SQLAlchemy 2.x async engine/session, Alembic baseline including the `postgis` extension
- Shared enums, error envelope, ID strategy and `Africa/Johannesburg` datetime helpers
- Jinja2 base layout, Tailwind build, htmx/Alpine wiring, design tokens
- Structured JSON logging with request-context middleware, `/health` and `/ready`
- `scripts/ci-local.sh` (ruff → mypy → pytest → docker build) and pre-commit hooks
- Test factories and a `seed_dev_data.py` that produces clinics, queues, staff and tickets

## Issues

| # | Title |
|---|-------|
| 1 | Repository scaffold, Python 3.14 + FastAPI app factory, typed settings |
| 2 | Docker Compose dev stack: PostgreSQL 18 + PostGIS, Redis, API |
| 3 | Async SQLAlchemy 2.x + Alembic baseline (PostGIS extension enabled) |
| 4 | Shared kernel: enums, error envelope, IDs, Africa/Johannesburg datetimes |
| 5 | Base UI shell: Jinja2 layout, Tailwind build, htmx + Alpine wiring |
| 6 | Structured logging, request-context middleware, health & readiness probes |
| 7 | `ci-local.sh` harness (ruff, mypy, pytest, docker build) + pre-commit |
| 8 | Test factories and `seed_dev_data.py` demo dataset |

## Exit criteria

- [ ] `docker compose up` brings up API + Postgres/PostGIS + Redis, and `/health` returns 200
- [ ] `alembic upgrade head` creates the baseline schema with the PostGIS extension present
- [ ] A Jinja2 page renders with Tailwind CSS applied and an htmx fragment swap working
- [ ] `./scripts/ci-local.sh` runs ruff, mypy and pytest green in under 3 minutes on a laptop
- [ ] `uv run scripts/seed_dev_data.py` produces at least 5 clinics with real coordinates, 3 queues each, and 20 tickets
- [ ] Every business datetime helper returns `Africa/Johannesburg`; no naive datetimes pass the lint rule

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M1/)
