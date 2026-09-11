# PR: Prove the database baseline against PostgreSQL, and settle sync versus async (Issue 3 / M1-03)

**Milestone:** [Milestone 1: Foundation & Local CI](https://github.com/Billykat7/clinicQ/milestone/1) ·
**Issue:** [#3](https://github.com/Billykat7/clinicQ/issues/3)

The database layer this issue asks for was already in the kernel: sync and async sessions, a
constraint naming convention, mixins, and a baseline migration that installs PostGIS. What was
missing was proof. The suite builds its schema with `create_all` on SQLite, and a kernel test says
plainly that "nothing in this repository runs the migrations". This PR runs them. Every criterion is
now a test against a real PostgreSQL 18 + PostGIS database that did not exist a moment before the
test started. Decision 5 is settled: new modules use the sync session. Running the migrations for
real also turned up two defects, both fixed here: `alembic upgrade head`, the exact command in the
criteria, failed outright when run bare, and every migration line printed twice.

## Summary

- **Decision 5, settled and written down:** sync `get_db` by default, `get_async_db` for streaming
  handlers (see *Design notes*). It is recorded in `docs/GITHUB/ISSUES/README.md` and in
  `src/database/session.py`; the README, the M1 milestone and the Issue 3 spec now say the same.
- **Throwaway PostgreSQL databases for tests** (`tests/conftest.py`): `empty_database` creates
  `clinicq_test_<uuid7>` on the server named by `TEST_DATABASE_URL` and drops it afterwards;
  `migrated_database` brings it to `head` through the real `alembic/env.py`. Tests carry a new
  `postgres` marker; `make test-postgres` runs them against the compose server.
- **Twelve tests, one or more per criterion:** upgrade from empty with PostGIS, the
  downgrade-to-base-and-back round trip, an empty autogenerate diff, naming conventions (in the
  database and in a freshly generated migration), and sessions that roll back and never leak, both
  sync and async.
- **`alembic upgrade head` works when run bare:** `alembic.ini` gains `prepend_sys_path = .`.
  Before, only `scripts/db/*.sh` worked, because they set `PYTHONPATH`.
- **Every migration line printed once:** the `alembic` and `sqlalchemy` loggers no longer carry a
  handler of their own on top of root's.
- **The rollback is explicit** in both session dependencies, rather than left to `close()`.

## Design notes

**Sync by default (decision 5).** The kernel's RBAC dependencies, tenancy scoping, audit writer and
all eleven routers take a sync `Session`, and FastAPI runs a sync route in its worker pool, so it
never blocks the event loop. "Async everywhere" would mean async twins of all of those before the
first ClinicQ route, for no gain at a clinic's request rate. The async session stays for the
handlers that must not hold a worker for long, the board's server-sent events in M8 first among
them. Alembic stays on the sync engine as well; the spec's "Alembic configured for async" does not
apply.

**A throwaway database per test, on an explicitly named server.** The fixture never guesses a
server. On the machine this was built on, port 5432 is another product's platform database, and a
test suite that creates and drops databases must not find that by accident. So `TEST_DATABASE_URL`
names the server, databases are created with a `clinicq_test_` prefix, and only such a name is ever
dropped (`WITH (FORCE)`, so a leaked connection cannot block the cleanup). Without the variable the
tests skip with the instruction. `REQUIRE_POSTGRES_TESTS=1` turns that skip into a failure, which is
what `make test-postgres` sets, and what CI (Issue 9) should set, so a missing service container
cannot pass as green.

**The migrations run through the real `env.py`.** The fixture hands `env.py` its connection in
`config.attributes["connection"]`, Alembic's cookbook pattern. So the test runs the same code path
as the CLI, against the test database, whatever `DATABASE_URL` a developer's `.env` names. Two
details make that safe inside the test process. `env.py` skips `fileConfig` when asked to
(`configure_logger`), and it never disables existing loggers, so the application's loggers keep
working.

**What each criterion is proven by:**

- *Upgrade on an empty database:* PostGIS is absent before and present in `public` after. Every
  model's table exists, `alembic_version` is at head, and a real
  `ST_Distance(Johannesburg, Durban)` returns about 500 km.
- *Round trip:* up, down to base, up again, each as its own run. After the downgrade the schema has
  no tables and `alembic_version` is empty, while PostGIS stays (it is shared). After the second
  upgrade the same tables are back.
- *Empty autogenerate diff:* `alembic check` passes, and `compare_metadata` returns `[]`.
- *No leak, and rollback:* a pool of 5 with no overflow and a 2-second checkout timeout; a crash and
  a domain error after a flushed write; then 50 failing requests followed by a success. The row is
  never kept, `pool.checkedout()` returns to 0, and `pg_stat_activity` shows no connection idle in a
  transaction.
- *Real PostGIS, not SQLite:* all twelve tests are marked `postgres` and run on PostgreSQL 18.6 with
  PostGIS 3.6.4.
- *Naming convention:* every constraint and index in the schema checked by its prefix, primary keys
  exactly `pk_<table>`. Alembic's own `alembic_version_pkc` is excluded, because it is not ours. A
  migration rendered by autogenerate for a new table carries `pk_`, `fk_…_widget`, `uq_` and `ck_`
  names.

**The tests fail when they should.** `get_db` was broken on purpose (no rollback, no close) and
the suite rerun. Four tests failed: the connection stayed checked out after a failed request, and
after 50 failures the 51st request answered 500 because the pool was exhausted. With the dependency
restored, all twelve pass. On `main`, `close()` already rolled back implicitly, so the behaviour
was right; what changes is that it is now stated, and tested.

**Out of scope:** domain tables (Issues 23, 25, 39) and test factories (Issue 8, which will reuse
these fixtures).

## Changes

- **`alembic.ini`:** `prepend_sys_path = .` (with `path_separator = os`); the `alembic` and
  `sqlalchemy` loggers propagate to root instead of printing twice.
- **`alembic/env.py`:** runs on a connection passed in `config.attributes`, logging configured
  only when asked, existing loggers left enabled.
- **`src/database/session.py`:** decision 5 in the module docstring; explicit rollback in `get_db`
  and `get_async_db`.
- **`tests/conftest.py`:** `postgres_server_url`, `empty_database`, `migrated_database`,
  `migrated_engine`, `run_alembic()`.
- **`tests/integration/database/`** (new): `test_alembic_baseline.py` (6) and
  `test_session_lifecycle.py` (6).
- **`pyproject.toml`:** the `postgres` marker. **`Makefile`:** `make test-postgres`.
- **`scripts/db/alembic-{downgrade,revision}.sh`:** usage text pointed at `scripts/database/`,
  which does not exist.
- **Docs:** decision 5 in `docs/GITHUB/ISSUES/README.md`; the Issue 3 spec, the M1 milestone and the
  README's tech-stack row.

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (160 files)
- [x] With `TEST_DATABASE_URL` set (the compose server, PostgreSQL 18.6 + PostGIS 3.6.4): **897
      passed** (885 on `main` plus 12 new). Without it: 885 passed and the 12 skipped. The same 24
      failures as `main` both times, compared with `diff`
- [x] The spec's *How to verify*, run with the real CLI on a brand-new database
      (`DATABASE_URL=…/clinicq_verify`):

      ```text
      $ make migrate-up
      ✓ DB HOST: localhost  |  PORT: 5433  |  DB: clinicq_verify  |  SCHEMA: clinicq
      INFO  [alembic.runtime.migration] Running upgrade  -> 0001, baseline — every table this project ships with
      $ make migrate-down
      INFO  [alembic.runtime.migration] Running downgrade 0001 -> , baseline — every table this project ships with
      $ make migrate-up
      INFO  [alembic.runtime.migration] Running upgrade  -> 0001, baseline — every table this project ships with
      $ alembic current
      0001 (head)
      $ alembic check
      No new upgrade operations detected.
      $ alembic revision --autogenerate -m "issue3 probe"     # then deleted
      def upgrade() -> None:
          # ### commands auto generated by Alembic - please adjust! ###
          pass
      pg_extension: ['plpgsql', 'postgis']
      postgis_full_version: POSTGIS="3.6.4 94d984b" [EXTENSION] PGSQL="180" GEOS="3.14.1 …
      tables in clinicq: 34
      ```

- [x] Bare `alembic`, before and after: on `main`, `alembic upgrade head` stopped with
      `ModuleNotFoundError: No module named 'src'`; now `alembic current` answers `0001 (head)`.
      Each `INFO [alembic.runtime.migration] …` line printed twice before, and prints once now
- [x] The broken-`get_db` run above: 4 failed, 2 passed; restored, 6 of 6 pass
- [ ] Linux: run on macOS with the compose image under emulation. Issue 9's workflow runs the same
      image, and should set `TEST_DATABASE_URL` and `REQUIRE_POSTGRES_TESTS=1`

## Acceptance criteria

- [x] `alembic upgrade head` on an empty database succeeds and installs PostGIS (a test, and the CLI
      above)
- [x] `alembic downgrade base` then `upgrade head` round-trips without error (a test, and
      `make migrate-down` then `make migrate-up`)
- [x] Autogenerate produces a clean (empty) diff immediately after an upgrade (`alembic check`, an
      empty generated revision, `compare_metadata == []`)
- [x] The session dependency rolls back on an exception and never leaks a connection (sync and async;
      50 failures on a pool of 5)
- [x] Tests run against a real PostGIS database, not SQLite (the 12 `postgres` tests)
- [x] Constraint names follow the documented convention in every generated migration (the schema's
      constraints and indexes, and a freshly rendered migration)

## Risk and rollback

The runtime change is small: both session dependencies call `rollback()` before `close()`, which
`close()` already did implicitly. The Alembic changes affect only how the CLI finds the code and
prints its log. The new tests are skipped wherever no PostgreSQL is named, so `make test` behaves as
before. No migration. Rollback is a revert of this PR.

**Follow-ups found along the way:**

- Issue 9 should run `make test-postgres` in CI against the same `postgis/postgis:18-3.6` service
  container, with `REQUIRE_POSTGRES_TESTS=1`. Until then these tests run only where a developer
  starts the stack.

Closes #3
