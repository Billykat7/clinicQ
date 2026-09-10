# Issue 39: `tickets` model and concurrency-safe daily sequence numbering

> **In short:** Every ticket gets a number that is unique for its queue and day, even when two receptionists tap at the same instant, because the database guarantees it.

| | |
|---|---|
| **Milestone** | [M6: Queue Engine Core](../../MILESTONES/M6_queue_engine_core.md) |
| **Sprint** | 6 (weeks 11–12) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Queue |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 17](../M3/ISSUE_17_patient_identity_otp.md): Patient identity: phone-first records with OTP verification<br>[Issue 25](../M4/ISSUE_25_queues_model_multiroom.md): `queues` model: multi-room, multi-service queues per site |
| **Unblocks** | [Issue 40](../M6/ISSUE_40_join_queue_service_api.md): Join-queue service and API (all channels), with abuse guards<br>[Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection<br>[Issue 42](../M6/ISSUE_42_wait_time_estimation.md): Wait-time estimation service and `wait_time_samples`<br>[Issue 47](../M6/ISSUE_47_queue_contract_concurrency_tests.md): Queue OpenAPI contract, concurrency and state-machine tests<br>[Issue 70](../M9/ISSUE_70_qr_ticket_code.md): QR ticket code for kiosk check-in and reception lookup<br>[Issue 88](../M12/ISSUE_88_daily_stats_worker.md): Nightly `daily_queue_stats` aggregation worker |

## Context

The foundation of the whole product. Two receptionists tapping *Add walk-in* at the same instant must
not both receive `#043`, so the daily sequence is allocated by the database under a unique constraint,
not computed in Python from a `COUNT(*)`, which is the classic way this breaks in production.

## Starting point

- Greenfield, in `src/modules/queue/` next to the `queues` model from Issue 25.
- Allocate the number inside the database (a counter row locked with `SELECT … FOR UPDATE`, or an upsert that returns the new value) under the unique constraint; never `COUNT(*) + 1` in Python.
- The service day is the Johannesburg date from Issue 4's helpers, not the UTC date.

## Scope

- `tickets` model: queue, site, patient (nullable for anonymous walk-ins), sequence number, source, display name, reason text, comment consent, status, joined/called/completed timestamps
- Per-site, per-queue, per-service-day sequence allocation under a unique constraint, resilient to concurrency
- Sequence reset at the start of each service day, in `Africa/Johannesburg`
- Indexes supporting the board query (`queue`, `status`, `sequence`) and the patient lookup
- Ticket reference code for QR and reception lookup

## Out of scope

- Joining a queue (Issue 40) and changing a ticket's status (Issue 41).
- The QR code drawn from the reference (Issue 70).

## Acceptance criteria

- [ ] 100 concurrent joins produce 100 unique consecutive numbers, proven by a concurrency test
- [ ] The unique constraint on (queue, service_day, sequence) makes a duplicate impossible at the database level
- [ ] Numbers restart at 1 at the start of each service day, tested across a midnight boundary
- [ ] The board query for one queue runs on an index, confirmed by `EXPLAIN`
- [ ] A walk-in with no patient record is representable without a placeholder patient row
- [ ] Reference codes are short, unambiguous and safe to read aloud

## How to verify

1. `pytest tests/integration/queue/test_sequence_concurrency.py`: 100 parallel joins give 1–100 with no gaps or repeats.
2. Insert a duplicate `(queue, service_day, sequence)` by hand in psql: the constraint refuses it.
3. Freeze the clock at 23:59 and 00:01 Johannesburg time: the second ticket is `#001`.

## Files touched

- `src/modules/queue/sequence.py`
- `src/database/models/ticket.py`
- `alembic/versions/NNNN_tickets.py`
- `tests/integration/queue/test_sequence_concurrency.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #39
