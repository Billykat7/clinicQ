# Issue 34: Area/suburb fallback search for patients without GPS

> **In short:** A patient without GPS, or on a feature phone, can still find clinics by typing a suburb or township name.

| | |
|---|---|
| **Milestone** | [M5: Discovery & Geolocation](../../MILESTONES/M5_discovery_geolocation.md) |
| **Sprint** | 2 (weeks 3–4); the sprint plan puts this in **F**'s lane, see the note below |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Discovery |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 31](../M5/ISSUE_31_clinics_nearby_postgis_search.md): `GET /api/clinics/nearby`: PostGIS radius search with distance and ETA |
| **Unblocks** | [Issue 38](../M5/ISSUE_38_discovery_analytics_contract.md): Discovery analytics events and discovery OpenAPI contract |

> **Note:** The spec names A as owner; the sprint plan has F preparing the suburb dataset in sprint 2. A builds the search, F supplies and documents the data.

## Context

GPS is unavailable more often than smartphone-centric designs assume: permission declined, indoors,
an old device, or a USSD session with no location at all. Searching from a suburb centroid keeps the same
service working identically across all four channels.

## Starting point

- Part of `src/modules/discovery/`. The service must be callable from USSD and WhatsApp, not only the web page.
- Record the dataset's source and licence in the migration docstring, as the acceptance criteria require.

## Scope

- `areas` reference table: suburb/township/town name, municipality, province, centroid
- Typeahead area search with fuzzy matching and common alternative spellings
- `find_nearby_sites` accepting an area id and resolving it to its centroid
- Recently used areas remembered per patient
- A seeded area dataset covering the pilot province, sourced from open data with attribution

## Out of scope

- The USSD and WhatsApp menus that call this (Issues 73, 75).

## Acceptance criteria

- [ ] Declining GPS still returns results via a typed suburb name
- [ ] Typeahead tolerates a misspelling and common alternative names
- [ ] The same service call backs the web, USSD and WhatsApp area search
- [ ] Area data is seeded by migration with its source documented
- [ ] Distances from an area centroid are clearly labelled as approximate
- [ ] Selecting a recently used area takes one interaction

## How to verify

1. Search for a misspelt suburb (for example "Soweeto"): the right area is suggested.
2. Search by area: results appear, with distances labelled as approximate.
3. `make migrate-up` on a fresh database seeds the areas.

## Files touched

- `src/modules/discovery/areas.py`
- `src/database/models/area.py`
- `alembic/versions/NNNN_areas_seed.py`
- `tests/integration/discovery/test_area_search.py`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #34
