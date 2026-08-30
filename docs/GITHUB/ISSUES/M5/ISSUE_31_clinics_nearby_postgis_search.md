# Issue 31: `GET /api/clinics/nearby`: PostGIS radius search with distance and ETA

**Area:** Backend / Discovery
**Milestone:** M5 - Discovery & Geolocation
**Owner role:** Backend Lead
**Depends on:** Issue 23
**Estimate:** 3 days
**Status:** Planned

## Context

The search service is the shared core of discovery. Four channels call it: the web page, the map, the
USSD menu and the WhatsApp bot, so it lives in the service layer and returns plain data, never HTML.
A GiST-indexed `ST_DWithin` keeps it fast as the directory grows.

## Scope

- `find_nearby_sites(lat, lon, radius_m, sector, limit)` using `ST_DWithin` with distance ordering
- Straight-line distance plus a rough travel-time estimate, both returned per result
- Sector filter (`public` / `private` / `all`) and an open-now filter
- Live queue length and current wait estimate joined from the snapshot cache
- `GET /api/clinics/nearby` exposing the service, with pagination and a maximum radius

## Acceptance criteria

- [ ] A search from a coordinate returns clinics within the radius, nearest first
- [ ] The query uses the GiST index, confirmed by an `EXPLAIN` assertion in a test
- [ ] Search completes in under 200 ms with 500 seeded clinics
- [ ] Only `verified` sites are returned
- [ ] The service returns data structures, never rendered HTML, so channels can reuse it
- [ ] Radius is capped server-side so a caller cannot request the entire country

## Files touched

- `app/services/discovery.py`
- `app/api/discovery.py`
- `tests/integration/test_nearby_search.py`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #31
