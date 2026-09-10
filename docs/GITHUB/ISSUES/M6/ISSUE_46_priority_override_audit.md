# Issue 46: Clinical priority override with reason codes and audit trail

> **In short:** Staff can move a visibly unwell patient forward in two taps, but never silently: every move needs a reason and leaves a permanent record.

| | |
|---|---|
| **Milestone** | [M6: Queue Engine Core](../../MILESTONES/M6_queue_engine_core.md) |
| **Sprint** | 7 (weeks 13–14) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Queue |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 20](../M3/ISSUE_20_audit_log_admin_api.md): Append-only audit log and admin read API<br>[Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection |
| **Unblocks** | [Issue 47](../M6/ISSUE_47_queue_contract_concurrency_tests.md): Queue OpenAPI contract, concurrency and state-machine tests<br>[Issue 52](../M7/ISSUE_52_reorder_ui_audit_trail.md): Drag-to-reorder with reason codes and inline audit trail |

## Context

The 'one fair queue' rule only survives contact with a real clinic if staff can bump a visibly unwell
patient. Making that easy but attributable is what keeps it a clinical judgement rather than a way to
skip the line: every override needs a reason code and lands in the audit log.

## Starting point

- Build on the audit trail from Issue 20; `queue_reorders` is the queue-specific detail, the audit row is the proof.
- The reason codes are an enum in `src/commons/enums.py`, not free text.

## Scope

- Priority flags with a controlled reason-code list (visibly unwell, elderly, infant, pregnancy, staff referral, other)
- `queue_reorders`: ticket, staff, reason code, free-text note, previous and new position, timestamp
- Reorder service recalculating positions and pushing updates to affected patients and the board
- Mandatory reason code: an override cannot be saved without one
- A daily override count per staff member, surfaced in the M12 reports

## Out of scope

- The drag-to-reorder dashboard UI (Issue 52).
- Per-staff override reports (Issue 90).

## Acceptance criteria

- [ ] An override cannot be saved without a reason code
- [ ] Every override writes a `queue_reorders` row with before and after positions
- [ ] Affected patients see their updated position within 2 seconds
- [ ] Override counts per staff member are reportable
- [ ] An override cannot move a ticket ahead of one already `in_progress`
- [ ] The override trail is visible to the clinic manager in the dashboard

## How to verify

1. Save an override with no reason code: refused.
2. Move a ticket ahead of one already in progress: refused.
3. Make an override: one `queue_reorders` row with before and after positions, visible to the clinic manager.

## Files touched

- `src/modules/queue/priority.py`
- `src/database/models/queue_reorder.py`
- `tests/integration/queue/test_priority_override.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #46
