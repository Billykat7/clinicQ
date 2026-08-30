# Milestone 6: Queue Engine Core

**Status:** 📋 planned · **Phase:** Semester 1 · Sprint 6–7 · **critical path** · **Suggested tag:** `v0.6.0`
**Primary owner:** Backend Lead
**Depends on:** M4 (M3 for patient identity)
**Blocks:** M7, M8, M9, M10, M11, M12; the entire second half of the project. This is the critical path, so protect it.

## Goal

Build the heart of the product: one fair queue per room, joined from any channel, with concurrency-safe ticket numbering, a strict lifecycle, honest wait estimates, recall and no-show handling, transfers between rooms, and audited clinical priority overrides.

## Why this milestone exists

Every other surface is a view onto this engine. The two rules that matter most are worth stating as
invariants rather than features:

1. **One queue, not two.** A remote join and a walk-in take numbers from the *same* sequence. The
   common failure mode in naive queue apps is an online line that quietly jumps the physical line;
   patients notice within a day and staff stop using the system.
2. **Numbering is concurrency-safe.** Two receptionists tapping *Add walk-in* at the same instant
   must not both get `#043`. This is enforced in the database, not in Python.

Wait estimates are shown as a **range** ("~15–25 min"), never a single number, because a
false-precision estimate that slips is worse for trust than an honest band.

## Scope

- `tickets` model with a per-site, per-queue, per-day sequence allocated under a database constraint
- Join API used by every channel, with an abuse guard against duplicate and bulk joins
- Explicit lifecycle state machine: `waiting → called → in_progress → done | no_show | cancelled`
- Wait estimation from `wait_time_samples`, published as a range with a confidence signal
- Recall timers and automatic no-show transitions driven by `arq` scheduled jobs
- Patient-initiated cancellation with position recalculation for everyone behind
- Transfer between queues (triage → doctor → pharmacy) that preserves the visit
- Clinical priority override with a mandatory reason code, staff id and audit row
- Queue OpenAPI contract, concurrency tests and state-machine property tests

## Issues

| # | Title |
|---|-------|
| 39 | `tickets` model and concurrency-safe daily sequence numbering |
| 40 | Join-queue service and API (all channels), with abuse guards |
| 41 | Ticket lifecycle state machine and illegal-transition rejection |
| 42 | Wait-time estimation service and `wait_time_samples` |
| 43 | Recall timers and automatic no-show transitions (`arq` jobs) |
| 44 | Patient cancellation and queue position recalculation |
| 45 | Transfer between queues without re-joining (triage → doctor → pharmacy) |
| 46 | Clinical priority override with reason codes and audit trail |
| 47 | Queue OpenAPI contract, concurrency and state-machine tests |

## Exit criteria

- [ ] 100 concurrent joins on one queue produce 100 unique consecutive numbers, proven by a test
- [ ] Every illegal transition (e.g. `done → called`) is rejected with 409 and never mutates state
- [ ] A walk-in and a remote join issued in the same second occupy adjacent numbers in one sequence
- [ ] The estimate is a range derived from the last N completed visits on that queue, not a constant
- [ ] A called ticket that is not attended auto-recalls once, then becomes `no_show`, and the patient is told why
- [ ] A priority override cannot be saved without a reason code, and every one is visible in the audit log

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M6/)
