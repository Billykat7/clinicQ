# Issue 38: Discovery analytics events and discovery OpenAPI contract

**Area:** Backend / Quality
**Milestone:** M5 - Discovery & Geolocation
**Owner role:** Data & Research Lead
**Depends on:** Issues 31–37
**Estimate:** 2 days
**Status:** Planned

## Context

A clinic manager's first question is 'how many people saw us and did not come?'. Capturing the view
and the join as distinct events answers it, and the contract lets the channel adapters in M10 reuse
discovery without reading the code.

## Scope

- Discovery event capture: search performed, clinic viewed, join started, join completed, with channel and anonymised session
- Aggregation of view-to-join conversion per site, feeding the M12 reports
- Hand-written `contracts/discovery.yaml` with a drift test
- Rate limiting on the public search endpoint to prevent directory scraping
- Events carry no direct identifier beyond a rotating session token

## Acceptance criteria

- [ ] View and join events are captured separately and joinable per site
- [ ] Conversion rate per site is queryable and appears in the M12 report set
- [ ] `discovery.yaml` documents every discovery route, enforced by the drift test
- [ ] The search endpoint is rate limited per IP and per session
- [ ] No event row stores a phone number or a precise location
- [ ] Analytics can be disabled per site if a clinic objects

## Files touched

- `app/services/discovery_analytics.py`
- `contracts/discovery.yaml`
- `tests/test_openapi_contracts.py`

---

**Refs:** [M5 milestone](../../MILESTONES/M5_discovery_geolocation.md) · [product docs](../../../PRODUCT/02-discovery-and-geolocation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #38
