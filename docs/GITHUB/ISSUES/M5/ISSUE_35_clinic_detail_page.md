# Issue 35: Clinic detail page: hours, live queue length, services, contact

> **In short:** Everything a patient needs before deciding to go: today's hours, each queue's length and wait range, services, contact details, and a join button that explains itself when it is disabled.

| | |
|---|---|
| **Milestone** | [M5: Discovery & Geolocation](../../MILESTONES/M5_discovery_geolocation.md) |
| **Sprint** | 5 (weeks 9–10) |
| **Owner** | C, Frontend/Patient (backup: D, Frontend/Clinic) |
| **Area** | Frontend / Discovery |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 24](../M4/ISSUE_24_opening_hours_closures.md): Opening hours, holiday calendar and temporary-closure broadcast<br>[Issue 31](../M5/ISSUE_31_clinics_nearby_postgis_search.md): `GET /api/clinics/nearby`: PostGIS radius search with distance and ETA |
| **Unblocks** | [Issue 38](../M5/ISSUE_38_discovery_analytics_contract.md): Discovery analytics events and discovery OpenAPI contract |

## Context

The page where a patient decides to commit to a clinic and starts the join flow. It has to answer four
questions immediately: is it open, how long is the wait, what does it treat, and how do I get there.

## Starting point

- Same page family as Issue 32 (`src/web/discover.py`, `src/templates/discover/`).
- Hours come from `is_open_now()` and `next_open_at()` (Issue 24); wait ranges come from the estimator (Issue 42) once it exists.

## Scope

- Clinic detail page: name, sector badge, address, phone, today's hours, services, live queue length and wait range
- Per-queue breakdown so a patient can see that pharmacy is short while the doctor queue is long
- Prominent 'Join the queue' action, disabled with a reason when the clinic is closed or full
- Directions link and a tap-to-call number
- Payment and medical-aid information for private clinics, with the clinic-reported disclaimer

## Out of scope

- Joining the queue itself (Issue 40).
- Payment and medical-aid details (Issue 37 adds that block).

## Acceptance criteria

- [ ] Wait times are shown as a range, never a single number
- [ ] The join action is disabled with an explanation when joining is not possible
- [ ] The page is fully readable and usable on a 320 px-wide screen
- [ ] Live figures refresh via htmx without a full reload
- [ ] Medical-aid information is labelled as clinic-reported with a confirm-with-the-clinic note
- [ ] The page passes an automated accessibility check

## How to verify

1. Open a closed clinic: the join button is disabled and says when it opens.
2. Resize to 320 px wide: everything is readable with no horizontal scroll.
3. Leave the page open: live figures refresh without a reload.

## Files touched

- `src/web/discover.py`
- `src/templates/discover/detail.html`
- `src/templates/discover/_queue_summary.html`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #35
