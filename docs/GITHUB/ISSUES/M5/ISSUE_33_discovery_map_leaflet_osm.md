# Issue 33: Map view (Leaflet + OpenStreetMap) with clustering and directions hand-off

**Area:** Frontend / Discovery
**Milestone:** M5 - Discovery & Geolocation
**Owner role:** Frontend (Patient) Dev
**Depends on:** Issue 32
**Estimate:** 2 days
**Status:** Planned

## Context

A map answers 'which of these is actually on my taxi route?' in a way a list cannot. Leaflet with
OpenStreetMap tiles keeps it free and licence-clean, and the map is deliberately an enhancement layered
on top of a list that already works without it.

## Scope

- Leaflet map with OpenStreetMap tiles, user location marker and clinic pins
- Pin clustering, sector-coloured markers with distinct shapes, and a tap-to-preview card
- List/map toggle preserving filters and scroll position
- 'Directions' hand-off to the device's default maps application
- Tile-failure fallback that degrades to the list with an explanatory message

## Acceptance criteria

- [ ] The map shows all clinics currently in the filtered list, and no others
- [ ] Tapping a pin opens a preview card with the same data as the list card
- [ ] Switching between list and map preserves filters
- [ ] Markers are distinguishable by shape as well as colour
- [ ] Tile loading failure falls back to the list rather than showing a blank grey area
- [ ] OpenStreetMap attribution is displayed as the licence requires

## Files touched

- `app/templates/discover/map.html`
- `app/static/js/map.js`
- `app/static/vendor/leaflet/`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #33
