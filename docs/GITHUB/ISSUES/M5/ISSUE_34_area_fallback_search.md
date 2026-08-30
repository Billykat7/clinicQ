# Issue 34: Area/suburb fallback search for patients without GPS

**Area:** Backend / Discovery
**Milestone:** M5 - Discovery & Geolocation
**Owner role:** Backend Lead
**Depends on:** Issue 31
**Estimate:** 2 days
**Status:** Planned

## Context

GPS is unavailable more often than smartphone-centric designs assume: permission declined, indoors,
an old device, or a USSD session with no location at all. Searching from a suburb centroid keeps the same
service working identically across all four channels.

## Scope

- `areas` reference table: suburb/township/town name, municipality, province, centroid
- Typeahead area search with fuzzy matching and common alternative spellings
- `find_nearby_sites` accepting an area id and resolving it to its centroid
- Recently used areas remembered per patient
- A seeded area dataset covering the pilot province, sourced from open data with attribution

## Acceptance criteria

- [ ] Declining GPS still returns results via a typed suburb name
- [ ] Typeahead tolerates a misspelling and common alternative names
- [ ] The same service call backs the web, USSD and WhatsApp area search
- [ ] Area data is seeded by migration with its source documented
- [ ] Distances from an area centroid are clearly labelled as approximate
- [ ] Selecting a recently used area takes one interaction

## Files touched

- `app/database/models/area.py`
- `app/services/areas.py`
- `migrations/versions/*_areas_seed.py`
- `tests/integration/test_area_search.py`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #34
