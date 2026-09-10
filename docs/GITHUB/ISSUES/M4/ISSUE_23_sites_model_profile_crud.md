# Issue 23: `sites` model with PostGIS location and clinic profile CRUD

> **In short:** A clinic exists in the system with a real map location, so it can be found by distance and everything else (queues, staff, tickets) can hang off it.

| | |
|---|---|
| **Milestone** | [M4: Clinics, Queues & Configuration](../../MILESTONES/M4_clinics_queues_config.md) |
| **Sprint** | 5 (weeks 9–10) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Clinics |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 3](../M1/ISSUE_3_sqlalchemy_alembic_baseline.md): Async SQLAlchemy 2.x + Alembic baseline (PostGIS extension enabled)<br>[Issue 19](../M3/ISSUE_19_site_scoping_guard.md): Multi-tenant site scoping guard and cross-site access tests |
| **Unblocks** | [Issue 24](../M4/ISSUE_24_opening_hours_closures.md): Opening hours, holiday calendar and temporary-closure broadcast<br>[Issue 25](../M4/ISSUE_25_queues_model_multiroom.md): `queues` model: multi-room, multi-service queues per site<br>[Issue 27](../M4/ISSUE_27_display_privacy_settings.md): Display and privacy settings per site<br>[Issue 29](../M4/ISSUE_29_clinic_onboarding_verification.md): Clinic onboarding and platform-admin verification workflow<br>[Issue 30](../M4/ISSUE_30_sites_openapi_contract_tests.md): `sites` OpenAPI contract and module tests<br>[Issue 31](../M5/ISSUE_31_clinics_nearby_postgis_search.md): `GET /api/clinics/nearby`: PostGIS radius search with distance and ETA<br>[Issue 37](../M5/ISSUE_37_payment_medical_aid_filter.md): Payment and medical-aid directory filter (private clinics only)<br>[Issue 61](../M8/ISSUE_61_kiosk_device_registry.md): Kiosk device registry, pairing codes and heartbeat monitoring |

## Context

**Land this first.** Four other people are blocked until a clinic row with a location exists: discovery
searches it, tickets belong to its queues, the board renders its settings, and reports aggregate by it.
The PostGIS column and its GiST index are part of this issue precisely so M5 does not have to migrate a
populated table later.

## Starting point

- New module: `src/modules/sites/`, copying the shape of `src/modules/widgets/` (model, schemas, service, router, RBAC manifest) and registered in `src/api/v1/router.py` and `src/core/rbac_manifest_registry.py`.
- PostGIS is already enabled by the baseline migration. The Python side needs a geography type (GeoAlchemy2 is not in `requirements.txt` yet).
- CSP only allows `connect-src 'self'`, so geocoding must be proxied by the server, never called from the browser.
- **Sprint 5, day 3 is a team commitment:** land the model and a minimal API in a small PR first, because C, D and B build against it.

## Scope

- `sites` model: name, slug, sector (`public`/`private`), `geography(Point, 4326)` location, address, suburb, city, province, phone, status
- GiST index on the location column and a functional index on sector plus status
- Site CRUD API and management UI, restricted to clinic manager and platform admin
- Geocoding helper to derive a coordinate from a typed address at creation time, with a manual override
- Validation that a location falls within a sane bounding box for the operating country

## Out of scope

- Opening hours (Issue 24) and queues (Issue 25).
- The public registration form and verification (Issue 29).
- Nearby search (Issue 31).

## Acceptance criteria

- [ ] A site persists with a real coordinate and is returned by a `ST_DWithin` query
- [ ] The GiST index is present in the baseline migration and used by the query plan
- [ ] Sector is an enum, never a free string
- [ ] Creating a site without a valid location is rejected with a clear error
- [ ] Only a clinic manager for that site, or a platform admin, can edit it
- [ ] `sites` fixtures exist in `tests/factories.py` for every other module to use

## How to verify

1. Create a site from a typed address: it stores a coordinate inside South Africa.
2. Run the `ST_DWithin` query with `EXPLAIN`: it uses the GiST index.
3. Try to create a site at `0, 0`: rejected with a clear message.

## Files touched

- `src/modules/sites/`
- `src/database/models/site.py`
- `alembic/versions/NNNN_sites.py`
- `requirements.txt`
- `tests/factories.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #23
