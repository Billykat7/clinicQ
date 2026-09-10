# Issue 20: Append-only audit log and admin read API

> **In short:** Every change to a record leaves a permanent, tamper-proof entry saying who did what, when, and from which request.

| | |
|---|---|
| **Milestone** | [M3: Identity, Auth, RBAC & Consent](../../MILESTONES/M3_identity_auth_rbac.md) |
| **Sprint** | 4 (weeks 7–8) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Compliance |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 15](../M3/ISSUE_15_staff_user_security_core.md): Staff user model and security core (JWT, refresh rotation, password hashing)<br>[Issue 18](../M3/ISSUE_18_rbac_roles_enforcement.md): RBAC model, seeded roles and enforcement dependencies |
| **Unblocks** | [Issue 46](../M6/ISSUE_46_priority_override_audit.md): Clinical priority override with reason codes and audit trail<br>[Issue 95](../M13/ISSUE_95_data_map_retention_purge.md): Data map, retention policy and automatic purge jobs<br>[Issue 99](../M13/ISSUE_99_tamper_evident_audit_viewer.md): Tamper-evident (hash-chained) audit log and admin viewer UI |

## Context

When a patient asks why their ticket was moved, or an auditor asks who changed the display mode,
the answer has to come from a record rather than a recollection. The audit log is append-only by design:
rows are inserted, never updated or deleted.

## Starting point

- Built: the `audit_event` table with an append-only trigger installed by the baseline migration, `src/core/audit.py` (snapshot, diff and redaction) and a search API at `/api/v1/audit/events` that audits its own reads.
- The table has no `site_id` or `request_id` column yet; both are in the acceptance criteria.

## Scope

- `audit_log` model: actor, actor role, action, resource type and id, site, request id, before/after summary, timestamp
- A service helper called from every state-changing operation, with a test that flags an unaudited mutation
- Database-level protection against updates and deletes on the table
- Admin read API with filtering by site, actor, action, resource and date range
- Retention policy aligned with the M13 data map

## Out of scope

- Hash-chaining the log so edits are detectable (Issue 99).
- Audit retention and purge (Issue 95).

## Acceptance criteria

- [ ] Every state-changing action writes exactly one audit row
- [ ] Updating or deleting an audit row fails at the database level
- [ ] The read API filters by site and is itself site-scoped
- [ ] Audit rows carry the request id so they join to application logs
- [ ] Personal data in the before/after summary is minimised to what the audit needs
- [ ] A test proves a priority reorder, a display-mode change and a no-show all appear in the log

## How to verify

1. `UPDATE audit_event SET action='x'` in psql: the database raises an error.
2. Reorder a ticket, change a display mode and mark a no-show: three rows, each with a site and request id.
3. Search the API as Clinic A staff: only Clinic A rows come back.

## Files touched

- `src/database/models/audit_event.py`
- `src/core/audit.py`
- `src/modules/audit/`
- `alembic/versions/NNNN_audit_site_request.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #20
