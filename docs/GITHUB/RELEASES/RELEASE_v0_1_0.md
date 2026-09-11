# Release v0.1.0: Foundation & Local CI

**Date:** 2026-09-11 · **Milestone:** M1 · **Issues closed:** 1–8

A pre-release. The product does nothing a clinic can use yet: this is the foundation the next
thirteen milestones build on. It gives one application, one database, one design system, one
vocabulary, and one local gate that says whether a change is safe to push.

## What shipped

- **The application and its setup** (Issue 1, PR #110): the FastAPI app factory, typed settings with
  production guards, the `src/` layout, the kernel modules (auth, RBAC, audit, notifications,
  messaging, documents), the ClinicQ landing pages, and a *Getting started* that was run end to end
  on a clean checkout. Three runtime bugs found by `mypy` were fixed on the way, including webhook
  acknowledgements that answered 500.
- **One dev stack** (Issue 2, PR #111): PostgreSQL 18 with PostGIS 3.6 (decision 4) and Redis 8 in
  one compose project; `make db-up`, `make dev` and `make db-reset`; a smoke test that can no longer
  wipe the developer's database.
- **The database baseline, proven** (Issue 3, PR #115): twelve tests on throwaway PostgreSQL
  databases prove that upgrade from empty installs PostGIS, that downgrade to base and back
  round-trips, that autogenerate finds nothing to change, that constraint names follow the
  convention, and that both session dependencies roll back and never leak a connection. New modules
  use the sync session (decision 5). A bare `alembic upgrade head` now works.
- **The shared vocabulary** (Issue 4, PR #112): `SiteSector`, `TicketStatus`, `TicketSource` and
  `DisplayMode`, and `UserRole` extended with ClinicQ's five roles. `now_sast()` and the
  business-day helpers, UUIDv7 ids, and one error envelope (`detail`, `code`, `request_id`) for every
  API error, documented in OpenAPI. A conventions guard fails the build on a naive `datetime.now()`
  or a magic status string (non-negotiable 5).
- **The UI shell** (Issue 5, PR #113): hand-written design tokens rather than Tailwind (decision 2),
  and no Alpine.js (the CSP cannot run it). One base template with three layouts (patient,
  dashboard, board), a component library, self-hosted fonts, no CDN request, WCAG AA contrast
  checked in both themes, and `/dev/components` in development.
- **Observability** (Issue 6, PR #114): one request id on every log line and in `X-Request-ID`, the
  site and actor in the log context, redaction of phone numbers, OTPs and tokens in the logging
  layer, JSON console logs, and Redis in readiness while liveness touches nothing.
- **The local gate** (Issue 7, PR #116): pre-commit hooks including gitleaks (a commit carrying a fake
  AWS key was shown blocked), a 75% coverage floor, `unit`/`integration`/`slow` markers, a
  `make check` that names the stage that failed and passes in under three minutes, and
  `CONTRIBUTING.md`.
- **Test data** (Issue 8, PR #117): typed factories (staff persisted; patient, site, queue and ticket
  as agreed stubs), eleven real Gauteng and KwaZulu-Natal clinics from OpenStreetMap with queues and
  a day of ticket history, and `make seed-dev-data`. It is idempotent and refuses any database that
  is not a local development one.
- **Two follow-ups, merged after the eight issues closed:**
  - **Leftover exceptions removed** (Issue 4 follow-up, PR #118). `src/commons/exceptions.py` loses
    the 55 exception classes left over from the property-management project the kernel was built
    from. Nothing raised, caught or imported them. What remains is the envelope, the base, the six
    category bases and the 16 exceptions the kernel raises.
  - **No dependency advisories** (Issue 7 follow-up, PR #119). httpx2 and httpcore2 go from 2.9.1
    to 2.12.0, clearing four advisories: PYSEC-2026-3844 / CVE-2026-84381, PYSEC-2026-3845,
    PYSEC-2026-3846 / CVE-2026-84382 and PYSEC-2026-3848. The first version to fix the original
    three, 2.11.0, carries the fourth. `make check` now runs with no scanner warning: pip-audit
    finds no known vulnerability, and Trivy finds no CRITICAL or HIGH advisory with a fix in the
    image.

## Migrations

- `0001_baseline`: creates the `clinicq` schema, the kernel's 34 tables, the append-only trigger on
  `audit_event`, and the PostGIS extension in `public`. Reversible: `downgrade base` drops every
  table and the trigger and leaves PostGIS installed, because the extension is shared with anything
  else in the database. No other revision exists yet; the first ClinicQ tables arrive with M4.

## Upgrade notes

- **Start from an empty PostgreSQL 18 database.** The dev stack moved from PostgreSQL 16, which 18
  cannot open. `make db-up` creates a new volume (`clinicq_pgdata`), then run `make migrate-up`,
  `make seed-rbac` and `make seed-dev-data`. The old `clinicq-db` project keeps port 5432 until you
  remove it (see the README).
- **Install the hooks and gitleaks:** `pip install -e ".[dev]"`, `brew install gitleaks`, then
  `make hooks`. Without gitleaks every commit fails the secret-scan hook, by design.
- **Reinstall after pulling:** `pip install -e ".[dev]"` brings httpx2 and httpcore2 to 2.12.0 and
  refreshes the project's own requirements. Installing only `requirements.txt` leaves the old
  `httpx2==2.9.1` pin in the installed metadata, and `pip check` reports a conflict.
- **Before every push, `make check`** (or `make check-fast` without Docker). See `CONTRIBUTING.md`.
- **The console prints JSON logs** (`LOG_FORMAT=json` by default); `LOG_FORMAT=text` restores
  readable lines. `LOG_LEVEL` must be a level name (`info` is still accepted).
- **Redis is now in readiness:** with `REDIS_URL` set, `/health/ready` answers 503 while Redis is
  down. Without it, Redis is reported `skipped`.
- **Fonts are self-hosted** and the CSP no longer names the Google Fonts hosts; nothing on the page
  loads from another origin.
- **Database tests** run with `TEST_DATABASE_URL` set (`make test-postgres`); without it they are
  skipped.

## Known issues

- **No CI yet.** GitHub Actions arrive with Issue 9 (M2), so nothing runs on a pull request, and a
  tag push triggers nothing until Issues 10 and 11. The local `make check` is the only gate.
- **23 kernel guard tests are strict expected failures**, listed in `PENDING_ON_LATER_ISSUES` in
  `tests/conftest.py`. 19 wait for the workflows (Issue 9 onwards), and 4 wait for
  `docs/SECURITY/` documents that no issue creates yet. Each will fail loudly the day its file
  arrives, until its entry is removed.
- **The demo clinics are not in the database yet.** The `sites`, `queues` and `tickets` tables come
  with Issues 23, 25 and 39, so `make seed-dev-data` writes only the four staff accounts today and
  reports the clinics, queues and tickets it will write. The milestone's exit criterion for the
  seed (clinics, queues and tickets in a fresh database) is therefore met only for the dataset
  itself; it is fully met when those issues add their steps.
- **Seeded staff have no permissions** until Issue 18 seeds grants for the ClinicQ roles. They can
  sign in and do nothing else.
- **The scanners have limits:** both only warn, so `make check` passes whatever they find. Trivy
  looks only for CRITICAL or HIGH advisories with a published fix, so a lower-severity or unfixed
  one in the image does not show.
- **Kernel stylesheets predate the token rule:** `admin.css` and `landing.css` still carry colour
  literals, and border contrast (`--line`) is below 3:1, which the first form field must fix.
- **Tested on macOS only.** Linux and WSL were not tried; the Docker image and the compose stack are
  the closest evidence (the PostGIS image is amd64 and runs natively there).
- **A local `.env` with S3 logging enabled ships a developer's warnings to that bucket.** The kernel
  behaves this way when configured; `.env.example` (Issue 12) should default it off.
