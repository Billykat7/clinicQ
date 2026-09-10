# Issue 44: Patient cancellation and queue position recalculation

> **In short:** A patient who can no longer come can give their place back from any channel, and everyone behind them moves up straight away.

| | |
|---|---|
| **Milestone** | [M6: Queue Engine Core](../../MILESTONES/M6_queue_engine_core.md) |
| **Sprint** | 7 (weeks 13–14) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Queue |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection |
| **Unblocks** | [Issue 47](../M6/ISSUE_47_queue_contract_concurrency_tests.md): Queue OpenAPI contract, concurrency and state-machine tests |

## Context

Making cancellation easy is what keeps the queue honest: a patient who changes plans and cancels frees
a slot and improves everyone's estimate, whereas one who simply does not arrive costs the clinic a
consultation.

## Starting point

- A transition through `transition_ticket()` (Issue 41) plus a position recalculation; positions are derived from order, so do not store them on each ticket.
- Pushing the update to screens depends on the board stream (Issue 57); until then, the next page load shows the new position.

## Scope

- Patient-initiated cancellation from the ticket page, USSD, WhatsApp or a reply to a notification
- Position recalculation for every ticket behind the cancelled one
- Recalculated estimates pushed to affected patients and to the board
- A cancellation window rule: cancelling after being called requires staff involvement
- Cancellation reason captured optionally, for the M12 analysis

## Out of scope

- The USSD and WhatsApp menu entries (Issues 73, 75), which call this.
- No-show analysis (Issue 93).

## Acceptance criteria

- [ ] Cancelling frees the slot and recalculates positions for everyone behind, within 2 seconds
- [ ] Affected patients see an updated position without refreshing
- [ ] Cancellation works identically on all four channels
- [ ] A called ticket cannot be self-cancelled without staff action
- [ ] Cancellation is audited with the channel it came from
- [ ] Cancellation reasons feed the no-show analysis in M12

## How to verify

1. Cancel ticket #3 of 10: tickets 4–10 each move up one place within 2 seconds.
2. Try to self-cancel a called ticket: refused, with a message to speak to reception.
3. Cancel from each channel's test client: identical outcome and an audit row naming the channel.

## Files touched

- `src/modules/queue/service.py`
- `src/modules/queue/router.py`
- `tests/integration/queue/test_cancellation.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #44
