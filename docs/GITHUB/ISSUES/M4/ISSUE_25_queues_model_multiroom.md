# Issue 25: `queues` model: multi-room, multi-service queues per site

> **In short:** A clinic can run several named queues (triage, a consulting room, the pharmacy window), which is what patients actually join.

| | |
|---|---|
| **Milestone** | [M4: Clinics, Queues & Configuration](../../MILESTONES/M4_clinics_queues_config.md) |
| **Sprint** | 5 (weeks 9–10) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Clinics |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 23](../M4/ISSUE_23_sites_model_profile_crud.md): `sites` model with PostGIS location and clinic profile CRUD |
| **Unblocks** | [Issue 26](../M4/ISSUE_26_services_catalogue_service_times.md): Services catalogue with expected service times<br>[Issue 28](../M4/ISSUE_28_staff_site_room_assignment.md): Staff-to-site and room assignment<br>[Issue 30](../M4/ISSUE_30_sites_openapi_contract_tests.md): `sites` OpenAPI contract and module tests<br>[Issue 36](../M5/ISSUE_36_queue_snapshot_cache.md): `site_queue_snapshot` Redis cache with warm and invalidate paths<br>[Issue 39](../M6/ISSUE_39_tickets_model_sequence.md): `tickets` model and concurrency-safe daily sequence numbering<br>[Issue 45](../M6/ISSUE_45_queue_transfer.md): Transfer between queues without re-joining (triage → doctor → pharmacy)<br>[Issue 80](../M11/ISSUE_80_appointment_slots_capacity.md): Appointment slots and capacity model |

## Context

**Land this alongside Issue 23.** A real clinic visit is triage → doctor → pharmacy, not one line. Every
ticket in M6 belongs to a queue, so this model blocks the entire queue engine and everything downstream
of it.

## Starting point

- Greenfield. Put the model in `src/database/models/queue.py` and the service in `src/modules/queue/`, because M6's queue engine grows out of the same module.
- **Sprint 5, day 3 is a team commitment**, together with Issue 23: D's board and the M6 engine both start from this model.

## Scope

- `queues` model: site, name, kind (triage/consultation/pharmacy/other), room label, display order, active flag
- Queue CRUD scoped to the site, with reordering
- Deactivation that preserves historical tickets and excludes the queue from new joins
- A default queue set created automatically when a site is onboarded
- Per-queue configuration: expected service minutes, maximum daily capacity, whether remote joins are allowed

## Out of scope

- Tickets and the queue engine (Issue 39 onwards).
- Assigning staff to queues (Issue 28).

## Acceptance criteria

- [ ] A site can carry any number of named queues, ordered for display
- [ ] Deactivating a queue hides it from joins while keeping its history queryable
- [ ] A newly onboarded clinic starts with a sensible default queue set
- [ ] A queue that disallows remote joins accepts walk-ins only, enforced server-side
- [ ] Queue names are unique per site
- [ ] Queue factories exist for the M6 and M7 test suites

## How to verify

1. Onboard a clinic: it starts with the default queue set.
2. Deactivate a queue: new joins are refused, and yesterday's tickets for it still load.
3. Join a walk-in-only queue through the API as a remote patient: refused by the server.

## Files touched

- `src/modules/queue/`
- `src/database/models/queue.py`
- `alembic/versions/NNNN_queues.py`
- `tests/factories.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #25
