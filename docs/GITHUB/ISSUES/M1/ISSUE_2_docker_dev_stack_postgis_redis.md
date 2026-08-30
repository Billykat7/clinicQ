# Issue 2: Docker Compose dev stack: PostgreSQL 18 + PostGIS, Redis, API

**Area:** Infra / Foundation
**Milestone:** M1 - Foundation & Local CI
**Owner role:** DevOps/QA Lead
**Depends on:** Issue 1
**Estimate:** 1 day
**Status:** Planned

## Context

Discovery (M5) needs PostGIS and the queue engine (M6) needs Redis for caching and `arq` jobs. Both
must be available with one command so nobody spends a sprint installing a spatial database by hand.

## Scope

- `infra/docker-compose.yml` with `db` (PostgreSQL 18 + PostGIS), `redis`, and an optional `api` service
- Named volumes so data survives a restart, and a documented reset command
- Health checks on `db` and `redis` so the API waits for a ready database
- `.env.example` entries for `DATABASE_URL` and `REDIS_URL` pointing at the compose services
- A `make dev` / `scripts/dev.sh` shortcut that brings infra up and runs the API with reload

## Acceptance criteria

- [ ] `docker compose up -d db redis` gives a working PostGIS database and Redis in under 60 seconds
- [ ] `SELECT postgis_version();` succeeds against the compose database
- [ ] The API container and a locally-run API both connect using the same `DATABASE_URL` shape
- [ ] Stopping and restarting compose preserves seeded data
- [ ] A documented one-liner wipes and recreates the stack from scratch
- [ ] Compose file passes `docker compose config` with no warnings

## Files touched

- `infra/docker-compose.yml`
- `.env.example`
- `scripts/dev.sh`
- `README.md`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #2
