# Issue 2: Docker Compose dev stack: PostgreSQL 18 + PostGIS, Redis, API

> **In short:** One command gives every teammate the same PostGIS database and Redis, so nobody debugs a local install instead of the product.

| | |
|---|---|
| **Milestone** | [M1: Foundation & Local CI](../../MILESTONES/M1_foundation_local_ci.md) |
| **Sprint** | 1 (weeks 1–2) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Infra / Foundation |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 1](../M1/ISSUE_1_repo_scaffold_app_factory.md): Repository scaffold, Python 3.14 + FastAPI app factory, typed settings |
| **Unblocks** | [Issue 3](../M1/ISSUE_3_sqlalchemy_alembic_baseline.md): Async SQLAlchemy 2.x + Alembic baseline (PostGIS extension enabled)<br>[Issue 43](../M6/ISSUE_43_recall_noshow_timers.md): Recall timers and automatic no-show transitions (`arq` jobs) |

## Context

Discovery (M5) needs PostGIS and the queue engine (M6) needs Redis for caching and `arq` jobs. Both
must be available with one command so nobody spends a sprint installing a spatial database by hand.

## Starting point

- `infra/docker/docker-compose.db.yml` (`make db-up`) already runs PostGIS, and `infra/docker/docker-compose.yml` runs the full stack (`make docker-up`).
- The compose image is `postgis/postgis:16-3.4`; the spec asks for PostgreSQL 18. Upgrade the image or amend the spec; see [open decisions](../README.md#open-decisions).
- Redis is only in the production compose file today, and there is no tracked `.env.example` yet (Issue 12 owns the full file; this issue adds the two URLs).

## Scope

- `infra/docker-compose.yml` with `db` (PostgreSQL 18 + PostGIS), `redis`, and an optional `api` service
- Named volumes so data survives a restart, and a documented reset command
- Health checks on `db` and `redis` so the API waits for a ready database
- `.env.example` entries for `DATABASE_URL` and `REDIS_URL` pointing at the compose services
- A `make dev` / `scripts/dev.sh` shortcut that brings infra up and runs the API with reload

## Out of scope

- The production compose file and deploys (Issue 11).
- Seed data (Issue 8).
- Every other setting in `.env.example` (Issue 12).

## Acceptance criteria

- [ ] `docker compose up -d db redis` gives a working PostGIS database and Redis in under 60 seconds
- [ ] `SELECT postgis_version();` succeeds against the compose database
- [ ] The API container and a locally-run API both connect using the same `DATABASE_URL` shape
- [ ] Stopping and restarting compose preserves seeded data
- [ ] A documented one-liner wipes and recreates the stack from scratch
- [ ] Compose file passes `docker compose config` with no warnings

## How to verify

1. `make db-up`, then `psql $DATABASE_URL -c 'SELECT postgis_version();'` returns a version.
2. `docker compose -f infra/docker/docker-compose.yml config` prints no warnings.
3. Stop and start the stack: data seeded before the restart is still there.

## Files touched

- `infra/docker/docker-compose.db.yml`
- `infra/docker/docker-compose.yml`
- `.env.example`
- `Makefile`
- `README.md`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #2
