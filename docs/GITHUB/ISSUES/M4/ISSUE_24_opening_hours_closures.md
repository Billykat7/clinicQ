# Issue 24: Opening hours, holiday calendar and temporary-closure broadcast

**Area:** Backend / Clinics
**Milestone:** M4 - Clinics, Queues & Configuration
**Owner role:** Backend Lead
**Depends on:** Issue 23
**Estimate:** 2 days
**Status:** Planned

## Context

A clinic that shows as open when its doors are locked sends a patient on a wasted trip. Opening hours,
public holidays and an ad-hoc closure broadcast together produce one honest `is_open_now` answer that
discovery, the board and the channels all read.

## Scope

- Weekly opening-hours schedule per site, with support for a lunch break and split shifts
- Public-holiday calendar with a per-site override for clinics that open on holidays
- Ad-hoc temporary closure with a reason and an end time, triggerable by a clinic manager
- `is_open_now(site)` and `next_open_at(site)` helpers in `Africa/Johannesburg`
- Closure broadcast notifying patients already holding a ticket for that site

## Acceptance criteria

- [ ] `is_open_now()` respects weekly hours, holidays and ad-hoc closures in that order of precedence
- [ ] A closure immediately stops new joins for that site across all four channels
- [ ] Patients holding a ticket at a closing site are notified with the reason
- [ ] Discovery shows 'closed, opens 07:00 tomorrow' rather than hiding the clinic entirely
- [ ] All time comparisons use `Africa/Johannesburg`, proven by a test crossing midnight
- [ ] Holiday data is seeded for the current and next year

## Files touched

- `app/database/models/site_hours.py`
- `app/services/site_hours.py`
- `app/api/sites.py`
- `tests/unit/test_opening_hours.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #24
