# PR: Run PostgreSQL 18 + PostGIS and Redis as one compose dev stack (Issue 2 / M1-02)

**Milestone:** [Milestone 1: Foundation & Local CI](https://github.com/Billykat7/clinicQ/milestone/1) ·
**Issue:** [#2](https://github.com/Billykat7/clinicQ/issues/2)

One command now gives every teammate the same PostgreSQL 18 + PostGIS 3.6 database and Redis 8, and
the API container waits for both to be healthy. Before this branch, the dev database was PostgreSQL 16
while every spec and the shared platform database said 18. Redis existed only in the production
compose file, and there was no `.env.example`. This PR settles decision 4 at 18 and makes the compose
files, the CI smoke test and the specs agree, with a guard test so they keep agreeing. Every acceptance
criterion was demonstrated on a clean checkout rather than ticked from reading the files; the outputs
are in *Testing*.

## Summary

- **Decision 4, PostgreSQL 18:** `postgis/postgis:18-3.6` replaces `16-3.4`, matching the major and
  minor versions of the shared platform database. The volume mount moves with it (see *Design notes*).
- **One compose project:** `docker-compose.db.yml` defines `db` and `redis` once;
  `docker-compose.yml` includes it and adds the API and nginx. Every `make` target drives the same
  containers and volumes.
- **Redis 8 with a health check**, and the API waits for `db` and `redis` to report healthy.
- **`.env.example`** with `DATABASE_URL` and `REDIS_URL`. The API container gets the same URLs with
  the host swapped for the service name.
- **Shortcuts:** `make dev` (`scripts/dev.sh`) brings the infrastructure up and runs the API with
  reload; `make db-reset` wipes and recreates the stack.
- **CI agrees:** the `ci-local.sh --compose` smoke test now checks PostGIS and Redis in a project of
  its own. Issue 9's spec names the same image, and a guard test fails if a second PostgreSQL version
  appears in a compose file or workflow.

## Design notes

**18, not 16 (decision 4).** The shared platform database runs PostgreSQL 18.6 with PostGIS 3.6.4, and
`postgis/postgis:18-3.6` is exactly that today. So a query that works on a laptop works where the
product runs. Staying on 16 would have meant developing against a version nothing else runs, and giving
up what 18 adds, such as a native `uuidv7()` (checked below). The decision is recorded in
`docs/GITHUB/ISSUES/README.md`, and the specs it affects (2, 9, 102) now say 18 and name the image.

**The volume mount had to move with the tag.** PostgreSQL 18 images keep their data in
`/var/lib/postgresql/18/docker` and declare the volume at `/var/lib/postgresql`. With only the tag
bumped and the mount left at `/var/lib/postgresql/data`, the container exits with status 1 on every
start, even with an empty volume (reproduced below). A one-line tag change would have broken
`make db-up` for the whole team.

**Existing PostgreSQL 16 data is left alone.** PostgreSQL 18 cannot open a 16 data directory. The
project name changes from `clinicq-db` to `clinicq`, so the new stack starts in a new volume
(`clinicq_pgdata`) and never touches the old one (`clinicq-db_clinicq_pgdata`). Local data is
rebuilt by migrations and seeds, so there is no `pg_upgrade` step. The README explains how to remove
the old project, and warns that its container keeps holding port 5432 until you do.

**amd64 on Apple Silicon.** `postgis/postgis:18-3.6` is published for `linux/amd64` only. Without a
`platform:` line, pulling it on an arm64 Mac fails with *no matching manifest*, so the compose file
names the platform and Docker Desktop emulates it. The cold start is still 12 seconds. The
alternative was to build our own image from `postgres:18` plus PostGIS from apt, which runs natively on
arm64. It was rejected because CI service containers need an image they can pull, and using one image
everywhere is what keeps a single version in play.

**Defined once, one project.** `docker-compose.yml` uses `include:` rather than repeating `db` and
`redis`, and both files carry `name: clinicq`. That way `docker compose up -d db redis` (the acceptance
criterion), `make db-up`, `make dev` and `make docker-up` all share one set of containers and
volumes. This needs Compose 2.24 or newer (`include`, an optional `env_file`, `start_interval`); the
README says so, and `ci-local.sh` no longer falls back to the legacy `docker-compose` v1 binary, which
cannot read the file.

**Health checks that mean ready.** `pg_isready` runs over TCP. On first boot the entrypoint runs a
temporary server on the Unix socket only while it installs PostGIS, and a socket check calls that
server ready (the logs below show both servers). The Redis check matches `PONG`, because `redis-cli`
exits 0 on an error reply such as `LOADING` (checked). `start_interval: 1s` lets `--wait` return in
seconds rather than after the first 10-second interval.

**Same URL shape, host swapped.** The API container overrides `DATABASE_URL` and `REDIS_URL` with
hosts `db` and `redis`; everything else still comes from `.env`, which is now optional, so the file
validates on a fresh clone. Two overrides were removed. `SECRET_KEY` is not a setting (the app reads
`JWT_SECRET`), and the `LOG_LEVEL` override silently beat any `LOG_LEVEL` in `.env`.

**Backing services on loopback.** `db` and `redis` publish on `127.0.0.1`: the database password is a
development default and Redis has none. nginx keeps its previous binding.

**The smoke test could have wiped the dev database.** `ci-local.sh --compose` starts with `down -v`.
Once the database lived in `docker-compose.yml`'s project, that line would have deleted every
developer's local data. The smoke test now runs as its own project, `clinicq-smoke`, on ports 18000,
15432 and 16379; the run below had the dev stack up alongside it the whole time.

**`.env.example` is tracked explicitly.** `.gitignore` gains `!.env.example`, because a common global
rule (`.env.*`, present on the author's machine) would otherwise keep the template out of commits
without any warning. Nothing reads `REDIS_URL` yet; the Redis readiness check in Issue 6 will be the
first. `Settings` ignores unknown keys, so no field is added here.

**Out of scope:** the production compose file and deploys (Issue 11), seed data (Issue 8), the rest of
`.env.example` (Issue 12), and the GitHub Actions workflow itself (Issue 9). Issue 9's spec now names
the image, and the guard test will hold the workflow to it.

## Changes

- **`infra/docker/docker-compose.db.yml`:** `db` on `postgis/postgis:18-3.6` (`platform: linux/amd64`,
  volume at `/var/lib/postgresql`, TCP health check); new `redis` (`redis:8-alpine`, `/data` volume,
  `PONG` health check); both on loopback; project `clinicq`.
- **`infra/docker/docker-compose.yml`:** includes the file above; `app` waits for healthy `db` and
  `redis`, gets service-name URLs and an optional `env_file`, and polls liveness every second at
  start; nginx waits for a healthy `app`; `HTTP_PORT` override.
- **`.env.example`** (new) and **`.gitignore`** (`!.env.example`).
- **`Makefile`:** `dev`, `db-reset`; `db-up` starts `db` and `redis` with `--wait`; `docker-up`
  builds and waits; every target uses one `COMPOSE_DEV` command.
- **`scripts/dev.sh`** (new); **`scripts/run-local.sh`** on the new services;
  **`scripts/ci-local.sh`** smoke test isolated, with PostGIS and Redis checks.
- **`tests/unit/platform/test_dev_stack_compose.py`** (new): six offline guard tests. One PostgreSQL
  image across compose files and workflows, and it is 18; the volume path; health-gated start;
  loopback ports; `.env.example` matching the compose defaults; the container URLs matching it with
  only the host swapped.
- **`README.md`:** *Getting started* uses `cp .env.example .env`; a new *The local Docker stack*
  section covers services, ports, reset, the PostgreSQL 16 upgrade and Apple Silicon.
- **Specs:** decision 4 recorded in `docs/GITHUB/ISSUES/README.md`; Issue 2's starting point, scope
  path (`infra/docker/`), *How to verify* and files; Issues 9 and 102 name 18 and the image.
- **`57f98b8`:** the M1 IDE prompts forbid co-author trailers in commits.

## Testing

Everything below ran from a clean worktree of this branch, with `.env` created by
`cp .env.example .env`. On this machine ports 5432 (the platform database), 6379 and 8000 were
already taken, so the ports were moved the way the README documents: `DB_PORT=5433`,
`REDIS_PORT=6380` and `HTTP_PORT=8080` in `infra/docker/.env`, and the same ports in `.env`.

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (153 files)
- [x] `make test`: 682 passed (676 on `main` plus the 6 new). 24 fail (13 failures, 11 errors), the
      same 24 as on `main`, compared test by test: guard tests for files later issues create
      (`.github/workflows/`, `.gitleaks.toml`, `docs/SECURITY/`)
- [x] The guard tests fail when they should. Against the pre-Issue 2 settings (the `16-3.4` image,
      the `/data` mount, Redis on every interface, `service_started`, a `localhost` container URL),
      5 of 6 fail, each naming the problem. A workflow with a `postgres:17` service container is
      reported as a second version in play
- [x] `docker compose config`: exit 0 with nothing on stderr, with no `.env`, with `.env` from the
      template, and run from `infra/docker`
- [x] Cold start with fresh volumes: `make db-up` healthy in **12.2 s**; the literal
      `docker compose up -d --wait db redis` from `infra/docker` in **8.5 s**
- [x] PostGIS from the host through `$DATABASE_URL` (the app's driver; the host has no `psql`):

      ```text
      DATABASE_URL=postgresql://btk_user:change-me@localhost:5433/btk
      SELECT postgis_version()  -> 3.6 USE_GEOS=1 USE_PROJ=1 USE_STATS=1
      postgis_full_version()    -> POSTGIS="3.6.4 94d984b" [EXTENSION] PGSQL="180" ...
      SHOW server_version       -> 18.6 (Debian 18.6-1.pgdg13+2)
      SELECT uuidv7()           -> 01a08d4c-6192-7b07-adc5-c8d4ff09b658
      redis-cli -u $REDIS_URL ping -> PONG   (redis_version:8.10.1)
      ```

- [x] Health check timing, from the `db` logs: the temporary socket-only server was ready at
      21:49:30 and the real server at 21:49:36. The TCP check passed only after the second
- [x] `make migrate-up` on PostgreSQL 18 created all 34 tables in `clinicq` (Issue 3's baseline
      unchanged); `make seed-rbac` seeded 2 roles, 26 resources, 7 actions and 107 permissions
- [x] Same URL shape, host-run API (`uvicorn` on :8002) and API container (nginx on :8080):

      ```text
      host .env      DATABASE_URL=postgresql://btk_user:change-me@localhost:5433/btk
      app container  DATABASE_URL=postgresql://btk_user:change-me@db:5432/btk
      app container  REDIS_URL=redis://redis:6379/0
      host  /health/ready -> 200 {"database":"ok","migrations":"ok","storage":"skipped"}
      nginx /health/ready -> 200 {"database":"ok","migrations":"ok","storage":"skipped"}
      ```

- [x] Seeded data survives a restart. `make db-down` removes the containers (volumes kept), and
      `make db-up` creates new ones (db `4fc21eb9286f` then `bac03f32523b`):

      ```text
      BEFORE  tables 34, rbac_role 2, resources 26, actions 7, permissions 107, role_permission 7
              redis issue2:probe = 'written before the restart'
      AFTER   tables 34, rbac_role 2, resources 26, actions 7, permissions 107, role_permission 7
              redis issue2:probe = 'written before the restart'
      ```

- [x] Wipe and recreate: `make db-reset` in **13.1 s**. Both volumes were recreated (created at
      21:52:26, previously 21:49:25), leaving 0 tables in `clinicq`, the Redis key gone and
      `postgis_version()` back at `3.6`
- [x] `make dev`: without `.env` it exits 1 with "No .env found. Create one from the template first";
      with it, both services turn healthy, then Uvicorn runs with the reloader and `/health/ready`
      answers 200. It activated `.venv` itself from a shell with no venv
- [x] CI smoke test (`ci-local.sh --compose` step 4, run as committed with steps 1 to 3 stubbed,
      because the 24 existing failures stop `make check-compose` at step 2): passed in 19 s with
      `18.6`, PostGIS `3.6`, `PONG` and `/health/live` 200. It ran while the dev stack was up, and
      the dev containers kept their ids, their 34 tables and a Redis marker key; no `clinicq-smoke`
      containers or volumes were left behind
- [x] The mount-path trap: `postgis/postgis:18-3.6` with a volume at `/var/lib/postgresql/data` exited
      with status 1 on an empty volume, the entrypoint reporting data "in: /var/lib/postgresql/data
      (unused mount/volume)"
- [x] Upgrade path: the old `clinicq-db` project on `16-3.4` ran next to the new stack without
      touching it, and `docker compose -p clinicq-db down -v`, run with no compose file, removed only
      the old container and volume
- [ ] Linux and WSL: run on macOS (arm64, Docker 29.6, Compose 5.3) only. The image is amd64, so
      Linux and WSL on amd64 run it natively without the emulation used here; Issue 9's workflow
      will run the same stack on Linux
- [ ] Screenshot: no UI change

## Acceptance criteria

- [x] `docker compose up -d db redis` gives a working PostGIS database and Redis in under 60 seconds
      (8.5 s with the literal command, 12.2 s through `make db-up`, both from fresh volumes)
- [x] `SELECT postgis_version();` succeeds against the compose database (`3.6`, on PostgreSQL 18.6)
- [x] The API container and a locally-run API both connect using the same `DATABASE_URL` shape
      (`@localhost:5433/btk` and `@db:5432/btk`; both `/health/ready` 200 against one database)
- [x] Stopping and restarting compose preserves seeded data (containers removed and recreated;
      every count and the Redis key unchanged)
- [x] A documented one-liner wipes and recreates the stack from scratch (`make db-reset`, in the
      README with what it runs)
- [x] Compose file passes `docker compose config` with no warnings (no `.env`, template `.env`, and
      from `infra/docker`)

## Risk and rollback

Only development files change; the production compose file is untouched. The visible effect: after
pulling this, a developer's next `make db-up` starts an **empty** PostgreSQL 18 database, so they run
`make migrate-up` (and the seeds) once. Their PostgreSQL 16 volume is untouched, but its container
holds port 5432 until they remove it as the README describes. Rollback is a revert of this PR; since the
old volume still exists, reverting brings the PostgreSQL 16 data back as well.

**Follow-ups found along the way:** Compose interpolates `$` inside the `.env` that the API container
reads, so a value containing `$` loses part of itself under `make docker-up`. This was already the
case before this change; the author's `.env` shows it as two "variable is not set" warnings. Issue 12
should document escaping it as `$$`. Also, `ci-local.sh` sets four `*_PASSED` flags it never reads
(shellcheck SC2034), which was already true before this change.

Closes #2
