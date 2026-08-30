# Issue 35: Clinic detail page: hours, live queue length, services, contact

**Area:** Frontend / Discovery
**Milestone:** M5 - Discovery & Geolocation
**Owner role:** Frontend (Patient) Dev
**Depends on:** Issues 31, 24
**Estimate:** 2 days
**Status:** Planned

## Context

The page where a patient decides to commit to a clinic and starts the join flow. It has to answer four
questions immediately: is it open, how long is the wait, what does it treat, and how do I get there.

## Scope

- Clinic detail page: name, sector badge, address, phone, today's hours, services, live queue length and wait range
- Per-queue breakdown so a patient can see that pharmacy is short while the doctor queue is long
- Prominent 'Join the queue' action, disabled with a reason when the clinic is closed or full
- Directions link and a tap-to-call number
- Payment and medical-aid information for private clinics, with the clinic-reported disclaimer

## Acceptance criteria

- [ ] Wait times are shown as a range, never a single number
- [ ] The join action is disabled with an explanation when joining is not possible
- [ ] The page is fully readable and usable on a 320 px-wide screen
- [ ] Live figures refresh via htmx without a full reload
- [ ] Medical-aid information is labelled as clinic-reported with a confirm-with-the-clinic note
- [ ] The page passes an automated accessibility check

## Files touched

- `app/web/discover/routes.py`
- `app/templates/discover/detail.html`
- `app/templates/discover/_queue_summary.html`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #35
