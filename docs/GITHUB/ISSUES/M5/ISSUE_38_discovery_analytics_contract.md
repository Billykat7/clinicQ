# Issue 38: Discovery analytics events and discovery OpenAPI contract

> **In short:** The team can see which clinics people look at and which they actually join, without storing anything that identifies a patient, and the discovery API is written down as a contract.

| | |
|---|---|
| **Milestone** | [M5: Discovery & Geolocation](../../MILESTONES/M5_discovery_geolocation.md) |
| **Sprint** | 6 (weeks 11–12), with C on the same issue |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Quality |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 31](../M5/ISSUE_31_clinics_nearby_postgis_search.md): `GET /api/clinics/nearby`: PostGIS radius search with distance and ETA<br>[Issue 32](../M5/ISSUE_32_discovery_list_ui_sector_toggle.md): Discovery list UI with public/private/all toggle and sector badges<br>[Issue 33](../M5/ISSUE_33_discovery_map_leaflet_osm.md): Map view (Leaflet + OpenStreetMap) with clustering and directions hand-off<br>[Issue 34](../M5/ISSUE_34_area_fallback_search.md): Area/suburb fallback search for patients without GPS<br>[Issue 35](../M5/ISSUE_35_clinic_detail_page.md): Clinic detail page: hours, live queue length, services, contact<br>[Issue 36](../M5/ISSUE_36_queue_snapshot_cache.md): `site_queue_snapshot` Redis cache with warm and invalidate paths<br>[Issue 37](../M5/ISSUE_37_payment_medical_aid_filter.md): Payment and medical-aid directory filter (private clinics only) |
| **Unblocks** | No other issue waits on this one. |

> **Note:** The sprint plan has C doing the analytics events and F the contract (sprint 6); the spec lists F as the single owner.

## Context

A clinic manager's first question is 'how many people saw us and did not come?'. Capturing the view
and the join as distinct events answers it, and the contract lets the channel adapters in M10 reuse
discovery without reading the code.

## Starting point

- Reuse the contract drift test from Issue 30 rather than writing a second one.
- The kernel's sliding-window limiter (`src/core/rate_limit.py`) already fronts other public endpoints; add the search endpoint the same way.

## Scope

- Discovery event capture: search performed, clinic viewed, join started, join completed, with channel and anonymised session
- Aggregation of view-to-join conversion per site, feeding the M12 reports
- Hand-written `contracts/discovery.yaml` with a drift test
- Rate limiting on the public search endpoint to prevent directory scraping
- Events carry no direct identifier beyond a rotating session token

## Out of scope

- The conversion reports themselves (Issue 89 shows them).

## Acceptance criteria

- [ ] View and join events are captured separately and joinable per site
- [ ] Conversion rate per site is queryable and appears in the M12 report set
- [ ] `discovery.yaml` documents every discovery route, enforced by the drift test
- [ ] The search endpoint is rate limited per IP and per session
- [ ] No event row stores a phone number or a precise location
- [ ] Analytics can be disabled per site if a clinic objects

## How to verify

1. Search, view a clinic and join: three events, none containing a phone number or exact coordinates.
2. Hammer the search endpoint from one IP: rate limited.
3. Add an undocumented discovery route: the drift test fails.

## Files touched

- `src/modules/discovery/analytics.py`
- `contracts/discovery.yaml`
- `tests/integration/contracts/test_openapi_contracts.py`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #38
