# Issue 30: `sites` OpenAPI contract and module tests

**Area:** Backend / Quality
**Milestone:** M4 - Clinics, Queues & Configuration
**Owner role:** Backend Lead
**Depends on:** Issues 23–29
**Estimate:** 2 days
**Status:** Planned

## Context

The sites module is consumed by discovery, the dashboard, the board and both channel adapters. A
hand-written OpenAPI contract with a drift test means those consumers can be built against a stable
document instead of against whatever the code happens to return this week.

## Scope

- Hand-written `contracts/sites.yaml` covering every route the sites and queues routers serve
- Documented error responses (400, 403, 404, 409) alongside the happy paths
- A drift test asserting every route+method+status is documented and every documented path has a handler
- Module test suite covering site CRUD, hours, queues, services, assignments and onboarding
- Example payloads in the contract that the frontend and channel teams can code against

## Acceptance criteria

- [ ] `sites.yaml` documents every sites and queues route, enforced by the drift test
- [ ] Adding an undocumented route fails the test suite
- [ ] Error responses are documented, not only success responses
- [ ] Module tests cover the full site lifecycle including onboarding and suspension
- [ ] The contract's examples are valid against its own schemas
- [ ] Frontend and channel teams confirm they can build against the contract without reading the code

## Files touched

- `contracts/sites.yaml`
- `tests/test_openapi_contracts.py`
- `tests/integration/test_sites_module.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #30
