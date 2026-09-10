# Issue 19: Multi-tenant site scoping guard and cross-site access tests

> **In short:** A receptionist at Clinic A can never see anything at Clinic B, and cannot even tell whether a Clinic B record exists.

| | |
|---|---|
| **Milestone** | [M3: Identity, Auth, RBAC & Consent](../../MILESTONES/M3_identity_auth_rbac.md) |
| **Sprint** | 4 (weeks 7–8), with E on the same issue |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Security |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 18](../M3/ISSUE_18_rbac_roles_enforcement.md): RBAC model, seeded roles and enforcement dependencies |
| **Unblocks** | [Issue 23](../M4/ISSUE_23_sites_model_profile_crud.md): `sites` model with PostGIS location and clinic profile CRUD |

## Context

ClinicQ is multi-tenant from the first pilot. A receptionist at Clinic A must never be able to read,
guess or enumerate a ticket at Clinic B. Retrofitting that guarantee once forty routes exist is far more
expensive than landing it now, before there is anything to scope.

## Starting point

- `src/core/scope.py` (`resolve_scope`) already answers *whose rows* separately from RBAC's *what*, using the grant's scope tier (`GrantScope`: own, assigned, business).
- Scoped role assignments (`UserRoleAssignment` with `scope_type` and `scope_id`) are the natural home for "this person works at this site": add `site` as a scope type.
- The source project scoped by property; the resolver's resource shapes need the ClinicQ equivalents (site, queue, ticket).

## Scope

- `require_site_access(site_id)` dependency resolving the caller's permitted sites
- A repository/query helper that applies the site filter, and a lint or test that flags a raw model query in a router
- Cross-site access returns 404, not 403, so ids cannot be probed for existence
- Platform-admin escape hatch that is explicit, audited and never implicit
- A dedicated cross-tenant test suite covering every resource type

## Out of scope

- Which queues a nurse may call within their site (Issue 28).
- The audit trail itself (Issue 20).

## Acceptance criteria

- [ ] A Clinic A staff token receives 404 for every Clinic B resource id
- [ ] Ticket, queue, staff and report endpoints are all covered by the cross-tenant suite
- [ ] No router constructs a query on a site-scoped model without the helper, enforced by a guard test
- [ ] Platform-admin cross-site reads write an audit row every time
- [ ] Enumerating sequential ids leaks no information about another site
- [ ] The guard is documented as a project non-negotiable in `docs/guideline.md`

## How to verify

1. Use a Clinic A token against a Clinic B ticket, queue and staff id: 404 each time, never 403.
2. Add a router query that skips the scope helper: the guard test fails.
3. Read across sites as a platform admin: an audit row is written.

## Files touched

- `src/core/scope.py`
- `src/database/models/user_role_assignment.py`
- `tests/integration/security/test_cross_tenant.py`
- `docs/guideline.md`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #19
