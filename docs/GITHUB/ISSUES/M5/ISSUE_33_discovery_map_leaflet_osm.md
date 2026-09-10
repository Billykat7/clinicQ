# Issue 33: Map view (Leaflet + OpenStreetMap) with clustering and directions hand-off

> **In short:** Patients who think in maps get one: the same filtered clinics as pins, with a hand-off to their phone's own directions app.

| | |
|---|---|
| **Milestone** | [M5: Discovery & Geolocation](../../MILESTONES/M5_discovery_geolocation.md) |
| **Sprint** | 4 (weeks 7–8) |
| **Owner** | C, Frontend/Patient (backup: D, Frontend/Clinic) |
| **Area** | Frontend / Discovery |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 32](../M5/ISSUE_32_discovery_list_ui_sector_toggle.md): Discovery list UI with public/private/all toggle and sector badges |
| **Unblocks** | [Issue 38](../M5/ISSUE_38_discovery_analytics_contract.md): Discovery analytics events and discovery OpenAPI contract |

## Context

A map answers 'which of these is actually on my taxi route?' in a way a list cannot. Leaflet with
OpenStreetMap tiles keeps it free and licence-clean, and the map is deliberately an enhancement layered
on top of a list that already works without it.

## Starting point

- Leaflet is already vendored at `src/static/vendor/leaflet/`, so no CDN is needed.
- Map tiles come from the OpenStreetMap tile host, which the CSP already allows as an image source.

## Scope

- Leaflet map with OpenStreetMap tiles, user location marker and clinic pins
- Pin clustering, sector-coloured markers with distinct shapes, and a tap-to-preview card
- List/map toggle preserving filters and scroll position
- 'Directions' hand-off to the device's default maps application
- Tile-failure fallback that degrades to the list with an explanatory message

## Out of scope

- The list page and its filters (Issue 32).
- Turn-by-turn routing: the directions button only hands off to the device.

## Acceptance criteria

- [ ] The map shows all clinics currently in the filtered list, and no others
- [ ] Tapping a pin opens a preview card with the same data as the list card
- [ ] Switching between list and map preserves filters
- [ ] Markers are distinguishable by shape as well as colour
- [ ] Tile loading failure falls back to the list rather than showing a blank grey area
- [ ] OpenStreetMap attribution is displayed as the licence requires

## How to verify

1. Filter to Private, switch to the map: only the private clinics from the list are pinned.
2. Block the tile host in dev tools: the page falls back to the list with an explanation.
3. Switch back to the list: the filters and scroll position are unchanged.

## Files touched

- `src/templates/discover/map.html`
- `src/static/js/discover-map.js`
- `src/static/vendor/leaflet/`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #33
