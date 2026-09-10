# Issue 8: Test factories and `seed_dev_data.py` demo dataset

> **In short:** A believable demo world on every laptop: real Gauteng and KZN clinics with queues and a day of ticket history, created by one idempotent command.

| | |
|---|---|
| **Milestone** | [M1: Foundation & Local CI](../../MILESTONES/M1_foundation_local_ci.md) |
| **Sprint** | 2 (weeks 3–4) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Backend / Quality |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 3](../M1/ISSUE_3_sqlalchemy_alembic_baseline.md): Async SQLAlchemy 2.x + Alembic baseline (PostGIS extension enabled)<br>[Issue 4](../M1/ISSUE_4_shared_kernel_enums_time_errors.md): Shared kernel: enums, error envelope, IDs, Africa/Johannesburg datetimes |
| **Unblocks** | [Issue 9](../M2/ISSUE_9_actions_ci_lint_type_test.md): GitHub Actions CI: lint, type-check, test with PostGIS + Redis services |

## Context

Every teammate needs a believable clinic, queue and ticket to develop against, and every test needs
to build one in a line. A shared factory and seed script prevents six private copies of `create_test_clinic()`
and gives the demo a dataset that looks real on screen.

## Starting point

- `tests/conftest.py` exists; there are no factories yet.
- The kernel's seeding lives in `scripts/db/seed-dev-user.py` (one dev user) and `scripts/db/seed_rbac.py` (`make seed-rbac`). Follow the same pattern for `seed_dev_data.py`.
- Sites, queues and tickets do not exist until Issues 23, 25 and 39, so build the factories for them as those land, or against agreed stubs.

## Scope

- Factory helpers for site, queue, staff user, patient and ticket, with sensible defaults and overrides
- `scripts/seed_dev_data.py`: at least 8 clinics with real Gauteng/KZN coordinates, mixed public and private, 2–4 queues each
- Seeded tickets spread across the day so wait-time estimates and the heatmap have something to show
- A seeded staff account per role, with credentials printed by the script
- Idempotent seeding: re-running updates rather than duplicating

## Out of scope

- Production data or real patient records, ever.
- The area and suburb dataset (Issue 34).

## Acceptance criteria

- [ ] `uv run scripts/seed_dev_data.py` populates a fresh database in under 30 seconds
- [ ] The seeded clinics appear at sensible real-world coordinates on a map
- [ ] Factories produce valid objects with a single call and no required arguments
- [ ] Re-running the seed script does not create duplicate clinics
- [ ] Seeded credentials are clearly marked as development-only and refuse to run against production
- [ ] At least one seeded clinic has enough ticket history for a non-trivial wait estimate

## How to verify

1. Run the seed script twice on a fresh database: the second run creates no duplicates.
2. Point the script at a production `DATABASE_URL`: it refuses to run.
3. Open the discovery map (once Issue 33 lands): seeded clinics sit at their real coordinates.

## Files touched

- `tests/factories.py`
- `tests/conftest.py`
- `scripts/db/seed_dev_data.py`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #8
