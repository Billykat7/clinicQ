# Issue 32: Discovery list UI with public/private/all toggle and sector badges

> **In short:** The first screen a patient sees: nearby clinics, public or private, with a live queue length and an honest wait range, fast on a cheap phone.

| | |
|---|---|
| **Milestone** | [M5: Discovery & Geolocation](../../MILESTONES/M5_discovery_geolocation.md) |
| **Sprint** | 3–5 (weeks 5–10) |
| **Owner** | C, Frontend/Patient (backup: D, Frontend/Clinic) |
| **Area** | Frontend / Discovery |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 31](../M5/ISSUE_31_clinics_nearby_postgis_search.md): `GET /api/clinics/nearby`: PostGIS radius search with distance and ETA |
| **Unblocks** | [Issue 33](../M5/ISSUE_33_discovery_map_leaflet_osm.md): Map view (Leaflet + OpenStreetMap) with clustering and directions hand-off<br>[Issue 37](../M5/ISSUE_37_payment_medical_aid_filter.md): Payment and medical-aid directory filter (private clinics only)<br>[Issue 38](../M5/ISSUE_38_discovery_analytics_contract.md): Discovery analytics events and discovery OpenAPI contract |

## Context

The first screen a patient sees. It has to work on a cheap Android phone on a weak connection, so it
is server-rendered with htmx, shows the three things that actually drive the decision (distance, queue
length, open or closed), and never blocks on a map tile.

## Starting point

- Build against the fixture data agreed with A until Issue 31 merges (sprint 3), then switch to the real API.
- Pages are server-rendered with htmx swaps: add a `src/web/discover.py` router and `src/templates/discover/`, following the CSP rules (no inline scripts or styles) that the kernel's guard tests enforce.
- The kernel's loading skeletons and toasts (`src/static/js/ui-feedback.js`) cover the loading and error states.

## Scope

- Mobile-first list of nearby clinics: name, sector badge, distance, live queue length, wait estimate, open/closed
- Public / Private / All toggle that re-renders the list via an htmx swap without a full page load
- Location permission prompt with a clear explanation and a graceful decline path
- Loading skeletons, an empty state ('no clinics within 10 km. Try a wider radius') and an error state
- Radius selector and sort options (nearest, shortest queue)

## Out of scope

- The map (Issue 33) and the clinic detail page (Issue 35).
- The suburb search behind the "decline location" path (Issue 34 provides the service).

## Acceptance criteria

- [ ] The list renders usable content in under 2 seconds on a throttled 3G profile
- [ ] Toggling sector re-renders the list without a full page reload
- [ ] Declining location shows the area-search fallback rather than a dead end
- [ ] Empty and error states are designed, not default browser output
- [ ] Sector badges are distinguishable without relying on colour alone
- [ ] The page is fully usable with a keyboard and passes an automated accessibility check

## How to verify

1. Throttle to Slow 3G in dev tools: useful content within 2 seconds.
2. Toggle Public / Private / All: the list changes with no full reload.
3. Decline the location prompt: the suburb search appears instead of an empty page.
4. Tab through the page with the keyboard only, and run an automated accessibility check.

## Files touched

- `src/web/discover.py`
- `src/templates/discover/list.html`
- `src/templates/discover/_clinic_card.html`
- `src/static/css/`
- `tests/integration/discovery/test_discover_pages.py`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #32
