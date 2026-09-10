# Issue 30: `sites` OpenAPI contract and module tests

> **In short:** The sites and queues API is written down as a contract the frontend and channel teams can build against without reading backend code.

| | |
|---|---|
| **Milestone** | [M4: Clinics, Queues & Configuration](../../MILESTONES/M4_clinics_queues_config.md) |
| **Sprint** | 5 (weeks 9–10); the sprint plan puts this in **E and F**'s lane, see the note below |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Quality |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 23](../M4/ISSUE_23_sites_model_profile_crud.md): `sites` model with PostGIS location and clinic profile CRUD<br>[Issue 24](../M4/ISSUE_24_opening_hours_closures.md): Opening hours, holiday calendar and temporary-closure broadcast<br>[Issue 25](../M4/ISSUE_25_queues_model_multiroom.md): `queues` model: multi-room, multi-service queues per site<br>[Issue 26](../M4/ISSUE_26_services_catalogue_service_times.md): Services catalogue with expected service times<br>[Issue 27](../M4/ISSUE_27_display_privacy_settings.md): Display and privacy settings per site<br>[Issue 28](../M4/ISSUE_28_staff_site_room_assignment.md): Staff-to-site and room assignment<br>[Issue 29](../M4/ISSUE_29_clinic_onboarding_verification.md): Clinic onboarding and platform-admin verification workflow |
| **Unblocks** | No other issue waits on this one. |

> **Note:** The spec names A as owner, but the sprint plan puts the contract harness in E's lane and the contract review in F's lane (sprint 5). Agree who writes the drift test before sprint 5.

## Context

The sites module is consumed by discovery, the dashboard, the board and both channel adapters. A
hand-written OpenAPI contract with a drift test means those consumers can be built against a stable
document instead of against whatever the code happens to return this week.

## Starting point

- There is no `contracts/` folder yet; this issue creates it and the drift test the later contracts (Issues 38, 47, 71, 79, 94) reuse.
- FastAPI already publishes `/openapi.json`, which is what the drift test compares against.

## Scope

- Hand-written `contracts/sites.yaml` covering every route the sites and queues routers serve
- Documented error responses (400, 403, 404, 409) alongside the happy paths
- A drift test asserting every route+method+status is documented and every documented path has a handler
- Module test suite covering site CRUD, hours, queues, services, assignments and onboarding
- Example payloads in the contract that the frontend and channel teams can code against

## Out of scope

- Contracts for discovery, queue, notifications, channels and reporting (their own milestones).

## Acceptance criteria

- [ ] `sites.yaml` documents every sites and queues route, enforced by the drift test
- [ ] Adding an undocumented route fails the test suite
- [ ] Error responses are documented, not only success responses
- [ ] Module tests cover the full site lifecycle including onboarding and suspension
- [ ] The contract's examples are valid against its own schemas
- [ ] Frontend and channel teams confirm they can build against the contract without reading the code

## How to verify

1. Add an undocumented route: the drift test fails and names it.
2. Validate every example in `sites.yaml` against its schema: all pass.
3. C and B each confirm in the PR that they can build against the contract alone.

## Files touched

- `contracts/sites.yaml`
- `tests/integration/contracts/test_openapi_contracts.py`
- `tests/integration/sites/test_sites_module.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #30
