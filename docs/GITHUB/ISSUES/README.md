# ClinicQ issue specs: how to read one, and how to pick one up

Every GitHub issue in this project is generated from a file in this folder, so the file is the
source of truth: scope, context, acceptance criteria and how to check them live here, not in a chat
thread. This page explains what each part of a spec means, the steps for picking one up, where the
code goes in *this* repository, and the decisions and inconsistencies the team still has to settle.

- **109 issues** in `M1/` … `M14/`, numbered 1–109 across the whole project.
- **[Backlog](BACKLOG/)**: six ideas deliberately parked until after the capstone.
- **[Milestones](../MILESTONES/README.md)**: what each group of issues delivers, and in which order.

## What a spec contains

| Part | What it tells you |
|------|-------------------|
| **Title** (`# Issue N: …`) | The GitHub issue title. **Never rename it or the file**: `scripts/gh_sync_docs.py` finds the issue by the `Issue N` prefix, and the legacy `scripts/gh_sync_issues.py` by the exact title. |
| **In short** | One or two plain sentences: what the team gets when this merges. Read this first. |
| **Milestone** / **Sprint** | Which milestone it closes, and when the [sprint plan](../../TEAM/WORKLOAD_SPLIT.md#3-sprint-by-sprint-lanes) schedules it. |
| **Owner** | The role code from the [workload split](../../TEAM/WORKLOAD_SPLIT.md#1-the-six-roles) (A–F) and the named backup who reviews and covers. |
| **Estimate** | Working days for the owner, including tests. |
| **Depends on** | Issues that must be merged, or stubbed by agreement, before this one starts. |
| **Unblocks** | Issues waiting on this one. If that list is long, ship a small first PR early. |
| **Note** | Only present when something needs a decision before work starts (for example, the spec and the sprint plan disagree on the owner). |
| **Context** | Why the issue exists: the problem, in the product's own terms. |
| **Starting point** | What already exists in the repository to build on, or the pattern to copy, and any open decision that affects it. **Read this before writing code**: several issues are largely done by the kernel the project started from. |
| **Scope** | What to build. |
| **Out of scope** | What *not* to build here, and which issue does it instead. |
| **Acceptance criteria** | The checklist a reviewer ticks. An issue is done when every box is ticked. |
| **How to verify** | Concrete steps a reviewer (or you, before asking for review) runs to see it working. |
| **Files touched** | Where the change is expected to land, in this repository's real layout. `NNNN` in a migration name is the next free revision number. |

## Picking up an issue

1. **Check it is ready** (the [Definition of Ready](../README.md#definition-of-ready--definition-of-done)): every issue under *Depends on* is merged, or you have agreed a stub or fixture with its owner, and nobody disputes the acceptance criteria.
2. **Read *In short*, *Starting point* and *Out of scope*** before *Scope*. They exist to stop two people building the same thing, or one person rebuilding what the kernel already has.
3. **Branch** as `Issue/<N>/<short-slug>` and start every commit message with `Issue <N>: `.
4. **If another person consumes your work, ship the contract first**: the OpenAPI entry, schemas and a fixture, as its own small PR (section 5.1 of the [workload split](../../TEAM/WORKLOAD_SPLIT.md#51-contract-first-implementation-second)).
5. **Run `make check`** (the same gate as CI) before pushing.
6. **Open the PR** with its description in `docs/GITHUB/PR/`, run the *How to verify* steps, tick the acceptance criteria, and attach a screenshot for anything a user sees.

## Where code goes in this repository

The specs were first written for an `app/` layout. The repository was then scaffolded from an existing
kernel with a `src/` layout, and the file paths in every spec now point at the real locations:

| You are adding… | It goes in… | Example to copy |
|-----------------|-------------|-----------------|
| A domain module (model, service, API, permissions) | `src/modules/<module>/` with `service.py`, `router.py`, `schemas.py` and `rbac_manifest.py` | `src/modules/widgets/` (the worked example; `make delete-example-module` removes it) |
| A database model | `src/database/models/<name>.py`, exported from `src/database/models/__init__.py` | `src/database/models/widget.py` |
| A migration | `alembic/versions/NNNN_<slug>.py` (`scripts/db/alembic-revision.sh`, then `make migrate-up`) | `alembic/versions/0001_baseline.py` |
| An API router | registered in `src/api/v1/router.py` (one import, one `include_router`); served under `/api/v1/` | the existing routers there |
| The module's permissions | registered in `src/core/rbac_manifest_registry.py`, then `make seed-rbac` | `src/modules/widgets/rbac_manifest.py` |
| A server-rendered page | `src/web/<area>.py` (or `src/web/<area>/` once there are several) plus `src/templates/<area>/`, extending one of the three layouts in `src/templates/layouts/` (patient, dashboard, board) and built from the macros in `src/templates/components/` | `src/web/routes.py`; `/dev/components` in development |
| CSS or JavaScript | `src/static/css/`, `src/static/js/`, third-party code in `src/static/vendor/`. No inline styles or scripts: the CSP forbids them and guard tests enforce it. Colours only as tokens from `site.css` (decision 2) | `src/static/css/site.css` (the design tokens), `components.css` |
| Shared enums, errors, helpers | `src/commons/`: `enums.py` (wire values), `exceptions.py` (domain errors and the error envelope), `time.py` (`now_sast()`, `business_date()`), `ids.py` (`new_id()`) | A domain error subclasses a category in `exceptions.py` (`NotFoundError`, `ConflictError`…); `src/core/error_handlers.py` answers it |
| Cross-cutting infrastructure | `src/core/` (config, security, audit, scoping, rate limits, scheduler) | |
| A scheduled job | a `run_*` function in the module's `service.py`, registered in `src/core/scheduler.py` | the notification retry and document retention sweeps |
| Tests | `tests/unit/<area>/test_*.py` and `tests/integration/<area>/test_*.py` | `tests/integration/auth/` |
| Docker and Compose | `infra/docker/` | |

The ClinicQ modules the specs create are `sites`, `patients`, `staff`, `discovery`, `queue`, `display`,
`channels`, `appointments`, `reporting` and `compliance`, plus the dashboard pages in `src/web/dashboard/`.
Notifications extend the kernel's existing `src/modules/notifications/`.

## Open decisions

Places where the specs and the repository disagree. None blocks sprint 1, but each should be settled
(and the losing side's wording corrected) before the first issue it affects starts.

| # | Decision | The specs say | The repository has | Affects |
|---|----------|---------------|--------------------|---------|
| 1 | Background jobs (**decided in Issue 43**) | `arq` workers | APScheduler with a PostgreSQL advisory lock (`src/core/scheduler.py`), already running the notification retry and retention sweeps, the queue snapshot reconciliation (Issue 36) and the recall timers (Issue 43). **Kept, no `arq`**: see the note below the table | 36, 43, 63, 81, 82, 85, 88, 91, 95, 99 |
| 2 | Styling (**decided in Issue 5**) | Tailwind, compiled; Alpine.js for local state | Hand-written CSS design tokens (`site.css`, `admin.css`, `landing.css`) with light and dark themes. **Kept, with htmx and no Alpine**: see the note below the table | 5 and every UI issue |
| 3 | Packaging and layout (**decided in Issue 1**) | `uv` and `uv sync`; an `app/` package | `requirements.txt` with setuptools (`pyproject.toml`); a `src/` package. **Kept as is**: see the note below the table | 1, 7, 9 |
| 4 | PostgreSQL version (**decided in Issue 2**) | 18 | `postgis/postgis:16-3.4` in `infra/docker/docker-compose.db.yml`. **Now `postgis/postgis:18-3.6`**: see the note below the table | 2, 3, 9, 102 |
| 5 | Database sessions (**decided in Issue 3**) | Async everywhere | Both; most kernel routes use the sync `get_db`. **Sync by default, async for streams**: see the note below the table | 3 and every new module |
| 6 | Seeding roles and grants (**decided in Issue 18**) | By migration | By module manifests and `make seed-rbac` (idempotent). **Kept, and run automatically**: see the note below the table | 18 |
| 7 | Database topology | A production database of its own | A schema (`clinicq`) in a shared platform database, which `scripts/db/backup.sh` assumes | 102, 103 |
| 8 | API paths (**decided in Issue 31**) | `/api/...` (for example `/api/clinics/nearby`) | Everything under `/api/v1/`. **Kept**: see the note below the table | 31 and every API issue |
| 9 | USSD and WhatsApp identity (**decided in Issue 17**) | "A USSD session is trusted via the gateway MSISDN without a second OTP" | `patient_for_gateway()` in `src/modules/patients/service.py`. **Trusted, with conditions**: see the note below the table | 17, 73, 75 |

**Decision 1, recorded in Issue 43:** scheduled work stays on the kernel's APScheduler sweeps under
a PostgreSQL advisory lock (`src/core/scheduler.py`); the project does not add `arq` or a second job
runner. The case for `arq` was "survives a restart, never double-fires", and for the jobs the specs
describe the sweeps already give both, for a reason worth stating: **the state of a timed job lives
in the database, not in the worker.** The recall timers (Issue 43) keep no timer in memory; a deadline
is `called_at` (or `recalled_at`) plus a timeout, so a process that started a second ago sees exactly
what the last one saw, and `tests/integration/queue/test_recall_timers.py` proves it by running the
sweep in fresh interpreter processes around a restart. Double-firing is refused three times over:
the advisory lock elects one runner, each row is locked (`FOR UPDATE SKIP LOCKED`), and the lifecycle
refuses a move whose `expected_status` has gone. `arq` would have added Redis as a hard dependency of
correctness, a second process type to deploy, monitor and restart, and a second place a job's state
could disagree with the database, for no property the sweeps lack. What `arq` does better, many
short independent tasks fanned out fast, is not a shape any current spec has; an issue that needs it
revisits this with the job named. Specs that say `arq` mean a `run_*` sweep in `src/core/scheduler.py`.

**Decision 8, recorded in Issue 31:** every route stays under `/api/v1/`, the discovery search
included: it is served at `GET /api/v1/clinics/nearby`, not `/api/clinics/nearby`. The version
prefix is where the kernel's error responses, OpenAPI document, contract drift test and route-gate
guard all look, and the channel adapters (M10) will be written against a versioned path they can
keep when a `v2` appears. Specs that write `/api/...` mean `/api/v1/...`; no route is served at both.

**Decision 6, recorded in Issue 18:** roles and grants are seeded from the module manifests by
`make seed-rbac` (`scripts/db/seed_rbac.py` → `sync_rbac_catalog`), not by an Alembic migration.
What the criterion "created by migration, not by a manual script" protects against is a deployment
whose permissions depend on someone remembering to run something by hand, and that holds: the
deploy sequence (`scripts/db/deploy-sequence.sh`) runs the seed right after `alembic upgrade head`
and then `seed_rbac --check`, in the new image, before it serves traffic, and CI runs the same
sequence on every pull request, so drift fails a PR rather than a deploy. A migration would be the
worse home: grants live next to the resources they gate, in each module's `rbac_manifest.py`, and
a migration freezes the values of its day, so a later manifest edit would need a second, hand-written
data migration to reach a database; the seed is idempotent and insert-only, so an operator's grant
edit from `/admin/rbac` survives a deploy. The roles it seeds, and what each may do, are in
`docs/architecture/rbac-matrix.md` (generated from a seeded database, with a drift test), and every
decision is pinned by `tests/snapshots/rbac_decisions.txt`.

**Decision 9, recorded in Issue 17:** a USSD session, and a WhatsApp conversation, identify the
patient by the number the gateway reports, **with no second OTP**. The mobile network authenticated
that MSISDN when it connected the session (a USSD session cannot be started from someone else's
SIM), and Meta verified the WhatsApp number when it registered. An SMS code on top would add a
second channel, a cost per session and a failure point to prove what the network already proved,
and would shut out feature phones on weak coverage, the people USSD is for. The trust holds only
because of three conditions the M10 issues must keep: the gateway webhook is authenticated (a
shared secret or signature, and an IP allow-list), so only the aggregator can claim a number; the
trust never becomes a web session, which only a code can start; and the accepted risk is the one an
SMS code accepts anyway, a lost or stolen phone. The reasoning is also in the `patients` package
docstring, next to the code.

**Decision 2, recorded in Issue 5:** the hand-written design tokens stay, and grow into the one
token set all three layouts read (colour, a type scale, spacing, layers); there is no Tailwind and no
Alpine.js. Tailwind would add a build step to every laptop, the Docker image and CI, and would leave
the kernel's console on the tokens until someone rewrote it, so the project would carry two design
systems, the thing Issue 5 exists to prevent. Alpine's standard build evaluates expressions with
`new Function`, which the CSP (no `'unsafe-eval'`) blocks, and its CSP build cannot run the
expressions that make it worth having; small modules in `src/static/js/` do the same job under the
same policy. The components are Jinja macros in `src/templates/components/` over
`src/static/css/components.css`, and the fonts are self-hosted, so no page loads CSS, JavaScript or
fonts from another host. Specs that say Tailwind or Alpine mean the tokens and a module in
`src/static/js/`; `/dev/components` shows what exists.

**Decision 5, recorded in Issue 3:** new modules use the **sync** session, `get_db`, like every
kernel router. The RBAC dependencies, the tenancy scoping and the audit writer all take a sync
`Session`, and FastAPI runs a sync route in its worker pool, so it never blocks the event loop.
Going async everywhere would mean async twins of all of those before the first ClinicQ route, for no
gain at a clinic's request rate. `get_async_db` stays for the few handlers that must not hold a
worker for long: the board's server-sent events (M8) and similar streams. Alembic runs on the sync
engine too. Both dependencies roll back on an exception and never leak a connection, which
`tests/integration/database/test_session_lifecycle.py` proves against PostgreSQL.

**Decision 3, recorded in Issue 1:** the project keeps `requirements.txt` with setuptools and the
`src/` package it was scaffolded with. Every script, Dockerfile, CI stage and guard test already
assumes them, and moving to `uv` or to an `app/` package would be a repository-wide change with no
feature behind it. Revisit only as a team decision in its own issue; until then, specs that mention
`uv`, `uv sync`, `uv run` or `app/` mean the equivalents in the *Where code goes* table above.

**Decision 4, recorded in Issue 2:** PostgreSQL **18** with PostGIS **3.6**, as the specs say. The
dev stack runs `postgis/postgis:18-3.6`, which is the same major and minor versions as the shared
platform database (PostgreSQL 18.6, PostGIS 3.6.4 when this was decided), so a query that works on a
laptop works where the product is deployed. Staying on 16 would have meant developing against a
version nothing else runs, and losing what 18 adds (a native `uuidv7()` among them). The image is
written once, in `infra/docker/docker-compose.db.yml`; CI (Issue 9) must use the same image for its
service container, and `tests/unit/platform/test_dev_stack_compose.py` fails if a compose file or
workflow brings in a second PostgreSQL version.

## Planning inconsistencies found

Found while cross-checking the specs against the [workload split](../../TEAM/WORKLOAD_SPLIT.md). Each
affected spec carries a **Note** saying the same thing.

- **The spec and the sprint plan name different owners** for Issues 30 (E and F in the plan), 31 (C),
  34 (F), 57 and 58 (D) and 70 (C). Section 9 of the workload split also leaves 3, 4, 30, 31, 34, 36, 38
  and 70 unassigned, gives 17 to F (for the consent wording), puts 57 and 58 under D, and lists 61
  under both D and E.
- **Suspected dependency errors.** Issue 60 depended on Issue 26 (the services catalogue); Issue 27
  (display settings, including `board_language` and `announce_audio`) was intended. Issues 56 and 60
  read the privacy projection from Issue 58 but did not list it. *Both are corrected: Issue 56 depends on
  Issue 58, and Issue 60 on Issues 27, 57 and 58.*
- **A dependency on a later milestone.** Issue 66 (M9, sprint 4) needs Issue 77 (M10, sprint 8) for
  translations.
- **Milestones that cannot close in their stated window.** The sprint plan schedules issues of M2, M7,
  M8 and M9 after those milestones' windows; the milestone pages say which sprint each really closes in.
- **Unscheduled.** Issue 36 is not in any lane of the sprint plan.

## Glossary

| Term | Meaning here |
|------|--------------|
| **Site** | One clinic, the unit every record is scoped to. "Site" in code, "clinic" in the UI. |
| **Sector** | `public` or `private`. |
| **Queue** | A named line at a site: triage, a consulting room, the pharmacy window. |
| **Clinic service** | Something a site offers (immunisation, chronic collection) with an expected duration. |
| **Ticket** | One patient's place in one queue on one day. |
| **Sequence** | The ticket's number, unique per queue per service day. |
| **Service day** | The calendar day in `Africa/Johannesburg`, which is when numbering restarts. |
| **Source / channel** | How the ticket was created: web, USSD, WhatsApp or a reception walk-in. All share one sequence. |
| **Visit** | The tickets of one patient journey across queues (triage → doctor → pharmacy). |
| **Display mode** | What the waiting-room board may show: `number_only` (the default), `name_lite` or `full`. |
| **Projection** | The server-side function that removes whatever the display mode forbids before anything reaches the board. |
| **Board** | The waiting-room TV. **Dashboard** is the staff screen. **Kiosk** is the check-in tablet at the door. |
| **Contract** | A hand-written OpenAPI file in `contracts/`, with a *drift test* that fails when the code and the file disagree. |
| **Stub / fixture** | Fake data in the agreed contract shape, so a consumer can build before the producer has finished. |
| **Lane** | One person's column in the sprint plan. |
| **Scope tier** | How far a permission reaches: `own`, `assigned` or `business` (see `src/core/scope.py`). |
| **MSISDN** | A mobile number as the USSD gateway reports it. |
| **PWA / SSE** | Progressive web app (installable website); server-sent events (the live update stream). |
| **POPIA / DSAR** | South Africa's Protection of Personal Information Act; a data-subject access request. |

## All issues

| # | Issue | Milestone | Owner | Sprint | Estimate | Depends on |
|---|-------|-----------|-------|--------|----------|------------|
| [1](M1/ISSUE_1_repo_scaffold_app_factory.md) | Repository scaffold, Python 3.14 + FastAPI app factory, typed settings | [M1](../MILESTONES/M1_foundation_local_ci.md) | E | 1 | 2 days | - |
| [2](M1/ISSUE_2_docker_dev_stack_postgis_redis.md) | Docker Compose dev stack: PostgreSQL 18 + PostGIS, Redis, API | [M1](../MILESTONES/M1_foundation_local_ci.md) | E | 1 | 1 day | [1](M1/ISSUE_1_repo_scaffold_app_factory.md) |
| [3](M1/ISSUE_3_sqlalchemy_alembic_baseline.md) | Async SQLAlchemy 2.x + Alembic baseline (PostGIS extension enabled) | [M1](../MILESTONES/M1_foundation_local_ci.md) | A | 1 | 2 days | [2](M1/ISSUE_2_docker_dev_stack_postgis_redis.md) |
| [4](M1/ISSUE_4_shared_kernel_enums_time_errors.md) | Shared kernel: enums, error envelope, IDs, Africa/Johannesburg datetimes | [M1](../MILESTONES/M1_foundation_local_ci.md) | A | 1 | 1 day | [1](M1/ISSUE_1_repo_scaffold_app_factory.md) |
| [5](M1/ISSUE_5_base_ui_shell_tailwind_htmx.md) | Base UI shell: Jinja2 layout, Tailwind build, htmx + Alpine wiring | [M1](../MILESTONES/M1_foundation_local_ci.md) | C | 1 | 2 days | [1](M1/ISSUE_1_repo_scaffold_app_factory.md) |
| [6](M1/ISSUE_6_logging_request_context_health.md) | Structured logging, request-context middleware, health & readiness probes | [M1](../MILESTONES/M1_foundation_local_ci.md) | E | 1 | 1 day | [1](M1/ISSUE_1_repo_scaffold_app_factory.md) |
| [7](M1/ISSUE_7_ci_local_harness_precommit.md) | `ci-local.sh` harness (ruff, mypy, pytest, docker build) + pre-commit | [M1](../MILESTONES/M1_foundation_local_ci.md) | E | 2 | 1 day | [1](M1/ISSUE_1_repo_scaffold_app_factory.md), [3](M1/ISSUE_3_sqlalchemy_alembic_baseline.md) |
| [8](M1/ISSUE_8_test_factories_seed_data.md) | Test factories and `seed_dev_data.py` demo dataset | [M1](../MILESTONES/M1_foundation_local_ci.md) | E | 2 | 2 days | [3](M1/ISSUE_3_sqlalchemy_alembic_baseline.md), [4](M1/ISSUE_4_shared_kernel_enums_time_errors.md) |
| [9](M2/ISSUE_9_actions_ci_lint_type_test.md) | GitHub Actions CI: lint, type-check, test with PostGIS + Redis services | [M2](../MILESTONES/M2_cicd_environments.md) | E | 2 | 2 days | [7](M1/ISSUE_7_ci_local_harness_precommit.md), [8](M1/ISSUE_8_test_factories_seed_data.md) |
| [10](M2/ISSUE_10_container_build_ghcr_release.md) | Container build and GHCR publish on tag | [M2](../MILESTONES/M2_cicd_environments.md) | E | 2 | 1 day | [9](M2/ISSUE_9_actions_ci_lint_type_test.md) |
| [11](M2/ISSUE_11_cd_staging_prod_approval.md) | CD: staging deploy on tag, production behind manual approval | [M2](../MILESTONES/M2_cicd_environments.md) | E | 3 | 2 days | [10](M2/ISSUE_10_container_build_ghcr_release.md) |
| [12](M2/ISSUE_12_env_matrix_config_validation.md) | Environment matrix, `.env.example` and fail-fast config validation | [M2](../MILESTONES/M2_cicd_environments.md) | E | 3 | 1 day | [1](M1/ISSUE_1_repo_scaffold_app_factory.md) |
| [13](M2/ISSUE_13_team_workflow_templates_codeowners.md) | Team workflow: branch protection, PR/issue templates, CODEOWNERS, labels sync | [M2](../MILESTONES/M2_cicd_environments.md) | E | 3 | 1 day | [9](M2/ISSUE_9_actions_ci_lint_type_test.md) |
| [14](M2/ISSUE_14_monitoring_baseline_alerts.md) | Monitoring baseline: uptime checks, error tracking, deploy notifications | [M2](../MILESTONES/M2_cicd_environments.md) | E | 3 | 1 day | [11](M2/ISSUE_11_cd_staging_prod_approval.md) |
| [15](M3/ISSUE_15_staff_user_security_core.md) | Staff user model and security core (JWT, refresh rotation, password hashing) | [M3](../MILESTONES/M3_identity_auth_rbac.md) | A | 2 | 3 days | [3](M1/ISSUE_3_sqlalchemy_alembic_baseline.md), [4](M1/ISSUE_4_shared_kernel_enums_time_errors.md) |
| [16](M3/ISSUE_16_staff_signin_sessions_csrf.md) | Staff sign-in, sessions, logout, CSRF and password reset | [M3](../MILESTONES/M3_identity_auth_rbac.md) | A | 3 | 2 days | [15](M3/ISSUE_15_staff_user_security_core.md) |
| [17](M3/ISSUE_17_patient_identity_otp.md) | Patient identity: phone-first records with OTP verification | [M3](../MILESTONES/M3_identity_auth_rbac.md) | A | 3 | 3 days | [4](M1/ISSUE_4_shared_kernel_enums_time_errors.md), [15](M3/ISSUE_15_staff_user_security_core.md) |
| [18](M3/ISSUE_18_rbac_roles_enforcement.md) | RBAC model, seeded roles and enforcement dependencies | [M3](../MILESTONES/M3_identity_auth_rbac.md) | A | 4 | 3 days | [15](M3/ISSUE_15_staff_user_security_core.md) |
| [19](M3/ISSUE_19_site_scoping_guard.md) | Multi-tenant site scoping guard and cross-site access tests | [M3](../MILESTONES/M3_identity_auth_rbac.md) | A | 4 | 2 days | [18](M3/ISSUE_18_rbac_roles_enforcement.md) |
| [20](M3/ISSUE_20_audit_log_admin_api.md) | Append-only audit log and admin read API | [M3](../MILESTONES/M3_identity_auth_rbac.md) | A | 4 | 2 days | [15](M3/ISSUE_15_staff_user_security_core.md), [18](M3/ISSUE_18_rbac_roles_enforcement.md) |
| [21](M3/ISSUE_21_consent_capture_withdrawal.md) | Consent capture and withdrawal (display, notifications, board comment) | [M3](../MILESTONES/M3_identity_auth_rbac.md) | F | 3 | 2 days | [17](M3/ISSUE_17_patient_identity_otp.md) |
| [22](M3/ISSUE_22_staff_invitations_account_settings.md) | Staff invitations and account settings | [M3](../MILESTONES/M3_identity_auth_rbac.md) | A | 4 | 2 days | [16](M3/ISSUE_16_staff_signin_sessions_csrf.md), [18](M3/ISSUE_18_rbac_roles_enforcement.md) |
| [23](M4/ISSUE_23_sites_model_profile_crud.md) | `sites` model with PostGIS location and clinic profile CRUD | [M4](../MILESTONES/M4_clinics_queues_config.md) | A | 5 | 3 days | [3](M1/ISSUE_3_sqlalchemy_alembic_baseline.md), [19](M3/ISSUE_19_site_scoping_guard.md) |
| [24](M4/ISSUE_24_opening_hours_closures.md) | Opening hours, holiday calendar and temporary-closure broadcast | [M4](../MILESTONES/M4_clinics_queues_config.md) | A | 5 | 2 days | [23](M4/ISSUE_23_sites_model_profile_crud.md) |
| [25](M4/ISSUE_25_queues_model_multiroom.md) | `queues` model: multi-room, multi-service queues per site | [M4](../MILESTONES/M4_clinics_queues_config.md) | A | 5 | 2 days | [23](M4/ISSUE_23_sites_model_profile_crud.md) |
| [26](M4/ISSUE_26_services_catalogue_service_times.md) | Services catalogue with expected service times | [M4](../MILESTONES/M4_clinics_queues_config.md) | A | 5 | 1 day | [25](M4/ISSUE_25_queues_model_multiroom.md) |
| [27](M4/ISSUE_27_display_privacy_settings.md) | Display and privacy settings per site | [M4](../MILESTONES/M4_clinics_queues_config.md) | A | 5 | 1 day | [21](M3/ISSUE_21_consent_capture_withdrawal.md), [23](M4/ISSUE_23_sites_model_profile_crud.md) |
| [28](M4/ISSUE_28_staff_site_room_assignment.md) | Staff-to-site and room assignment | [M4](../MILESTONES/M4_clinics_queues_config.md) | A | 5 | 1 day | [22](M3/ISSUE_22_staff_invitations_account_settings.md), [25](M4/ISSUE_25_queues_model_multiroom.md) |
| [29](M4/ISSUE_29_clinic_onboarding_verification.md) | Clinic onboarding and platform-admin verification workflow | [M4](../MILESTONES/M4_clinics_queues_config.md) | F | 4 | 2 days | [23](M4/ISSUE_23_sites_model_profile_crud.md), [24](M4/ISSUE_24_opening_hours_closures.md) |
| [30](M4/ISSUE_30_sites_openapi_contract_tests.md) | `sites` OpenAPI contract and module tests | [M4](../MILESTONES/M4_clinics_queues_config.md) | A | 5 | 2 days | [23](M4/ISSUE_23_sites_model_profile_crud.md), [24](M4/ISSUE_24_opening_hours_closures.md), [25](M4/ISSUE_25_queues_model_multiroom.md), [26](M4/ISSUE_26_services_catalogue_service_times.md), [27](M4/ISSUE_27_display_privacy_settings.md), [28](M4/ISSUE_28_staff_site_room_assignment.md), [29](M4/ISSUE_29_clinic_onboarding_verification.md) |
| [31](M5/ISSUE_31_clinics_nearby_postgis_search.md) | `GET /api/clinics/nearby`: PostGIS radius search with distance and ETA | [M5](../MILESTONES/M5_discovery_geolocation.md) | A | 5 | 3 days | [23](M4/ISSUE_23_sites_model_profile_crud.md) |
| [32](M5/ISSUE_32_discovery_list_ui_sector_toggle.md) | Discovery list UI with public/private/all toggle and sector badges | [M5](../MILESTONES/M5_discovery_geolocation.md) | C | 3–5 | 3 days | [31](M5/ISSUE_31_clinics_nearby_postgis_search.md) |
| [33](M5/ISSUE_33_discovery_map_leaflet_osm.md) | Map view (Leaflet + OpenStreetMap) with clustering and directions hand-off | [M5](../MILESTONES/M5_discovery_geolocation.md) | C | 4 | 2 days | [32](M5/ISSUE_32_discovery_list_ui_sector_toggle.md) |
| [34](M5/ISSUE_34_area_fallback_search.md) | Area/suburb fallback search for patients without GPS | [M5](../MILESTONES/M5_discovery_geolocation.md) | A | 2 | 2 days | [31](M5/ISSUE_31_clinics_nearby_postgis_search.md) |
| [35](M5/ISSUE_35_clinic_detail_page.md) | Clinic detail page: hours, live queue length, services, contact | [M5](../MILESTONES/M5_discovery_geolocation.md) | C | 5 | 2 days | [24](M4/ISSUE_24_opening_hours_closures.md), [31](M5/ISSUE_31_clinics_nearby_postgis_search.md) |
| [36](M5/ISSUE_36_queue_snapshot_cache.md) | `site_queue_snapshot` Redis cache with warm and invalidate paths | [M5](../MILESTONES/M5_discovery_geolocation.md) | A | 5–6 | 2 days | [25](M4/ISSUE_25_queues_model_multiroom.md), [31](M5/ISSUE_31_clinics_nearby_postgis_search.md) |
| [37](M5/ISSUE_37_payment_medical_aid_filter.md) | Payment and medical-aid directory filter (private clinics only) | [M5](../MILESTONES/M5_discovery_geolocation.md) | F | 5 | 2 days | [23](M4/ISSUE_23_sites_model_profile_crud.md), [32](M5/ISSUE_32_discovery_list_ui_sector_toggle.md) |
| [38](M5/ISSUE_38_discovery_analytics_contract.md) | Discovery analytics events and discovery OpenAPI contract | [M5](../MILESTONES/M5_discovery_geolocation.md) | F | 6 | 2 days | [31](M5/ISSUE_31_clinics_nearby_postgis_search.md), [32](M5/ISSUE_32_discovery_list_ui_sector_toggle.md), [33](M5/ISSUE_33_discovery_map_leaflet_osm.md), [34](M5/ISSUE_34_area_fallback_search.md), [35](M5/ISSUE_35_clinic_detail_page.md), [36](M5/ISSUE_36_queue_snapshot_cache.md), [37](M5/ISSUE_37_payment_medical_aid_filter.md) |
| [39](M6/ISSUE_39_tickets_model_sequence.md) | `tickets` model and concurrency-safe daily sequence numbering | [M6](../MILESTONES/M6_queue_engine_core.md) | A | 6 | 3 days | [17](M3/ISSUE_17_patient_identity_otp.md), [25](M4/ISSUE_25_queues_model_multiroom.md) |
| [40](M6/ISSUE_40_join_queue_service_api.md) | Join-queue service and API (all channels), with abuse guards | [M6](../MILESTONES/M6_queue_engine_core.md) | A | 6 | 3 days | [39](M6/ISSUE_39_tickets_model_sequence.md) |
| [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md) | Ticket lifecycle state machine and illegal-transition rejection | [M6](../MILESTONES/M6_queue_engine_core.md) | A | 6 | 2 days | [39](M6/ISSUE_39_tickets_model_sequence.md) |
| [42](M6/ISSUE_42_wait_time_estimation.md) | Wait-time estimation service and `wait_time_samples` | [M6](../MILESTONES/M6_queue_engine_core.md) | A | 7 | 3 days | [26](M4/ISSUE_26_services_catalogue_service_times.md), [39](M6/ISSUE_39_tickets_model_sequence.md) |
| [43](M6/ISSUE_43_recall_noshow_timers.md) | Recall timers and automatic no-show transitions (`arq` jobs) | [M6](../MILESTONES/M6_queue_engine_core.md) | A | 7 | 2 days | [2](M1/ISSUE_2_docker_dev_stack_postgis_redis.md), [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md) |
| [44](M6/ISSUE_44_patient_cancel_recalculation.md) | Patient cancellation and queue position recalculation | [M6](../MILESTONES/M6_queue_engine_core.md) | A | 7 | 2 days | [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md) |
| [45](M6/ISSUE_45_queue_transfer.md) | Transfer between queues without re-joining (triage → doctor → pharmacy) | [M6](../MILESTONES/M6_queue_engine_core.md) | A | 7 | 2 days | [25](M4/ISSUE_25_queues_model_multiroom.md), [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md) |
| [46](M6/ISSUE_46_priority_override_audit.md) | Clinical priority override with reason codes and audit trail | [M6](../MILESTONES/M6_queue_engine_core.md) | A | 7 | 2 days | [20](M3/ISSUE_20_audit_log_admin_api.md), [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md) |
| [47](M6/ISSUE_47_queue_contract_concurrency_tests.md) | Queue OpenAPI contract, concurrency and state-machine tests | [M6](../MILESTONES/M6_queue_engine_core.md) | A | 7 | 3 days | [39](M6/ISSUE_39_tickets_model_sequence.md), [40](M6/ISSUE_40_join_queue_service_api.md), [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md), [42](M6/ISSUE_42_wait_time_estimation.md), [43](M6/ISSUE_43_recall_noshow_timers.md), [44](M6/ISSUE_44_patient_cancel_recalculation.md), [45](M6/ISSUE_45_queue_transfer.md), [46](M6/ISSUE_46_priority_override_audit.md) |
| [48](M7/ISSUE_48_dashboard_shell_role_nav.md) | Dashboard shell, role-aware navigation and site switcher | [M7](../MILESTONES/M7_clinic_dashboard.md) | D | 3 | 2 days | [18](M3/ISSUE_18_rbac_roles_enforcement.md), [28](M4/ISSUE_28_staff_site_room_assignment.md) |
| [49](M7/ISSUE_49_front_desk_board_live.md) | Front-desk board: all active queues, live via SSE with polling fallback | [M7](../MILESTONES/M7_clinic_dashboard.md) | D | 6 | 3 days | [40](M6/ISSUE_40_join_queue_service_api.md), [48](M7/ISSUE_48_dashboard_shell_role_nav.md) |
| [50](M7/ISSUE_50_call_next_actions.md) | Call next, recall, mark done and no-show actions | [M7](../MILESTONES/M7_clinic_dashboard.md) | D | 7 | 2 days | [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md), [49](M7/ISSUE_49_front_desk_board_live.md) |
| [51](M7/ISSUE_51_walkin_intake_ticket_stub.md) | Walk-in intake form and printable ticket stub | [M7](../MILESTONES/M7_clinic_dashboard.md) | D | 7 | 2 days | [40](M6/ISSUE_40_join_queue_service_api.md), [49](M7/ISSUE_49_front_desk_board_live.md) |
| [52](M7/ISSUE_52_reorder_ui_audit_trail.md) | Drag-to-reorder with reason codes and inline audit trail | [M7](../MILESTONES/M7_clinic_dashboard.md) | D | 8 | 2 days | [46](M6/ISSUE_46_priority_override_audit.md) |
| [53](M7/ISSUE_53_nurse_room_view_visit_notes.md) | Nurse/doctor room view and private visit notes | [M7](../MILESTONES/M7_clinic_dashboard.md) | D | 8 | 2 days | [28](M4/ISSUE_28_staff_site_room_assignment.md), [48](M7/ISSUE_48_dashboard_shell_role_nav.md) |
| [54](M7/ISSUE_54_manager_settings_ui.md) | Clinic manager settings UI (profile, hours, display mode, staff, services) | [M7](../MILESTONES/M7_clinic_dashboard.md) | D | 9 | 3 days | [24](M4/ISSUE_24_opening_hours_closures.md), [27](M4/ISSUE_27_display_privacy_settings.md), [28](M4/ISSUE_28_staff_site_room_assignment.md) |
| [55](M7/ISSUE_55_dashboard_offline_tests.md) | Reconnect/offline states and dashboard interaction tests | [M7](../MILESTONES/M7_clinic_dashboard.md) | D | 11 | 2 days | [49](M7/ISSUE_49_front_desk_board_live.md), [50](M7/ISSUE_50_call_next_actions.md) |
| [56](M8/ISSUE_56_board_page_kiosk.md) | Waiting-room board page (kiosk) with now-serving and up-next panels | [M8](../MILESTONES/M8_display_monitor.md) | D | 5 | 3 days | [27](M4/ISSUE_27_display_privacy_settings.md), [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md), [58](M8/ISSUE_58_board_privacy_rendering.md) |
| [57](M8/ISSUE_57_board_sse_channel.md) | SSE live update channel with reconnect, backoff and heartbeat | [M8](../MILESTONES/M8_display_monitor.md) | A | 9 | 2 days | [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md), [56](M8/ISSUE_56_board_page_kiosk.md) |
| [58](M8/ISSUE_58_board_privacy_rendering.md) | Server-side privacy-mode rendering and consent gating | [M8](../MILESTONES/M8_display_monitor.md) | A | 9 | 2 days | [21](M3/ISSUE_21_consent_capture_withdrawal.md), [27](M4/ISSUE_27_display_privacy_settings.md) |
| [59](M8/ISSUE_59_board_accessibility.md) | Accessibility pass: contrast, type scale, 5-metre legibility, reduced motion | [M8](../MILESTONES/M8_display_monitor.md) | D | 10 | 2 days | [56](M8/ISSUE_56_board_page_kiosk.md) |
| [60](M8/ISSUE_60_board_audio_tts.md) | Audio chime and multi-language text-to-speech call announcements | [M8](../MILESTONES/M8_display_monitor.md) | D | 10 | 2 days | [27](M4/ISSUE_27_display_privacy_settings.md), [57](M8/ISSUE_57_board_sse_channel.md), [58](M8/ISSUE_58_board_privacy_rendering.md) |
| [61](M8/ISSUE_61_kiosk_device_registry.md) | Kiosk device registry, pairing codes and heartbeat monitoring | [M8](../MILESTONES/M8_display_monitor.md) | E | 10 | 2 days | [23](M4/ISSUE_23_sites_model_profile_crud.md), [56](M8/ISSUE_56_board_page_kiosk.md) |
| [62](M8/ISSUE_62_board_resilience_tests.md) | Board resilience: cached last-known state, stale banner, recovery tests | [M8](../MILESTONES/M8_display_monitor.md) | D | 10–11 | 2 days | [57](M8/ISSUE_57_board_sse_channel.md), [61](M8/ISSUE_61_kiosk_device_registry.md) |
| [63](M9/ISSUE_63_notification_service_adapters.md) | Notification service, transport adapters and delivery log | [M9](../MILESTONES/M9_notifications_patient_pwa.md) | B | 3 | 3 days | [21](M3/ISSUE_21_consent_capture_withdrawal.md), [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md) |
| [64](M9/ISSUE_64_web_push_vapid.md) | Web Push (VAPID) subscriptions and 'you're next' push | [M9](../MILESTONES/M9_notifications_patient_pwa.md) | B | 5 | 2 days | [63](M9/ISSUE_63_notification_service_adapters.md) |
| [65](M9/ISSUE_65_sms_gateway_cost_caps.md) | SMS gateway adapter with cost caps, delivery receipts and kill switch | [M9](../MILESTONES/M9_notifications_patient_pwa.md) | B | 5 | 2 days | [63](M9/ISSUE_63_notification_service_adapters.md) |
| [66](M9/ISSUE_66_notification_templates_i18n.md) | Multi-language notification templates and admin editor | [M9](../MILESTONES/M9_notifications_patient_pwa.md) | B | 4 | 2 days | [63](M9/ISSUE_63_notification_service_adapters.md), [77](M10/ISSUE_77_i18n_menu_translations.md) |
| [67](M9/ISSUE_67_notification_preferences_quiet_hours.md) | Notification preferences, quiet hours and opt-out enforcement | [M9](../MILESTONES/M9_notifications_patient_pwa.md) | B | 6 | 2 days | [21](M3/ISSUE_21_consent_capture_withdrawal.md), [63](M9/ISSUE_63_notification_service_adapters.md) |
| [68](M9/ISSUE_68_patient_ticket_page.md) | Patient ticket page: live position, ETA countdown, cancel | [M9](../MILESTONES/M9_notifications_patient_pwa.md) | C | 7 | 3 days | [40](M6/ISSUE_40_join_queue_service_api.md), [42](M6/ISSUE_42_wait_time_estimation.md) |
| [69](M9/ISSUE_69_pwa_shell_service_worker.md) | PWA shell: manifest, service worker, offline last-known ticket | [M9](../MILESTONES/M9_notifications_patient_pwa.md) | C | 7 | 2 days | [5](M1/ISSUE_5_base_ui_shell_tailwind_htmx.md), [68](M9/ISSUE_68_patient_ticket_page.md) |
| [70](M9/ISSUE_70_qr_ticket_code.md) | QR ticket code for kiosk check-in and reception lookup | [M9](../MILESTONES/M9_notifications_patient_pwa.md) | B | 8 | 1 day | [39](M6/ISSUE_39_tickets_model_sequence.md), [68](M9/ISSUE_68_patient_ticket_page.md) |
| [71](M9/ISSUE_71_notifications_contract_tests.md) | Notification OpenAPI contract, delivery and retry tests | [M9](../MILESTONES/M9_notifications_patient_pwa.md) | B | 11–12 | 2 days | [63](M9/ISSUE_63_notification_service_adapters.md), [64](M9/ISSUE_64_web_push_vapid.md), [65](M9/ISSUE_65_sms_gateway_cost_caps.md), [66](M9/ISSUE_66_notification_templates_i18n.md), [67](M9/ISSUE_67_notification_preferences_quiet_hours.md), [68](M9/ISSUE_68_patient_ticket_page.md), [69](M9/ISSUE_69_pwa_shell_service_worker.md), [70](M9/ISSUE_70_qr_ticket_code.md) |
| [72](M10/ISSUE_72_channel_adapter_framework.md) | Channel adapter framework with Redis session state | [M10](../MILESTONES/M10_ussd_whatsapp_channels.md) | B | 7 | 3 days | [31](M5/ISSUE_31_clinics_nearby_postgis_search.md), [40](M6/ISSUE_40_join_queue_service_api.md) |
| [73](M10/ISSUE_73_ussd_menu_tree.md) | USSD webhook and menu tree (find, join, status, cancel) | [M10](../MILESTONES/M10_ussd_whatsapp_channels.md) | B | 8 | 3 days | [72](M10/ISSUE_72_channel_adapter_framework.md) |
| [74](M10/ISSUE_74_ussd_sessions_security.md) | USSD session resume, timeouts and gateway signature verification | [M10](../MILESTONES/M10_ussd_whatsapp_channels.md) | B | 9 | 2 days | [73](M10/ISSUE_73_ussd_menu_tree.md) |
| [75](M10/ISSUE_75_whatsapp_webhook_quick_replies.md) | WhatsApp Cloud API webhook with quick-reply flows | [M10](../MILESTONES/M10_ussd_whatsapp_channels.md) | B | 10 | 3 days | [72](M10/ISSUE_72_channel_adapter_framework.md) |
| [76](M10/ISSUE_76_whatsapp_templates_optin.md) | WhatsApp templates, opt-in capture and session-window handling | [M10](../MILESTONES/M10_ussd_whatsapp_channels.md) | B | 10 | 2 days | [66](M9/ISSUE_66_notification_templates_i18n.md), [75](M10/ISSUE_75_whatsapp_webhook_quick_replies.md) |
| [77](M10/ISSUE_77_i18n_menu_translations.md) | i18n framework and menu translations (5 languages) | [M10](../MILESTONES/M10_ussd_whatsapp_channels.md) | F | 8 | 3 days | [72](M10/ISSUE_72_channel_adapter_framework.md) |
| [78](M10/ISSUE_78_channel_simulators.md) | Channel simulators for credential-free local development | [M10](../MILESTONES/M10_ussd_whatsapp_channels.md) | B | 7 | 2 days | [73](M10/ISSUE_73_ussd_menu_tree.md), [75](M10/ISSUE_75_whatsapp_webhook_quick_replies.md) |
| [79](M10/ISSUE_79_channel_parity_tests.md) | Cross-channel parity tests and channel-mix analytics | [M10](../MILESTONES/M10_ussd_whatsapp_channels.md) | B | 11 | 2 days | [72](M10/ISSUE_72_channel_adapter_framework.md), [73](M10/ISSUE_73_ussd_menu_tree.md), [74](M10/ISSUE_74_ussd_sessions_security.md), [75](M10/ISSUE_75_whatsapp_webhook_quick_replies.md), [76](M10/ISSUE_76_whatsapp_templates_optin.md), [77](M10/ISSUE_77_i18n_menu_translations.md), [78](M10/ISSUE_78_channel_simulators.md) |
| [80](M11/ISSUE_80_appointment_slots_capacity.md) | Appointment slots and capacity model | [M11](../MILESTONES/M11_appointments_checkin_patient_care.md) | A | 8 | 3 days | [25](M4/ISSUE_25_queues_model_multiroom.md), [26](M4/ISSUE_26_services_catalogue_service_times.md) |
| [81](M11/ISSUE_81_booking_reschedule_auto_ticket.md) | Book, reschedule, cancel and auto-convert an appointment into a ticket | [M11](../MILESTONES/M11_appointments_checkin_patient_care.md) | A | 9 | 3 days | [80](M11/ISSUE_80_appointment_slots_capacity.md) |
| [82](M11/ISSUE_82_appointment_reminders_reply.md) | Appointment reminders with confirm/cancel by reply | [M11](../MILESTONES/M11_appointments_checkin_patient_care.md) | B | 11 | 2 days | [63](M9/ISSUE_63_notification_service_adapters.md), [81](M11/ISSUE_81_booking_reschedule_auto_ticket.md) |
| [83](M11/ISSUE_83_kiosk_self_checkin.md) | Self check-in kiosk and QR arrival check-in | [M11](../MILESTONES/M11_appointments_checkin_patient_care.md) | C | 9 | 3 days | [70](M9/ISSUE_70_qr_ticket_code.md), [81](M11/ISSUE_81_booking_reschedule_auto_ticket.md) |
| [84](M11/ISSUE_84_proxy_dependant_booking.md) | Proxy booking for dependants with recorded consent | [M11](../MILESTONES/M11_appointments_checkin_patient_care.md) | A | 10 | 2 days | [17](M3/ISSUE_17_patient_identity_otp.md), [81](M11/ISSUE_81_booking_reschedule_auto_ticket.md) |
| [85](M11/ISSUE_85_chronic_repeat_reminders.md) | Chronic and repeat-visit reminder schedules | [M11](../MILESTONES/M11_appointments_checkin_patient_care.md) | B | 11 | 2 days | [63](M9/ISSUE_63_notification_service_adapters.md), [82](M11/ISSUE_82_appointment_reminders_reply.md) |
| [86](M11/ISSUE_86_virtual_waiting_room.md) | Virtual waiting room and travel-time-aware call-forward | [M11](../MILESTONES/M11_appointments_checkin_patient_care.md) | A | 10 | 2 days | [42](M6/ISSUE_42_wait_time_estimation.md), [68](M9/ISSUE_68_patient_ticket_page.md) |
| [87](M11/ISSUE_87_post_visit_feedback.md) | Post-visit feedback survey and satisfaction reporting | [M11](../MILESTONES/M11_appointments_checkin_patient_care.md) | F | 12 | 2 days | [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md), [63](M9/ISSUE_63_notification_service_adapters.md) |
| [88](M12/ISSUE_88_daily_stats_worker.md) | Nightly `daily_queue_stats` aggregation worker | [M12](../MILESTONES/M12_reporting_analytics.md) | F | 9 | 3 days | [39](M6/ISSUE_39_tickets_model_sequence.md), [41](M6/ISSUE_41_ticket_lifecycle_state_machine.md) |
| [89](M12/ISSUE_89_clinic_reports_ui.md) | Clinic reports UI: wait time, no-show, channel mix, heatmap | [M12](../MILESTONES/M12_reporting_analytics.md) | F | 10 | 3 days | [48](M7/ISSUE_48_dashboard_shell_role_nav.md), [88](M12/ISSUE_88_daily_stats_worker.md) |
| [90](M12/ISSUE_90_live_operational_kpis.md) | Live operational KPIs on the manager dashboard | [M12](../MILESTONES/M12_reporting_analytics.md) | F | 10 | 2 days | [49](M7/ISSUE_49_front_desk_board_live.md), [88](M12/ISSUE_88_daily_stats_worker.md) |
| [91](M12/ISSUE_91_exports_scheduled_reports.md) | CSV/PDF export and scheduled weekly email report | [M12](../MILESTONES/M12_reporting_analytics.md) | F | 11 | 2 days | [89](M12/ISSUE_89_clinic_reports_ui.md) |
| [92](M12/ISSUE_92_district_aggregate_dashboard.md) | Anonymised district aggregate dashboard with small-cell suppression | [M12](../MILESTONES/M12_reporting_analytics.md) | F | 11 | 3 days | [88](M12/ISSUE_88_daily_stats_worker.md) |
| [93](M12/ISSUE_93_noshow_risk_staffing_insight.md) | No-show risk insight and staffing recommendation | [M12](../MILESTONES/M12_reporting_analytics.md) | F | 12 | 3 days | [82](M11/ISSUE_82_appointment_reminders_reply.md), [88](M12/ISSUE_88_daily_stats_worker.md) |
| [94](M12/ISSUE_94_reporting_contract_accuracy.md) | Reporting contract and figure-accuracy tests | [M12](../MILESTONES/M12_reporting_analytics.md) | F | 12 | 2 days | [88](M12/ISSUE_88_daily_stats_worker.md), [89](M12/ISSUE_89_clinic_reports_ui.md), [90](M12/ISSUE_90_live_operational_kpis.md), [91](M12/ISSUE_91_exports_scheduled_reports.md), [92](M12/ISSUE_92_district_aggregate_dashboard.md), [93](M12/ISSUE_93_noshow_risk_staffing_insight.md) |
| [95](M13/ISSUE_95_data_map_retention_purge.md) | Data map, retention policy and automatic purge jobs | [M13](../MILESTONES/M13_security_privacy_compliance.md) | F | 12 | 3 days | [20](M3/ISSUE_20_audit_log_admin_api.md), [53](M7/ISSUE_53_nurse_room_view_visit_notes.md) |
| [96](M13/ISSUE_96_dsar_export_erasure.md) | Data-subject access export and erasure workflow | [M13](../MILESTONES/M13_security_privacy_compliance.md) | F | 13 | 3 days | [17](M3/ISSUE_17_patient_identity_otp.md), [95](M13/ISSUE_95_data_map_retention_purge.md) |
| [97](M13/ISSUE_97_hardening_rate_limits_scanning.md) | Hardening: rate limits, security headers, dependency and secret scanning | [M13](../MILESTONES/M13_security_privacy_compliance.md) | E | 8 | 3 days | [16](M3/ISSUE_16_staff_signin_sessions_csrf.md), [40](M6/ISSUE_40_join_queue_service_api.md) |
| [98](M13/ISSUE_98_encryption_pii_protection.md) | Encryption in transit and at rest, field-level encryption for patient contacts | [M13](../MILESTONES/M13_security_privacy_compliance.md) | E | 9 | 2 days | [17](M3/ISSUE_17_patient_identity_otp.md), [95](M13/ISSUE_95_data_map_retention_purge.md) |
| [99](M13/ISSUE_99_tamper_evident_audit_viewer.md) | Tamper-evident (hash-chained) audit log and admin viewer UI | [M13](../MILESTONES/M13_security_privacy_compliance.md) | A | 12 | 2 days | [20](M3/ISSUE_20_audit_log_admin_api.md) |
| [100](M13/ISSUE_100_pen_test_remediation.md) | Authorised penetration test of staging and remediation | [M13](../MILESTONES/M13_security_privacy_compliance.md) | E | 11–12 | 4 days | [97](M13/ISSUE_97_hardening_rate_limits_scanning.md), [98](M13/ISSUE_98_encryption_pii_protection.md) |
| [101](M13/ISSUE_101_accessibility_audit_remediation.md) | WCAG 2.2 AA accessibility audit and remediation | [M13](../MILESTONES/M13_security_privacy_compliance.md) | C | 10 | 3 days | [59](M8/ISSUE_59_board_accessibility.md), [68](M9/ISSUE_68_patient_ticket_page.md) |
| [102](M14/ISSUE_102_production_infra_tls.md) | Production infrastructure, TLS, domains and edge protection | [M14](../MILESTONES/M14_production_pilot_golive.md) | E | 13 | 3 days | [11](M2/ISSUE_11_cd_staging_prod_approval.md), [98](M13/ISSUE_98_encryption_pii_protection.md) |
| [103](M14/ISSUE_103_backups_restore_drill.md) | Backups, restore drill and disaster-recovery runbook | [M14](../MILESTONES/M14_production_pilot_golive.md) | E | 13 | 2 days | [102](M14/ISSUE_102_production_infra_tls.md) |
| [104](M14/ISSUE_104_monitoring_alerting_status.md) | Monitoring, alerting, on-call rota and status page | [M14](../MILESTONES/M14_production_pilot_golive.md) | E | 13 | 2 days | [14](M2/ISSUE_14_monitoring_baseline_alerts.md), [102](M14/ISSUE_102_production_infra_tls.md) |
| [105](M14/ISSUE_105_load_soak_testing.md) | Load and soak testing of the morning-rush profile | [M14](../MILESTONES/M14_production_pilot_golive.md) | E | 13 | 3 days | [47](M6/ISSUE_47_queue_contract_concurrency_tests.md), [102](M14/ISSUE_102_production_infra_tls.md) |
| [106](M14/ISSUE_106_pilot_rollout_kit.md) | Pilot rollout kit: site survey, install guide, staff training pack | [M14](../MILESTONES/M14_production_pilot_golive.md) | F | 13 | 3 days | [61](M8/ISSUE_61_kiosk_device_registry.md), [102](M14/ISSUE_102_production_infra_tls.md) |
| [107](M14/ISSUE_107_support_incident_sla.md) | Support process, incident runbooks and internal SLA | [M14](../MILESTONES/M14_production_pilot_golive.md) | E | 13 | 2 days | [104](M14/ISSUE_104_monitoring_alerting_status.md), [106](M14/ISSUE_106_pilot_rollout_kit.md) |
| [108](M14/ISSUE_108_uat_clinic_staff.md) | User acceptance testing with clinic staff and remediation | [M14](../MILESTONES/M14_production_pilot_golive.md) | F | 14 | 4 days | [106](M14/ISSUE_106_pilot_rollout_kit.md), [107](M14/ISSUE_107_support_incident_sla.md) |
| [109](M14/ISSUE_109_capstone_deliverables.md) | Capstone deliverables: demo script, video, report, poster, presentation | [M14](../MILESTONES/M14_production_pilot_golive.md) | F | 14 | 5 days | [100](M13/ISSUE_100_pen_test_remediation.md), [105](M14/ISSUE_105_load_soak_testing.md), [108](M14/ISSUE_108_uat_clinic_staff.md) |

---

**Navigation:** [GitHub docs index](../README.md) · [Milestones](../MILESTONES/README.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Engineering non-negotiables](../../guideline.md)
