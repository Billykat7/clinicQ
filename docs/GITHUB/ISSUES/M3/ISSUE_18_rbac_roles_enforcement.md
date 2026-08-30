# Issue 18: RBAC model, seeded roles and enforcement dependencies

**Area:** Backend / Auth
**Milestone:** M3 - Identity, Auth, RBAC & Consent
**Owner role:** Backend Lead
**Depends on:** Issue 15
**Estimate:** 3 days
**Status:** Planned

## Context

Five roles with genuinely different powers: a receptionist may issue a ticket but not change the
display mode; a nurse may call from their own room only. Enforcing that with a reusable dependency,
seeded from migrations, is what keeps authorisation out of individual route bodies.

## Scope

- Roles `patient`, `receptionist`, `nurse_doctor`, `clinic_manager`, `platform_admin` seeded via migration
- Verb-based permissions (`READ < CREATE < UPDATE < DELETE`) on named resources
- `require_permission(resource, verb)` FastAPI dependency used by every protected route
- Template helpers so the UI hides actions the current role cannot perform
- An RBAC matrix document listing every resource and which role holds which verb

## Acceptance criteria

- [ ] Every protected route declares its permission through the dependency, checked by a guard test
- [ ] A nurse cannot call next on a queue they are not assigned to
- [ ] A receptionist cannot change display mode or site settings
- [ ] The UI never renders an action the role cannot execute (defence in depth, not the only control)
- [ ] Seeded roles and permissions are created by migration, not by a manual script
- [ ] The RBAC matrix is committed and matches the seeded data, verified by a test

## Files touched

- `app/core/rbac.py`
- `app/database/models/role.py`
- `migrations/versions/*_rbac_seed.py`
- `docs/GITHUB/RBAC_MATRIX.md`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #18
