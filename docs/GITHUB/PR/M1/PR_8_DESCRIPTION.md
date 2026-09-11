# PR: Factories, a demo world of real clinics, and a seed that refuses production (Issue 8 / M1-08)

**Milestone:** [Milestone 1: Foundation & Local CI](https://github.com/Billykat7/clinicQ/milestone/1) ·
**Issue:** [#8](https://github.com/Billykat7/clinicQ/issues/8)

Every teammate now builds test data in one line, and every laptop can seed the same demo world with
one command. The world is eleven real clinics in Gauteng and KwaZulu-Natal, placed where
OpenStreetMap says they are, because discovery (Issue 31) and the map (Issue 33) will be judged
against them. Each clinic has two to four queues and a believable day of tickets. The seed script
is idempotent and refuses any database that is not a local development one. Both were proven by
running it: a second run created nothing, and a production URL was rejected before any connection
was made.

The honest limit: the sites, queues, tickets and patients tables do not exist until Issues 23, 25,
39 and 17. So the factory for staff is real and persists rows, and the others build **agreed
stubs**: dataclasses carrying exactly the fields those specs name, typed with the Issue 4 enums.
The seed writes staff today. It builds and reports the clinics, queues and history, and each of
those issues adds its own write step.

![The eleven demo clinics on OpenStreetMap](https://github.com/Billykat7/clinicQ/blob/7d102a7a29d6e9a5703bacb30a7ff0d185b35907/docs/GITHUB/PR/M1/assets/pr8/clinics-map.jpg?raw=true)

*The dataset plotted on OpenStreetMap tiles with the project's vendored Leaflet (green public, red
private). Issue 33's map does not exist yet, so this is a one-off plot of
`scripts/db/demo_dataset.py`, not an app page.*

## Which factories are real, and which are stubs

| Factory | Builds | Persists | Until |
|---------|--------|----------|-------|
| `StaffFactory` | `User`, verified and active, with a ClinicQ staff role and its role assignments | **yes**, `create(session)` | (real now) |
| `PatientFactory` | `PatientStub`: E.164 phone, display name, consent flags | no | Issue 17 (`patients`) |
| `SiteFactory` | `SiteStub`: the real clinics in turn | no | Issue 23 (`sites`) |
| `QueueFactory` | `QueueStub`: name, ticket prefix, pace, rooms | no | Issue 25 (`queues`) |
| `TicketFactory` | `TicketStub`: sequence, number, source, status, SAST timestamps | no | Issue 39 (`tickets`) |

When one of those issues lands, its author points the factory's `model` at the new class and adds
`create`. The field names already match the spec, so no caller changes.

## Summary

- **`tests/factories.py`:** a small typed factory framework (`build`, `build_batch`, per-factory
  sequences so unique fields stay unique, any field overridden by keyword), with the five factories
  above. It works with the SQLite `session_factory` fixture and the PostgreSQL fixtures from Issue 3.
- **`scripts/db/demo_dataset.py`:** the eleven clinics (8 public, 3 private; 7 in Gauteng, 4 in
  KwaZulu-Natal), each with its OpenStreetMap element. Also the queue plans (4 at a community health
  centre, 3 at a clinic, 2 at a private practice) and a deterministic ticket-history generator:
  3,574 tickets by 11:30 across 34 queues.
- **`scripts/db/seed_dev_data.py`** (`make seed-dev-data`): one staff account per ClinicQ staff role,
  printed with its development-only password. Everything is matched by a natural key: created,
  updated or left unchanged, never duplicated. It refuses unless `ENVIRONMENT=development`, the host
  is local (`localhost`, `127.0.0.1`, `::1`, `db`) and the database name does not contain `prod`.
- **Tests:** 39 on the dataset, 6 on the factories, 6 on the seed (refusal and two idempotence
  cases, which run the script for real).

## Design notes

**Real coordinates, read rather than remembered.** Every clinic's name, position and operator comes
from OpenStreetMap, queried through Nominatim and Overpass on 2026-09-11, and the dataset records
the OSM element (`way/1027130595` for Mandela Sisulu Clinic, for example). Verifying caught two
problems before they shipped. Hillbrow CHC has no operator tag, so its operator (the Gauteng
Department of Health) is marked as not coming from OSM. And an early candidate for a private
clinic, Unjani Clinic Tembisa, is tagged *public* in OSM; it was replaced by Randburg Medicross,
whose private operator OSM records. Data is © OpenStreetMap contributors, ODbL, attributed in the
module. Opening hours and queues are demo values and say so.

**Believable history, not random noise.** Arrivals are busiest in the first two hours (the queue
that forms before the doors open), and each queue has rooms serving at its own pace. Public clinics
run slightly over capacity in the morning rush; private practices, which book, never do. Waits
therefore rise and fall through the day. By 11:30, medians range from about 2 minutes at a quiet
private reception to about 98 at a public triage, which gives an estimator (Issue 42) and a heatmap
(Issue 90) something real to chew on. Calls always follow the sequence, which is non-negotiable 1:
a test caught two rooms calling tickets 1 and 2 out of order a few seconds apart, and the generator
now forbids it. The same site, queue and day always produce the same tickets.

**A refusal that cannot be talked round.** The guard reads only the settings and runs before
anything connects, so a production URL fails in milliseconds with exit code 2, not with a
connection error. There is deliberately no `--force`: seeding accounts with a published password
into a shared database is never the intent. The development password (`clinicq-dev-only`, or
`SEED_STAFF_PASSWORD`) is printed under a "DEVELOPMENT ONLY" banner. The accounts use the reserved
`.test` domain, so no mail can leave. Their permissions arrive with Issue 18; until then they can
sign in and do nothing else, and the script says so.

**Idempotent means "updates, never duplicates".** A second run on an untouched database reports
`0 created, 0 updated, 4 unchanged`. If someone demotes or deactivates a seeded account, the next
run puts it back in place (`1 updated`), and the row count never moves. The unscoped role
assignment RBAC resolves users by is added if it is missing, the same way the kernel's baseline
backfills it.

**No factory_boy.** A dozen typed lines cover what the tests need, with no global session (the
session is passed in) and return types mypy understands. Adding a dependency for that was not worth
it. The shared data lives in `scripts/db/`, so the seed script never imports test code.

**Out of scope:** production data (never), and the area and suburb dataset (Issue 34).

## Changes

- **`tests/factories.py`** (new): `Factory[T]`, `StaffFactory`, `PatientFactory`, `SiteFactory`,
  `QueueFactory`, `TicketFactory`.
- **`scripts/db/demo_dataset.py`** (new): `SiteStub`, `QueueStub`, `TicketStub`, `PatientStub`,
  `Province`, `CLINICS`, `queues_for()`, `ticket_history()`.
- **`scripts/db/seed_dev_data.py`** (new) and **`Makefile`** (`make seed-dev-data`).
- **Tests (new):** `tests/unit/seed/test_demo_dataset.py`,
  `tests/integration/database/test_factories.py`, `tests/integration/database/test_seed_dev_data.py`.
- **Docs:** `README.md` (demo data under *Getting started*), `CONTRIBUTING.md` (the seed in the
  setup, the factories), the Issue 8 spec (stubs, `make seed-dev-data`, files touched), and the map
  in `docs/GITHUB/PR/M1/assets/pr8/`.

## Testing

- [x] `ruff check .`, `ruff format --check .`, `mypy src/` (160 files) and every pre-commit hook
      clean; the conventions guard (Issue 4) scans `scripts/db/` and passes
- [x] `make check-fast`: green in 27 s, coverage 76.9%. With `TEST_DATABASE_URL`: **949 passed**
      (898 on `main` with its PostgreSQL tests, plus 51 new); without it, 935 passed and the 14
      PostgreSQL tests skipped. The 23 pending guards are unchanged
- [x] **A second run creates no duplicates**, and a production URL is rejected, live against the
      compose database:

      ```text
      $ make seed-dev-data   # first run, 2.1 s
      Seeding localhost:5433/btk (schema clinicq)
      Staff accounts: 4 created, 0 updated, 0 unchanged

      DEVELOPMENT ONLY. These accounts exist to click around a local database:
        platform_admin  platform-admin@clinicq.test    clinicq-dev-only
        clinic_manager  manager@clinicq.test           clinicq-dev-only
        receptionist    reception@clinicq.test         clinicq-dev-only
        nurse_doctor    nurse@clinicq.test             clinicq-dev-only
        Their permissions arrive with Issue 18 (RBAC); until then they can sign in and no more.

      Demo dataset (scripts/db/demo_dataset.py): 11 clinics in Gauteng and KwaZulu-Natal, 34 queues,
      3574 tickets by 11:30 today (3034 done; busiest: Imbalenhle Community Health Centre: Chronic
      medication collection, 155 done).

      $ make seed-dev-data   # second run
      Staff accounts: 0 created, 0 updated, 4 unchanged

      $ DATABASE_URL=postgresql://clinicq:***@db.prod.clinicq.co.za:5432/clinicq make seed-dev-data
      seed_dev_data: refused. DATABASE_URL points at db.prod.clinicq.co.za/clinicq, which is not a
      local development database (127.0.0.1, ::1, db, localhost).
      seed_dev_data: nothing was touched.
      make: *** [seed-dev-data] Error 2

      rows in clinicq.user for @clinicq.test: 4
      ```

- [x] The same proofs as tests that run the script in a subprocess: refused (exit 2, nothing on
      stdout) for a production host, a remote IP, a database named `clinicq_prod` and
      `ENVIRONMENT=production`. On a fresh migrated PostgreSQL database, two runs report 4 created,
      then 4 unchanged, in under 30 s, with 4 users and 4 role assignments. A demoted account is
      repaired in place (`1 updated`, still 4 rows)
- [x] The dataset: at least 8 clinics across both provinces and sectors; every coordinate inside
      its province; unique slugs, names, positions and OSM elements; 2 to 4 queues each; tickets
      sequenced from 1, SAST-aware and called in order; every outcome present; one queue with 30+
      finished tickets whose waits vary (standard deviation over 5 minutes); a morning rush busier
      than the afternoon
- [x] The factories: a verified receptionist with a working password and its role assignment from
      a bare `StaffFactory.create(db)`; overrides and a site scope apply; 20 of each factory's
      objects stay unique
- [ ] The clinics on the product's own map: Issue 33. The plot above uses the same tiles and
      coordinates

## Acceptance criteria

- [x] The seed script populates a fresh database in under 30 seconds (2.1 s live; asserted under 30
      s in the test). It runs as `make seed-dev-data`, since the project does not use `uv`
      (decision 3). *Populates* means the staff accounts today; clinics, queues and tickets follow as
      Issues 23, 25 and 39 add their tables
- [x] The seeded clinics appear at sensible real-world coordinates on a map (the plot above; each
      clinic traceable to its OpenStreetMap element)
- [x] Factories produce valid objects with a single call and no required arguments
- [x] Re-running the seed script does not create duplicate clinics: nothing is duplicated, proven
      for the staff it writes today. The clinic step, keyed by slug, arrives with Issue 23
- [x] Seeded credentials are clearly marked as development-only and refuse to run against production
- [x] At least one seeded clinic has enough ticket history for a non-trivial wait estimate (tested:
      30+ finished tickets with varying waits; the busiest queue has 155)

## Risk and rollback

Only development tooling and tests are added; no application code or migration changes. The seed
refuses every non-local database, so the worst it can do is create four `.test` accounts in a
developer's own database, and running it again changes nothing. Rollback is a revert of this PR.

**Follow-ups found along the way:**

- Issues 17, 23, 25 and 39 each own one change here: point the factory at the new model, add
  `create`, and add the write step to `seed()` in `scripts/db/seed_dev_data.py`, keyed by slug or
  number so the script stays idempotent.
- Issue 19 decides how a site scope is written on a role assignment. `StaffFactory` uses the
  kernel's `instance` scope until then.
- OpenStreetMap has two nodes for Red Hill Clinic (`Red Hill Clinic` and `Redhill Clinic`, 10 m
  apart); the dataset uses the one tagged with its operator.

Closes #8
