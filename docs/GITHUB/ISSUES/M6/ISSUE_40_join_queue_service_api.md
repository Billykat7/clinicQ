# Issue 40: Join-queue service and API (all channels), with abuse guards

> **In short:** One function puts a patient in a queue, whatever door they came through, which is how the "one fair queue" rule is kept.

| | |
|---|---|
| **Milestone** | [M6: Queue Engine Core](../../MILESTONES/M6_queue_engine_core.md) |
| **Sprint** | 6 (weeks 11–12) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Queue |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 39](../M6/ISSUE_39_tickets_model_sequence.md): `tickets` model and concurrency-safe daily sequence numbering |
| **Unblocks** | [Issue 47](../M6/ISSUE_47_queue_contract_concurrency_tests.md): Queue OpenAPI contract, concurrency and state-machine tests<br>[Issue 49](../M7/ISSUE_49_front_desk_board_live.md): Front-desk board: all active queues, live via SSE with polling fallback<br>[Issue 51](../M7/ISSUE_51_walkin_intake_ticket_stub.md): Walk-in intake form and printable ticket stub<br>[Issue 68](../M9/ISSUE_68_patient_ticket_page.md): Patient ticket page: live position, ETA countdown, cancel<br>[Issue 72](../M10/ISSUE_72_channel_adapter_framework.md): Channel adapter framework with Redis session state<br>[Issue 97](../M13/ISSUE_97_hardening_rate_limits_scanning.md): Hardening: rate limits, security headers, dependency and secret scanning |

## Context

One join service, four callers. The invariant this issue defends is fairness: a remote join and a
walk-in draw from the same sequence, in arrival order. A separate 'online' line that jumps the physical
line is the single fastest way to lose a clinic's trust.

## Starting point

- Build on Issue 39's model. Every channel (web, USSD, WhatsApp, reception) must call this one function; that is non-negotiable 1 in the [guideline](../../../guideline.md).
- Reuse the kernel's sliding-window limiter (`src/core/rate_limit.py`) for the per-phone and per-IP guards.

## Scope

- `join_queue(site, queue, patient, source, reason_text, consent)` service used by web, USSD, WhatsApp and reception
- One shared sequence per queue regardless of source, no separate online line
- Duplicate guard: one active ticket per patient per queue, with a clear 'you already hold #041' response
- Abuse guards: per-phone rate limit, per-IP limit on the web path, and a per-site daily cap
- Capacity check honouring the queue's daily limit and the clinic's opening hours

## Out of scope

- Walk-in intake screens (Issue 51) and channel adapters (M10), which only call this.
- Cancellation (Issue 44).

## Acceptance criteria

- [ ] A remote join and a walk-in issued in the same second occupy adjacent numbers in one sequence
- [ ] A second join attempt by the same patient on the same queue returns the existing ticket, not a new one
- [ ] Joining a closed or full queue is rejected with an explanatory error
- [ ] The rate limiter blocks bulk joins from one phone number
- [ ] All four sources are exercised by tests against the same service function
- [ ] Consent captured at join time is persisted with the ticket

## How to verify

1. Join the same queue twice as one patient: the second call returns the first ticket.
2. Join from a web session and a reception walk-in in the same second: adjacent numbers.
3. Join a closed queue: refused with a reason a patient would understand.

## Files touched

- `src/modules/queue/service.py`
- `src/modules/queue/router.py`
- `tests/integration/queue/test_join_queue.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #40
