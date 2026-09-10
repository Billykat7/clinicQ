# Issue 42: Wait-time estimation service and `wait_time_samples`

> **In short:** Patients see an honest wait range ("~15–25 min") built from how long this queue has actually been taking, not a made-up single number.

| | |
|---|---|
| **Milestone** | [M6: Queue Engine Core](../../MILESTONES/M6_queue_engine_core.md) |
| **Sprint** | 7 (weeks 13–14) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Queue |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 26](../M4/ISSUE_26_services_catalogue_service_times.md): Services catalogue with expected service times<br>[Issue 39](../M6/ISSUE_39_tickets_model_sequence.md): `tickets` model and concurrency-safe daily sequence numbering |
| **Unblocks** | [Issue 47](../M6/ISSUE_47_queue_contract_concurrency_tests.md): Queue OpenAPI contract, concurrency and state-machine tests<br>[Issue 68](../M9/ISSUE_68_patient_ticket_page.md): Patient ticket page: live position, ETA countdown, cancel<br>[Issue 86](../M11/ISSUE_86_virtual_waiting_room.md): Virtual waiting room and travel-time-aware call-forward |

## Context

An estimate that slips damages trust more than no estimate at all, which is why the output is a
**range** with a confidence signal rather than a single confident-looking number. The estimator starts
from the service catalogue's expected duration and switches to observed samples as they accumulate.

## Starting point

- Keep the estimator a pure function (samples in, range out) in `src/modules/queue/estimate.py`, so it is testable without a database.
- Until enough samples exist, fall back to the service's expected minutes from Issue 26.
- F writes the estimate methodology in sprint 6; read it before choosing N and the trimming rule.

## Scope

- `wait_time_samples`: queue, ticket, actual wait minutes, service minutes, recorded at
- Rolling estimate from the last N completed visits on that queue, with outlier trimming
- Output as a range (`~15–25 min`) plus a confidence level derived from sample count and variance
- Cold-start fallback to the service's expected duration until enough samples exist
- Hour-of-day weighting so a 07:30 estimate is not built from 14:00 data

## Out of scope

- Showing the range on screens (Issues 35, 49, 68 display it).
- The nightly statistics (Issue 88).

## Acceptance criteria

- [ ] Estimates are always a range, never a single number, on every surface
- [ ] Fewer than N samples produces a low-confidence estimate clearly labelled as approximate
- [ ] A single very long visit does not distort the estimate (outlier trimming works)
- [ ] The estimate updates as tickets complete, within one minute
- [ ] Estimator accuracy is measured against the fixture dataset and reported in the PR
- [ ] The estimator is a pure function of its inputs and is unit-testable without a database

## How to verify

1. `pytest tests/unit/queue/test_wait_estimate.py`: ranges, the cold start, outlier trimming and hour-of-day weighting.
2. The PR reports the estimator's error against the fixture dataset.

## Files touched

- `src/modules/queue/estimate.py`
- `src/database/models/wait_time_sample.py`
- `tests/unit/queue/test_wait_estimate.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #42
