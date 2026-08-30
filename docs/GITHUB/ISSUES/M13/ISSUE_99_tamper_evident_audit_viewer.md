# Issue 99: Tamper-evident (hash-chained) audit log and admin viewer UI

**Area:** Backend / Compliance
**Milestone:** M13 - Security, Privacy & POPIA Compliance
**Owner role:** Backend Lead
**Depends on:** Issue 20
**Estimate:** 2 days
**Status:** Planned

## Context

An audit log that can be quietly edited proves nothing. Hash-chaining each entry to its predecessor
makes retrospective alteration detectable, which is what lets the log answer a dispute rather than merely
describe one.

## Scope

- Hash chain over audit entries, each row carrying the previous row's digest
- A verification command that walks the chain and reports the first break
- Admin audit viewer with filtering, pagination and export
- Chain verification run on a schedule with alerting on failure
- Documented procedure for what to do when verification fails

## Acceptance criteria

- [ ] Editing a past audit row is detected by the verification command
- [ ] Verification of a full pilot's log completes in under a minute
- [ ] The viewer filters by site, actor, action, resource and date range
- [ ] Scheduled verification alerts the team on failure
- [ ] The viewer is reachable only by clinic managers (own site) and platform admins
- [ ] The failure procedure is documented and has been walked through once

## Files touched

- `app/services/audit.py`
- `app/web/admin/audit_viewer.py`
- `scripts/verify_audit_chain.py`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #99
