# Issue 40: Join-queue service and API (all channels), with abuse guards

**Area:** Backend / Queue
**Milestone:** M6 - Queue Engine Core
**Owner role:** Backend Lead
**Depends on:** Issue 39
**Estimate:** 3 days
**Status:** Planned

## Context

One join service, four callers. The invariant this issue defends is fairness: a remote join and a
walk-in draw from the same sequence, in arrival order. A separate 'online' line that jumps the physical
line is the single fastest way to lose a clinic's trust.

## Scope

- `join_queue(site, queue, patient, source, reason_text, consent)` service used by web, USSD, WhatsApp and reception
- One shared sequence per queue regardless of source, no separate online line
- Duplicate guard: one active ticket per patient per queue, with a clear 'you already hold #041' response
- Abuse guards: per-phone rate limit, per-IP limit on the web path, and a per-site daily cap
- Capacity check honouring the queue's daily limit and the clinic's opening hours

## Acceptance criteria

- [ ] A remote join and a walk-in issued in the same second occupy adjacent numbers in one sequence
- [ ] A second join attempt by the same patient on the same queue returns the existing ticket, not a new one
- [ ] Joining a closed or full queue is rejected with an explanatory error
- [ ] The rate limiter blocks bulk joins from one phone number
- [ ] All four sources are exercised by tests against the same service function
- [ ] Consent captured at join time is persisted with the ticket

## Files touched

- `app/services/queue.py`
- `app/api/tickets.py`
- `tests/integration/test_join_queue.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #40
