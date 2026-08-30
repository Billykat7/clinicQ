# Issue 25: `queues` model: multi-room, multi-service queues per site

**Area:** Backend / Clinics
**Milestone:** M4 - Clinics, Queues & Configuration
**Owner role:** Backend Lead
**Depends on:** Issue 23
**Estimate:** 2 days
**Status:** Planned

## Context

**Land this alongside Issue 23.** A real clinic visit is triage → doctor → pharmacy, not one line. Every
ticket in M6 belongs to a queue, so this model blocks the entire queue engine and everything downstream
of it.

## Scope

- `queues` model: site, name, kind (triage/consultation/pharmacy/other), room label, display order, active flag
- Queue CRUD scoped to the site, with reordering
- Deactivation that preserves historical tickets and excludes the queue from new joins
- A default queue set created automatically when a site is onboarded
- Per-queue configuration: expected service minutes, maximum daily capacity, whether remote joins are allowed

## Acceptance criteria

- [ ] A site can carry any number of named queues, ordered for display
- [ ] Deactivating a queue hides it from joins while keeping its history queryable
- [ ] A newly onboarded clinic starts with a sensible default queue set
- [ ] A queue that disallows remote joins accepts walk-ins only, enforced server-side
- [ ] Queue names are unique per site
- [ ] Queue factories exist for the M6 and M7 test suites

## Files touched

- `app/database/models/queue.py`
- `app/services/queues.py`
- `app/api/queues.py`
- `migrations/versions/*_queues.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #25
