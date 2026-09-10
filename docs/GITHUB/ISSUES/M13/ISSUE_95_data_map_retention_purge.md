# Issue 95: Data map, retention policy and automatic purge jobs

> **In short:** Personal details are kept only as long as they are needed, then deleted automatically, with proof that they are really gone.

| | |
|---|---|
| **Milestone** | [M13: Security, Privacy & POPIA Compliance](../../MILESTONES/M13_security_privacy_compliance.md) |
| **Sprint** | 12 (weeks 23–24); the sprint plan puts this in **A**'s lane, see the note below |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Compliance |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 20](../M3/ISSUE_20_audit_log_admin_api.md): Append-only audit log and admin read API<br>[Issue 53](../M7/ISSUE_53_nurse_room_view_visit_notes.md): Nurse/doctor room view and private visit notes |
| **Unblocks** | [Issue 96](../M13/ISSUE_96_dsar_export_erasure.md): Data-subject access export and erasure workflow<br>[Issue 98](../M13/ISSUE_98_encryption_pii_protection.md): Encryption in transit and at rest, field-level encryption for patient contacts |

## Context

Docs 04 and 13 promise that reason text and visit notes are purged on a short retention window. This
issue is where that sentence becomes a scheduled job with a test: the difference between a privacy
intention and a privacy control.

## Starting point

- The kernel already runs a retention sweep for documents (`run_retention_sweep` in `src/modules/documents/service.py`, registered in `src/core/scheduler.py`). Model the purge on it: idempotent, advisory-locked, tombstoning what it removes.
- F drafts the data map from sprint 2 onwards, so the list of fields should already exist when this starts.

## Scope

- A data map of every personal field: what it is, why it is held, its lawful basis, and its retention period
- Retention configuration per data class, with a policy ceiling that a site cannot exceed
- Nightly purge job for `reason_text`, `visit_notes`, expired OTPs, stale sessions and old notification bodies
- Purge receipts recording what class was purged and how many rows, without recording the content
- Tests proving data is genuinely gone, including from any denormalised copy

## Out of scope

- Deletion on a patient's request (Issue 96).
- Encryption of the fields that remain (Issue 98).

## Acceptance criteria

- [ ] Reason text and visit notes are irrecoverable after the retention window, proven by a test
- [ ] A site cannot configure a retention period longer than the policy ceiling
- [ ] The purge job is idempotent and safe to re-run
- [ ] Purge receipts are auditable without exposing purged content
- [ ] Aggregate statistics survive the purge; personal detail does not
- [ ] The data map is committed and reviewed by the whole team

## How to verify

1. Create a ticket with reason text, advance past the window, run the purge: the text is gone from every table, including copies.
2. Run the purge again: nothing changes and no error.
3. Try to set a site's retention above the policy ceiling: refused.

## Files touched

- `docs/COMPLIANCE/DATA_MAP.md`
- `src/modules/compliance/retention.py`
- `src/core/scheduler.py`
- `tests/integration/compliance/test_retention.py`

---

**Refs:** [M13 milestone](../../MILESTONES/M13_security_privacy_compliance.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #95
