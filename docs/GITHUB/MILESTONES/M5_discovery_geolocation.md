# Milestone 5: Discovery & Geolocation

**Status:** 📋 planned · **Phase:** Semester 1 · Sprint 5–6 · **Suggested tag:** `v0.5.0`
**Primary owner:** Frontend (Patient) Dev · Backend Lead
**Depends on:** M4
**Blocks:** M9 (the patient PWA links from discovery), M10 (USSD/WhatsApp reuse the same search service)

## Goal

Let a patient find the right clinic before thinking about a queue: a PostGIS radius search from GPS or a typed suburb, a public/private/all toggle, a map and list view, live queue length per clinic, and a self-reported payment/medical-aid filter for private clinics.

## Why this milestone exists

The original ClinicQueue brief was queue management for one clinic. The discovery layer is what turns
it into a product a patient would open unprompted, closer to "Maps for clinics, with a live queue
attached" than to practice-management software, and the thing no incumbent
(Qminder, Qless, Qmatic, NHS e-RS) offers at ordinary clinic scale in this market.

The **search service is channel-agnostic on purpose**: the same `find_nearby_sites()` call serves the
web page, the USSD menu and the WhatsApp bot, so M10 adds a thin adapter rather than a second search
implementation. The medical-aid filter is explicitly a **self-reported directory attribute** with a
"confirm with the clinic" disclaimer, not an eligibility check, because sending a patient to a
clinic that will not take their scheme is worse than not filtering at all.

## Scope

- `find_nearby_sites()` service: `ST_DWithin` + distance ordering, open/closed, live queue length
- Discovery list UI with sector toggle (Public / Private / All) and clear sector badges
- Map view on Leaflet + OpenStreetMap tiles with pin clustering and a 'directions' hand-off
- Area/suburb fallback for patients who decline GPS, using an area centroid
- Clinic detail page: hours, queue length, expected wait, services, phone, directions
- `site_queue_snapshot` cache in Redis so list rendering never runs a per-site ticket count
- `site_payment_profile`: accepts cash/card, self-reported medical-aid tags, copay notice, filterable only under 'Private'
- Discovery analytics events (viewed vs joined) and a discovery OpenAPI contract

## Issues

| # | Title |
|---|-------|
| 31 | `GET /api/clinics/nearby`: PostGIS radius search with distance and ETA |
| 32 | Discovery list UI with public/private/all toggle and sector badges |
| 33 | Map view (Leaflet + OpenStreetMap) with clustering and directions hand-off |
| 34 | Area/suburb fallback search for patients without GPS |
| 35 | Clinic detail page: hours, live queue length, services, contact |
| 36 | `site_queue_snapshot` Redis cache with warm and invalidate paths |
| 37 | Payment and medical-aid directory filter (private clinics only) |
| 38 | Discovery analytics events and discovery OpenAPI contract |

## Exit criteria

- [ ] Searching from a coordinate returns clinics within the radius, nearest first, in under 200 ms with 500 seeded clinics
- [ ] The sector toggle filters correctly, and payment filters are hidden entirely when 'Public' is selected
- [ ] Declining GPS still produces results via a typed suburb name
- [ ] Each result shows a live queue length no more than 30 seconds stale
- [ ] The map degrades to the list view on a slow connection rather than blocking the page
- [ ] A medical-aid tag is visibly labelled as clinic-reported, with a confirm-with-the-clinic disclaimer

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M5/)
