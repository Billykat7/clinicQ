# Issue 23: `sites` model with PostGIS location and clinic profile CRUD

**Area:** Backend / Clinics
**Milestone:** M4 - Clinics, Queues & Configuration
**Owner role:** Backend Lead
**Depends on:** Issues 3, 19
**Estimate:** 3 days
**Status:** Planned

## Context

**Land this first.** Four other people are blocked until a clinic row with a location exists: discovery
searches it, tickets belong to its queues, the board renders its settings, and reports aggregate by it.
The PostGIS column and its GiST index are part of this issue precisely so M5 does not have to migrate a
populated table later.

## Scope

- `sites` model: name, slug, sector (`public`/`private`), `geography(Point, 4326)` location, address, suburb, city, province, phone, status
- GiST index on the location column and a functional index on sector plus status
- Site CRUD API and management UI, restricted to clinic manager and platform admin
- Geocoding helper to derive a coordinate from a typed address at creation time, with a manual override
- Validation that a location falls within a sane bounding box for the operating country

## Acceptance criteria

- [ ] A site persists with a real coordinate and is returned by a `ST_DWithin` query
- [ ] The GiST index is present in the baseline migration and used by the query plan
- [ ] Sector is an enum, never a free string
- [ ] Creating a site without a valid location is rejected with a clear error
- [ ] Only a clinic manager for that site, or a platform admin, can edit it
- [ ] `sites` fixtures exist in `tests/factories.py` for every other module to use

## Files touched

- `app/database/models/site.py`
- `app/services/sites.py`
- `app/api/sites.py`
- `migrations/versions/*_sites.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #23
