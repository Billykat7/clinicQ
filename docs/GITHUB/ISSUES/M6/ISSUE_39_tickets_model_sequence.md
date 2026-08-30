# Issue 39: `tickets` model and concurrency-safe daily sequence numbering

**Area:** Backend / Queue
**Milestone:** M6 - Queue Engine Core
**Owner role:** Backend Lead
**Depends on:** Issues 25, 17
**Estimate:** 3 days
**Status:** Planned

## Context

The foundation of the whole product. Two receptionists tapping *Add walk-in* at the same instant must
not both receive `#043`, so the daily sequence is allocated by the database under a unique constraint,
not computed in Python from a `COUNT(*)`, which is the classic way this breaks in production.

## Scope

- `tickets` model: queue, site, patient (nullable for anonymous walk-ins), sequence number, source, display name, reason text, comment consent, status, joined/called/completed timestamps
- Per-site, per-queue, per-service-day sequence allocation under a unique constraint, resilient to concurrency
- Sequence reset at the start of each service day, in `Africa/Johannesburg`
- Indexes supporting the board query (`queue`, `status`, `sequence`) and the patient lookup
- Ticket reference code for QR and reception lookup

## Acceptance criteria

- [ ] 100 concurrent joins produce 100 unique consecutive numbers, proven by a concurrency test
- [ ] The unique constraint on (queue, service_day, sequence) makes a duplicate impossible at the database level
- [ ] Numbers restart at 1 at the start of each service day, tested across a midnight boundary
- [ ] The board query for one queue runs on an index, confirmed by `EXPLAIN`
- [ ] A walk-in with no patient record is representable without a placeholder patient row
- [ ] Reference codes are short, unambiguous and safe to read aloud

## Files touched

- `app/database/models/ticket.py`
- `app/services/ticket_sequence.py`
- `migrations/versions/*_tickets.py`
- `tests/integration/test_sequence_concurrency.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #39
