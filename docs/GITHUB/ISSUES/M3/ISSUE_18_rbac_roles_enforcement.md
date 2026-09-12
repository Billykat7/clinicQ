# Issue 18: RBAC model, seeded roles and enforcement dependencies

> **In short:** Each of ClinicQ's five roles can do exactly what its job needs, enforced on every route and mirrored in the UI.

| | |
|---|---|
| **Milestone** | [M3: Identity, Auth, RBAC & Consent](../../MILESTONES/M3_identity_auth_rbac.md) |
| **Sprint** | 4 (weeks 7–8) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Auth |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 15](../M3/ISSUE_15_staff_user_security_core.md): Staff user model and security core (JWT, refresh rotation, password hashing) |
| **Unblocks** | [Issue 19](../M3/ISSUE_19_site_scoping_guard.md): Multi-tenant site scoping guard and cross-site access tests<br>[Issue 20](../M3/ISSUE_20_audit_log_admin_api.md): Append-only audit log and admin read API<br>[Issue 22](../M3/ISSUE_22_staff_invitations_account_settings.md): Staff invitations and account settings<br>[Issue 48](../M7/ISSUE_48_dashboard_shell_role_nav.md): Dashboard shell, role-aware navigation and site switcher |

## Context

Five roles with genuinely different powers: a receptionist may issue a ticket but not change the
display mode; a nurse may call from their own room only. Enforcing that with a reusable dependency,
seeded from migrations, is what keeps authorisation out of individual route bodies.

## Starting point

- The kernel RBAC is richer than this spec: a resource tree, cumulative verbs, named actions and scope tiers (`src/core/rbac.py`), module manifests (`rbac_manifest.py`), a console at `/admin/rbac`, and a golden decision snapshot (`make rbac-snapshot-check`).
- `UserRole` in `src/commons/enums.py` still carries the source project's portal roles (`tenant`, `owner`, `manager`, `vendor`). This issue replaces them with `patient`, `receptionist`, `nurse_doctor`, `clinic_manager` and `platform_admin`.
- Grants are seeded by `make seed-rbac` from the manifests, not by a migration as the acceptance criteria say. Agree which one the criterion means before starting; see [open decisions](../README.md#open-decisions).

## Scope

- Roles `patient`, `receptionist`, `nurse_doctor`, `clinic_manager`, `platform_admin` seeded from the module manifests by `make seed-rbac`, which the deploy sequence runs after the migrations (decision 6)
- Verb-based permissions (`READ < CREATE < UPDATE < DELETE`) on named resources
- `require_permission(resource, verb)` FastAPI dependency used by every protected route
- Template helpers so the UI hides actions the current role cannot perform
- An RBAC matrix document listing every resource and which role holds which verb

## Out of scope

- Restricting rows to a caller's own site (Issue 19).
- Staff-to-room assignment (Issue 28).

## Acceptance criteria

- [ ] Every protected route declares its permission through the dependency, checked by a guard test
- [ ] A nurse cannot call next on a queue they are not assigned to
- [ ] A receptionist cannot change display mode or site settings
- [ ] The UI never renders an action the role cannot execute (defence in depth, not the only control)
- [ ] Seeded roles and permissions are created automatically by the deploy sequence (`make seed-rbac` after `alembic upgrade head`, decision 6), not by a manual script
- [ ] The RBAC matrix is committed and matches the seeded data, verified by a test

## How to verify

1. `make seed-rbac` on a fresh database, then `make seed-rbac-check`: no drift.
2. Sign in as a receptionist: the display-mode setting is neither shown nor callable.
3. `make rbac-snapshot-check` passes, and changes only where the PR says it should.

## Files touched

- `src/commons/enums.py`
- `src/core/rbac.py`
- `src/core/rbac_manifest_registry.py`
- `scripts/db/seed_rbac.py`
- `tests/test_rbac_matrix.py`
- `docs/architecture/rbac-matrix.md` (generated) and `tests/snapshots/rbac_decisions.txt`
- `src/modules/sites/rbac_manifest.py`, `src/modules/queues/rbac_manifest.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #18
