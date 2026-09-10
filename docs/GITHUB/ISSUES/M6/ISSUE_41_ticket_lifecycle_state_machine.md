# Issue 41: Ticket lifecycle state machine and illegal-transition rejection

> **In short:** A ticket can only move along the allowed path (waiting, called, in progress, done, and so on), and only one function is allowed to move it.

| | |
|---|---|
| **Milestone** | [M6: Queue Engine Core](../../MILESTONES/M6_queue_engine_core.md) |
| **Sprint** | 6 (weeks 11–12) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Queue |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 39](../M6/ISSUE_39_tickets_model_sequence.md): `tickets` model and concurrency-safe daily sequence numbering |
| **Unblocks** | [Issue 43](../M6/ISSUE_43_recall_noshow_timers.md): Recall timers and automatic no-show transitions (`arq` jobs)<br>[Issue 44](../M6/ISSUE_44_patient_cancel_recalculation.md): Patient cancellation and queue position recalculation<br>[Issue 45](../M6/ISSUE_45_queue_transfer.md): Transfer between queues without re-joining (triage → doctor → pharmacy)<br>[Issue 46](../M6/ISSUE_46_priority_override_audit.md): Clinical priority override with reason codes and audit trail<br>[Issue 47](../M6/ISSUE_47_queue_contract_concurrency_tests.md): Queue OpenAPI contract, concurrency and state-machine tests<br>[Issue 50](../M7/ISSUE_50_call_next_actions.md): Call next, recall, mark done and no-show actions<br>[Issue 56](../M8/ISSUE_56_board_page_kiosk.md): Waiting-room board page (kiosk) with now-serving and up-next panels<br>[Issue 57](../M8/ISSUE_57_board_sse_channel.md): SSE live update channel with reconnect, backoff and heartbeat<br>[Issue 63](../M9/ISSUE_63_notification_service_adapters.md): Notification service, transport adapters and delivery log<br>[Issue 87](../M11/ISSUE_87_post_visit_feedback.md): Post-visit feedback survey and satisfaction reporting<br>[Issue 88](../M12/ISSUE_88_daily_stats_worker.md): Nightly `daily_queue_stats` aggregation worker |

## Context

Ticket status drives the board, the notifications, the reports and the patient's screen. An explicit
state machine that rejects illegal transitions keeps those four consumers consistent, and makes 'how did
this ticket get here?' a question the audit log can answer.

## Starting point

- Put the `TicketStatus` enum from Issue 4 and a transition table in `src/modules/queue/lifecycle.py`.
- This is non-negotiable 2 in the [guideline](../../../guideline.md): add a guard test that fails if anything outside `transition_ticket()` assigns `ticket.status`.
- Write each transition's audit row with the kernel's `src/core/audit.py`.

## Scope

- Explicit transition table: `waiting → called → in_progress → done`, plus `no_show`, `cancelled`, `transferred`
- A single `transition_ticket()` function; no route or template mutates status directly
- Illegal transitions rejected with 409 and no state change
- Every transition timestamped, attributed and audited
- Terminal states immutable, with corrections expressed as a new ticket rather than an edit

## Out of scope

- Timed transitions such as recall and no-show (Issue 43).
- Transfers between queues (Issue 45).

## Acceptance criteria

- [ ] Every illegal transition is rejected with 409 and leaves state untouched, covered exhaustively by tests
- [ ] No code path outside `transition_ticket()` writes `ticket.status`, enforced by a guard test
- [ ] Each transition writes an audit row with actor and timestamp
- [ ] A terminal ticket cannot be reopened
- [ ] The state diagram is documented and matches the transition table, verified by a test
- [ ] Concurrent transitions on one ticket are serialised, with the loser receiving 409

## How to verify

1. The unit tests try every `(from, to)` pair: illegal ones return 409 and change nothing.
2. Add `ticket.status = ...` in any router: the guard test fails.
3. Two concurrent calls move one ticket: one wins, the other gets 409.

## Files touched

- `src/modules/queue/lifecycle.py`
- `src/commons/enums.py`
- `docs/PRODUCT/03-booking-and-queue.md`
- `tests/unit/queue/test_ticket_state_machine.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #41
