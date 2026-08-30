# Issue 95: Data map, retention policy and automatic purge jobs

**Area:** Backend / Compliance
**Milestone:** M13 - Security, Privacy & POPIA Compliance
**Owner role:** Data & Research Lead
**Depends on:** Issues 20, 53
**Estimate:** 3 days
**Status:** Planned

## Context

Docs 04 and 13 promise that reason text and visit notes are purged on a short retention window. This
issue is where that sentence becomes a scheduled job with a test: the difference between a privacy
intention and a privacy control.

## Scope

- A data map of every personal field: what it is, why it is held, its lawful basis, and its retention period
- Retention configuration per data class, with a policy ceiling that a site cannot exceed
- Nightly purge job for `reason_text`, `visit_notes`, expired OTPs, stale sessions and old notification bodies
- Purge receipts recording what class was purged and how many rows, without recording the content
- Tests proving data is genuinely gone, including from any denormalised copy

## Acceptance criteria

- [ ] Reason text and visit notes are irrecoverable after the retention window, proven by a test
- [ ] A site cannot configure a retention period longer than the policy ceiling
- [ ] The purge job is idempotent and safe to re-run
- [ ] Purge receipts are auditable without exposing purged content
- [ ] Aggregate statistics survive the purge; personal detail does not
- [ ] The data map is committed and reviewed by the whole team

## Files touched

- `docs/COMPLIANCE/DATA_MAP.md`
- `workers/retention_worker.py`
- `app/services/retention.py`
- `tests/integration/test_retention.py`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #95
