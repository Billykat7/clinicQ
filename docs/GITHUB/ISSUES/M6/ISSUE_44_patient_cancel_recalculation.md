# Issue 44: Patient cancellation and queue position recalculation

**Area:** Backend / Queue
**Milestone:** M6 - Queue Engine Core
**Owner role:** Backend Lead
**Depends on:** Issue 41
**Estimate:** 2 days
**Status:** Planned

## Context

Making cancellation easy is what keeps the queue honest: a patient who changes plans and cancels frees
a slot and improves everyone's estimate, whereas one who simply does not arrive costs the clinic a
consultation.

## Scope

- Patient-initiated cancellation from the ticket page, USSD, WhatsApp or a reply to a notification
- Position recalculation for every ticket behind the cancelled one
- Recalculated estimates pushed to affected patients and to the board
- A cancellation window rule: cancelling after being called requires staff involvement
- Cancellation reason captured optionally, for the M12 analysis

## Acceptance criteria

- [ ] Cancelling frees the slot and recalculates positions for everyone behind, within 2 seconds
- [ ] Affected patients see an updated position without refreshing
- [ ] Cancellation works identically on all four channels
- [ ] A called ticket cannot be self-cancelled without staff action
- [ ] Cancellation is audited with the channel it came from
- [ ] Cancellation reasons feed the no-show analysis in M12

## Files touched

- `app/services/queue.py`
- `app/api/tickets.py`
- `tests/integration/test_cancellation.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #44
