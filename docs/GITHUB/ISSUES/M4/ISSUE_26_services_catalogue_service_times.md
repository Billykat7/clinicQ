# Issue 26: Services catalogue with expected service times

> **In short:** Each clinic lists what it offers and how long each service usually takes, which gives wait estimates a sensible starting point before real data exists.

| | |
|---|---|
| **Milestone** | [M4: Clinics, Queues & Configuration](../../MILESTONES/M4_clinics_queues_config.md) |
| **Sprint** | 5 (weeks 9–10) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Clinics |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 25](../M4/ISSUE_25_queues_model_multiroom.md): `queues` model: multi-room, multi-service queues per site |
| **Unblocks** | [Issue 30](../M4/ISSUE_30_sites_openapi_contract_tests.md): `sites` OpenAPI contract and module tests<br>[Issue 42](../M6/ISSUE_42_wait_time_estimation.md): Wait-time estimation service and `wait_time_samples`<br>[Issue 80](../M11/ISSUE_80_appointment_slots_capacity.md): Appointment slots and capacity model |

## Context

Wait estimates in M6 need a starting number before a clinic has any history. A small services catalogue
with an expected duration per service gives the estimator a sane seed on a clinic's first morning, and
gives discovery something meaningful to show beyond a queue length.

## Starting point

- Greenfield, inside `src/modules/sites/`. F prepares the catalogue seed data in sprint 3.
- Name the model `ClinicService` (`clinic_service.py`): every module already has a `service.py`, and a model called `Service` will be misread in reviews.

## Scope

- `services` catalogue per site: name, category, expected minutes, active flag
- Optional link from a queue to the services it handles
- Seed data for common primary-care services (consultation, immunisation, chronic medication collection, antenatal, HIV/TB services)
- Expected duration used as the estimator's prior until real samples exist
- Services surfaced on the clinic detail page in discovery

## Out of scope

- The wait estimator (Issue 42), which reads the expected minutes.
- The clinic detail page (Issue 35), which displays the list.

## Acceptance criteria

- [ ] A new clinic gets a seeded services catalogue it can edit
- [ ] The wait estimator falls back to the service's expected minutes when fewer than N samples exist
- [ ] Services appear on the clinic detail page and in the channel menus
- [ ] Expected minutes are validated to a sensible range
- [ ] Deactivating a service removes it from new joins but not from history
- [ ] The catalogue is site-scoped like every other clinic resource

## How to verify

1. A newly onboarded clinic has the seeded catalogue and can edit it.
2. Set expected minutes to 0 or 600: rejected.
3. Deactivate a service: it disappears from new joins but past tickets still show it.

## Files touched

- `src/modules/sites/catalogue.py`
- `src/database/models/clinic_service.py`
- `alembic/versions/NNNN_clinic_services_seed.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #26
