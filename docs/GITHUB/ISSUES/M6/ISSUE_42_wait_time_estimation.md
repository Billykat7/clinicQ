# Issue 42: Wait-time estimation service and `wait_time_samples`

**Area:** Backend / Queue
**Milestone:** M6 - Queue Engine Core
**Owner role:** Backend Lead
**Depends on:** Issues 39, 26
**Estimate:** 3 days
**Status:** Planned

## Context

An estimate that slips damages trust more than no estimate at all, which is why the output is a
**range** with a confidence signal rather than a single confident-looking number. The estimator starts
from the service catalogue's expected duration and switches to observed samples as they accumulate.

## Scope

- `wait_time_samples`: queue, ticket, actual wait minutes, service minutes, recorded at
- Rolling estimate from the last N completed visits on that queue, with outlier trimming
- Output as a range (`~15–25 min`) plus a confidence level derived from sample count and variance
- Cold-start fallback to the service's expected duration until enough samples exist
- Hour-of-day weighting so a 07:30 estimate is not built from 14:00 data

## Acceptance criteria

- [ ] Estimates are always a range, never a single number, on every surface
- [ ] Fewer than N samples produces a low-confidence estimate clearly labelled as approximate
- [ ] A single very long visit does not distort the estimate (outlier trimming works)
- [ ] The estimate updates as tickets complete, within one minute
- [ ] Estimator accuracy is measured against the fixture dataset and reported in the PR
- [ ] The estimator is a pure function of its inputs and is unit-testable without a database

## Files touched

- `app/services/wait_estimate.py`
- `app/database/models/wait_time_sample.py`
- `tests/unit/test_wait_estimate.py`

---

**Refs:** [M6 milestone](../../MILESTONES/M6_queue_engine_core.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #42
