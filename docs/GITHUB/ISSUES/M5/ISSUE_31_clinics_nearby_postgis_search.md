# Issue 31: `GET /api/clinics/nearby`: PostGIS radius search with distance and ETA

> **In short:** Given a location, the system returns the nearest verified clinics with distance, rough travel time and live queue length, as data every channel can reuse.

| | |
|---|---|
| **Milestone** | [M5: Discovery & Geolocation](../../MILESTONES/M5_discovery_geolocation.md) |
| **Sprint** | 5 (weeks 9–10); the sprint plan puts this in **C**'s lane, see the note below |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Discovery |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 23](../M4/ISSUE_23_sites_model_profile_crud.md): `sites` model with PostGIS location and clinic profile CRUD |
| **Unblocks** | [Issue 32](../M5/ISSUE_32_discovery_list_ui_sector_toggle.md): Discovery list UI with public/private/all toggle and sector badges<br>[Issue 34](../M5/ISSUE_34_area_fallback_search.md): Area/suburb fallback search for patients without GPS<br>[Issue 35](../M5/ISSUE_35_clinic_detail_page.md): Clinic detail page: hours, live queue length, services, contact<br>[Issue 36](../M5/ISSUE_36_queue_snapshot_cache.md): `site_queue_snapshot` Redis cache with warm and invalidate paths<br>[Issue 38](../M5/ISSUE_38_discovery_analytics_contract.md): Discovery analytics events and discovery OpenAPI contract<br>[Issue 72](../M10/ISSUE_72_channel_adapter_framework.md): Channel adapter framework with Redis session state |

> **Note:** The spec names A as owner; the sprint plan has C wiring the UI to this API in sprint 5. A owns the query and endpoint, C the page that calls it (Issue 32).

## Context

The search service is the shared core of discovery. Four channels call it: the web page, the map, the
USSD menu and the WhatsApp bot, so it lives in the service layer and returns plain data, never HTML.
A GiST-indexed `ST_DWithin` keeps it fast as the directory grows.

## Starting point

- New module: `src/modules/discovery/`. It reads `sites` (Issue 23) and never writes to it.
- Return plain data from the service; the web page (Issue 32), USSD (Issue 73) and WhatsApp (Issue 75) all call the same function.
- Seed 500 clinics with the factories from Issue 8 for the performance criterion.

## Scope

- `find_nearby_sites(lat, lon, radius_m, sector, limit)` using `ST_DWithin` with distance ordering
- Straight-line distance plus a rough travel-time estimate, both returned per result
- Sector filter (`public` / `private` / `all`) and an open-now filter
- Live queue length and current wait estimate joined from the snapshot cache
- `GET /api/clinics/nearby` exposing the service, with pagination and a maximum radius

## Out of scope

- The list and map pages (Issues 32, 33).
- Searching by suburb instead of GPS (Issue 34).
- The live-length cache (Issue 36); until it lands, read the count directly.

## Acceptance criteria

- [ ] A search from a coordinate returns clinics within the radius, nearest first
- [ ] The query uses the GiST index, confirmed by an `EXPLAIN` assertion in a test
- [ ] Search completes in under 200 ms with 500 seeded clinics
- [ ] Only `verified` sites are returned
- [ ] The service returns data structures, never rendered HTML, so channels can reuse it
- [ ] Radius is capped server-side so a caller cannot request the entire country

## How to verify

1. `GET /api/v1/clinics/nearby?lat=-26.2&lon=27.9&radius_m=5000`: nearest first, verified sites only.
2. Ask for a 5,000 km radius: capped by the server.
3. The integration test asserts the `EXPLAIN` plan uses the GiST index and times the 500-clinic search.

## Files touched

- `src/modules/discovery/`
- `tests/integration/discovery/test_nearby_search.py`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #31
