# Issue 20: Append-only audit log and admin read API

**Area:** Backend / Compliance
**Milestone:** M3 - Identity, Auth, RBAC & Consent
**Owner role:** Backend Lead
**Depends on:** Issues 15, 18
**Estimate:** 2 days
**Status:** Planned

## Context

When a patient asks why their ticket was moved, or an auditor asks who changed the display mode,
the answer has to come from a record rather than a recollection. The audit log is append-only by design:
rows are inserted, never updated or deleted.

## Scope

- `audit_log` model: actor, actor role, action, resource type and id, site, request id, before/after summary, timestamp
- A service helper called from every state-changing operation, with a test that flags an unaudited mutation
- Database-level protection against updates and deletes on the table
- Admin read API with filtering by site, actor, action, resource and date range
- Retention policy aligned with the M13 data map

## Acceptance criteria

- [ ] Every state-changing action writes exactly one audit row
- [ ] Updating or deleting an audit row fails at the database level
- [ ] The read API filters by site and is itself site-scoped
- [ ] Audit rows carry the request id so they join to application logs
- [ ] Personal data in the before/after summary is minimised to what the audit needs
- [ ] A test proves a priority reorder, a display-mode change and a no-show all appear in the log

## Files touched

- `app/database/models/audit_log.py`
- `app/services/audit.py`
- `app/api/admin/audit.py`
- `migrations/versions/*_audit_log.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #20
