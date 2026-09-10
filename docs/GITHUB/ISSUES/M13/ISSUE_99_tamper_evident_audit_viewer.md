# Issue 99: Tamper-evident (hash-chained) audit log and admin viewer UI

> **In short:** Any after-the-fact edit to the audit log is detectable, and managers get a screen to browse it.

| | |
|---|---|
| **Milestone** | [M13: Security, Privacy & POPIA Compliance](../../MILESTONES/M13_security_privacy_compliance.md) |
| **Sprint** | 12 (weeks 23–24) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Compliance |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 20](../M3/ISSUE_20_audit_log_admin_api.md): Append-only audit log and admin read API |
| **Unblocks** | No other issue waits on this one. |

## Context

An audit log that can be quietly edited proves nothing. Hash-chaining each entry to its predecessor
makes retrospective alteration detectable, which is what lets the log answer a dispute rather than merely
describe one.

## Starting point

- The audit table is already append-only at the database level (a trigger from the baseline migration) and searchable through `/api/v1/audit/events`. This issue adds the hash chain and the viewer UI, which does not exist yet.
- Scheduled verification is a sweep in `src/core/scheduler.py`.

## Scope

- Hash chain over audit entries, each row carrying the previous row's digest
- A verification command that walks the chain and reports the first break
- Admin audit viewer with filtering, pagination and export
- Chain verification run on a schedule with alerting on failure
- Documented procedure for what to do when verification fails

## Out of scope

- The underlying audit trail (Issue 20).

## Acceptance criteria

- [ ] Editing a past audit row is detected by the verification command
- [ ] Verification of a full pilot's log completes in under a minute
- [ ] The viewer filters by site, actor, action, resource and date range
- [ ] Scheduled verification alerts the team on failure
- [ ] The viewer is reachable only by clinic managers (own site) and platform admins
- [ ] The failure procedure is documented and has been walked through once

## How to verify

1. Temporarily disable the trigger in a test database and edit an old row: `scripts/verify_audit_chain.py` reports that row as the first break.
2. Verify a full pilot-sized log: under a minute.
3. Open the viewer as a receptionist: refused; as a clinic manager: only their site.

## Files touched

- `src/core/audit.py`
- `src/web/admin_audit.py`
- `src/templates/admin/audit.html`
- `scripts/verify_audit_chain.py`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #99
