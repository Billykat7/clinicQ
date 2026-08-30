# Issue 26: Services catalogue with expected service times

**Area:** Backend / Clinics
**Milestone:** M4 - Clinics, Queues & Configuration
**Owner role:** Backend Lead
**Depends on:** Issue 25
**Estimate:** 1 day
**Status:** Planned

## Context

Wait estimates in M6 need a starting number before a clinic has any history. A small services catalogue
with an expected duration per service gives the estimator a sane seed on a clinic's first morning, and
gives discovery something meaningful to show beyond a queue length.

## Scope

- `services` catalogue per site: name, category, expected minutes, active flag
- Optional link from a queue to the services it handles
- Seed data for common primary-care services (consultation, immunisation, chronic medication collection, antenatal, HIV/TB services)
- Expected duration used as the estimator's prior until real samples exist
- Services surfaced on the clinic detail page in discovery

## Acceptance criteria

- [ ] A new clinic gets a seeded services catalogue it can edit
- [ ] The wait estimator falls back to the service's expected minutes when fewer than N samples exist
- [ ] Services appear on the clinic detail page and in the channel menus
- [ ] Expected minutes are validated to a sensible range
- [ ] Deactivating a service removes it from new joins but not from history
- [ ] The catalogue is site-scoped like every other clinic resource

## Files touched

- `app/database/models/service.py`
- `app/services/catalogue.py`
- `migrations/versions/*_services_seed.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #26
