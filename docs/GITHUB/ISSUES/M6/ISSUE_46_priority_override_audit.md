# Issue 46: Clinical priority override with reason codes and audit trail

**Area:** Backend / Queue
**Milestone:** M6 - Queue Engine Core
**Owner role:** Backend Lead
**Depends on:** Issues 41, 20
**Estimate:** 2 days
**Status:** Planned

## Context

The 'one fair queue' rule only survives contact with a real clinic if staff can bump a visibly unwell
patient. Making that easy but attributable is what keeps it a clinical judgement rather than a way to
skip the line: every override needs a reason code and lands in the audit log.

## Scope

- Priority flags with a controlled reason-code list (visibly unwell, elderly, infant, pregnancy, staff referral, other)
- `queue_reorders`: ticket, staff, reason code, free-text note, previous and new position, timestamp
- Reorder service recalculating positions and pushing updates to affected patients and the board
- Mandatory reason code: an override cannot be saved without one
- A daily override count per staff member, surfaced in the M12 reports

## Acceptance criteria

- [ ] An override cannot be saved without a reason code
- [ ] Every override writes a `queue_reorders` row with before and after positions
- [ ] Affected patients see their updated position within 2 seconds
- [ ] Override counts per staff member are reportable
- [ ] An override cannot move a ticket ahead of one already `in_progress`
- [ ] The override trail is visible to the clinic manager in the dashboard

## Files touched

- `app/services/queue_priority.py`
- `app/database/models/queue_reorder.py`
- `tests/integration/test_priority_override.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #46
