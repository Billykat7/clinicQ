# Issue 8: Test factories and `seed_dev_data.py` demo dataset

**Area:** Backend / Quality
**Milestone:** M1 - Foundation & Local CI
**Owner role:** DevOps/QA Lead
**Depends on:** Issues 3, 4
**Estimate:** 2 days
**Status:** Planned

## Context

Every teammate needs a believable clinic, queue and ticket to develop against, and every test needs
to build one in a line. A shared factory and seed script prevents six private copies of `create_test_clinic()`
and gives the demo a dataset that looks real on screen.

## Scope

- Factory helpers for site, queue, staff user, patient and ticket, with sensible defaults and overrides
- `scripts/seed_dev_data.py`: at least 8 clinics with real Gauteng/KZN coordinates, mixed public and private, 2–4 queues each
- Seeded tickets spread across the day so wait-time estimates and the heatmap have something to show
- A seeded staff account per role, with credentials printed by the script
- Idempotent seeding: re-running updates rather than duplicating

## Acceptance criteria

- [ ] `uv run scripts/seed_dev_data.py` populates a fresh database in under 30 seconds
- [ ] The seeded clinics appear at sensible real-world coordinates on a map
- [ ] Factories produce valid objects with a single call and no required arguments
- [ ] Re-running the seed script does not create duplicate clinics
- [ ] Seeded credentials are clearly marked as development-only and refuse to run against production
- [ ] At least one seeded clinic has enough ticket history for a non-trivial wait estimate

## Files touched

- `tests/factories.py`
- `scripts/seed_dev_data.py`
- `tests/conftest.py`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #8
