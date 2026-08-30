# Issue 28: Staff-to-site and room assignment

**Area:** Backend / Clinics
**Milestone:** M4 - Clinics, Queues & Configuration
**Owner role:** Backend Lead
**Depends on:** Issues 22, 25
**Estimate:** 1 day
**Status:** Planned

## Context

A nurse signed into Room 2 should see Room 2. This issue connects staff to sites and to the specific
queues they work, which is what makes the nurse view in M7 meaningful and the RBAC check in Issue 18
enforceable at the row level.

## Scope

- `staff_site_assignments`: staff, site, role at that site, active
- `staff_queue_assignments` linking a nurse or doctor to one or more queues
- Support for a staff member working at more than one site, with a site switcher in M7
- Assignment management UI for the clinic manager
- Assignment changes audited

## Acceptance criteria

- [ ] A nurse assigned to Room 2 cannot call next on Room 3
- [ ] A staff member assigned to two sites can switch between them without signing out
- [ ] Removing an assignment takes effect on the next request, not the next sign-in
- [ ] Assignment changes appear in the audit log with the acting manager
- [ ] A site always retains at least one clinic manager, enforced on removal
- [ ] Assignments are covered by the cross-tenant test suite

## Files touched

- `app/database/models/staff_assignment.py`
- `app/services/staff_assignments.py`
- `app/api/staff/assignments.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #28
