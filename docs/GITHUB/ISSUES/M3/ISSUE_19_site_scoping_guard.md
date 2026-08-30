# Issue 19: Multi-tenant site scoping guard and cross-site access tests

**Area:** Backend / Security
**Milestone:** M3 - Identity, Auth, RBAC & Consent
**Owner role:** Backend Lead
**Depends on:** Issue 18
**Estimate:** 2 days
**Status:** Planned

## Context

ClinicQ is multi-tenant from the first pilot. A receptionist at Clinic A must never be able to read,
guess or enumerate a ticket at Clinic B. Retrofitting that guarantee once forty routes exist is far more
expensive than landing it now, before there is anything to scope.

## Scope

- `require_site_access(site_id)` dependency resolving the caller's permitted sites
- A repository/query helper that applies the site filter, and a lint or test that flags a raw model query in a router
- Cross-site access returns 404, not 403, so ids cannot be probed for existence
- Platform-admin escape hatch that is explicit, audited and never implicit
- A dedicated cross-tenant test suite covering every resource type

## Acceptance criteria

- [ ] A Clinic A staff token receives 404 for every Clinic B resource id
- [ ] Ticket, queue, staff and report endpoints are all covered by the cross-tenant suite
- [ ] No router constructs a query on a site-scoped model without the helper, enforced by a guard test
- [ ] Platform-admin cross-site reads write an audit row every time
- [ ] Enumerating sequential ids leaks no information about another site
- [ ] The guard is documented as a project non-negotiable in `docs/guideline.md`

## Files touched

- `app/core/tenancy.py`
- `app/database/repository.py`
- `tests/integration/test_cross_tenant.py`
- `docs/guideline.md`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #19
