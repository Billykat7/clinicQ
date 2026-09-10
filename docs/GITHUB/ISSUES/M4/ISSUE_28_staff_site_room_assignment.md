# Issue 28: Staff-to-site and room assignment

> **In short:** Staff are linked to the clinics and rooms they work in, so a nurse only calls patients for her own room.

| | |
|---|---|
| **Milestone** | [M4: Clinics, Queues & Configuration](../../MILESTONES/M4_clinics_queues_config.md) |
| **Sprint** | 5 (weeks 9–10) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Clinics |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 22](../M3/ISSUE_22_staff_invitations_account_settings.md): Staff invitations and account settings<br>[Issue 25](../M4/ISSUE_25_queues_model_multiroom.md): `queues` model: multi-room, multi-service queues per site |
| **Unblocks** | [Issue 30](../M4/ISSUE_30_sites_openapi_contract_tests.md): `sites` OpenAPI contract and module tests<br>[Issue 48](../M7/ISSUE_48_dashboard_shell_role_nav.md): Dashboard shell, role-aware navigation and site switcher<br>[Issue 53](../M7/ISSUE_53_nurse_room_view_visit_notes.md): Nurse/doctor room view and private visit notes<br>[Issue 54](../M7/ISSUE_54_manager_settings_ui.md): Clinic manager settings UI (profile, hours, display mode, staff, services) |

## Context

A nurse signed into Room 2 should see Room 2. This issue connects staff to sites and to the specific
queues they work, which is what makes the nurse view in M7 meaningful and the RBAC check in Issue 18
enforceable at the row level.

## Starting point

- Site membership can reuse the kernel's scoped role assignment (`UserRoleAssignment` with `scope_type='site'`), the same record Issue 19 scopes on.
- Room-level assignment (`staff_queue_assignments`) is new.

## Scope

- `staff_site_assignments`: staff, site, role at that site, active
- `staff_queue_assignments` linking a nurse or doctor to one or more queues
- Support for a staff member working at more than one site, with a site switcher in M7
- Assignment management UI for the clinic manager
- Assignment changes audited

## Out of scope

- The dashboard site switcher (M7 uses what this stores).
- Invitations (Issue 22).

## Acceptance criteria

- [ ] A nurse assigned to Room 2 cannot call next on Room 3
- [ ] A staff member assigned to two sites can switch between them without signing out
- [ ] Removing an assignment takes effect on the next request, not the next sign-in
- [ ] Assignment changes appear in the audit log with the acting manager
- [ ] A site always retains at least one clinic manager, enforced on removal
- [ ] Assignments are covered by the cross-tenant test suite

## How to verify

1. Assign a nurse to Room 2: calling next on Room 3 is refused.
2. Remove the last clinic manager from a site: refused.
3. Remove an assignment: the very next request reflects it, without signing out.

## Files touched

- `src/modules/staff/assignments.py`
- `src/database/models/staff_queue_assignment.py`
- `src/database/models/user_role_assignment.py`
- `alembic/versions/NNNN_staff_queue_assignments.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #28
