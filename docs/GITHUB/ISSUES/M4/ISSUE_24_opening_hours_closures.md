# Issue 24: Opening hours, holiday calendar and temporary-closure broadcast

> **In short:** The system always knows whether a clinic is open right now, when it opens next, and tells waiting patients if it closes unexpectedly.

| | |
|---|---|
| **Milestone** | [M4: Clinics, Queues & Configuration](../../MILESTONES/M4_clinics_queues_config.md) |
| **Sprint** | 5 (weeks 9–10) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Clinics |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 23](../M4/ISSUE_23_sites_model_profile_crud.md): `sites` model with PostGIS location and clinic profile CRUD |
| **Unblocks** | [Issue 29](../M4/ISSUE_29_clinic_onboarding_verification.md): Clinic onboarding and platform-admin verification workflow<br>[Issue 30](../M4/ISSUE_30_sites_openapi_contract_tests.md): `sites` OpenAPI contract and module tests<br>[Issue 35](../M5/ISSUE_35_clinic_detail_page.md): Clinic detail page: hours, live queue length, services, contact<br>[Issue 54](../M7/ISSUE_54_manager_settings_ui.md): Clinic manager settings UI (profile, hours, display mode, staff, services) |

## Context

A clinic that shows as open when its doors are locked sends a patient on a wasted trip. Opening hours,
public holidays and an ad-hoc closure broadcast together produce one honest `is_open_now` answer that
discovery, the board and the channels all read.

## Starting point

- Greenfield, inside `src/modules/sites/`. Use the time helpers from Issue 4 for every comparison.
- F prepares the public-holiday dataset in sprint 4, so seed from that rather than typing dates in.

## Scope

- Weekly opening-hours schedule per site, with support for a lunch break and split shifts
- Public-holiday calendar with a per-site override for clinics that open on holidays
- Ad-hoc temporary closure with a reason and an end time, triggerable by a clinic manager
- `is_open_now(site)` and `next_open_at(site)` helpers in `Africa/Johannesburg`
- Closure broadcast notifying patients already holding a ticket for that site

## Out of scope

- Discovery's display of "closed, opens 07:00" (Issue 32 renders what this returns).
- Notification delivery itself (Issue 63); this issue only raises the closure event.

## Acceptance criteria

- [ ] `is_open_now()` respects weekly hours, holidays and ad-hoc closures in that order of precedence
- [ ] A closure immediately stops new joins for that site across all four channels
- [ ] Patients holding a ticket at a closing site are notified with the reason
- [ ] Discovery shows 'closed, opens 07:00 tomorrow' rather than hiding the clinic entirely
- [ ] All time comparisons use `Africa/Johannesburg`, proven by a test crossing midnight
- [ ] Holiday data is seeded for the current and next year

## How to verify

1. Unit tests cover weekly hours, a holiday override and an ad-hoc closure, including one crossing midnight in Johannesburg.
2. Close a site with patients waiting: joins stop on every channel and each ticket holder is notified with the reason.

## Files touched

- `src/modules/sites/hours.py`
- `src/database/models/site_hours.py`
- `alembic/versions/NNNN_site_hours.py`
- `tests/unit/sites/test_opening_hours.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #24
