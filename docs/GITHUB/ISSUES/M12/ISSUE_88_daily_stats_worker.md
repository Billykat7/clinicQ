# Issue 88: Nightly `daily_queue_stats` aggregation worker

**Area:** Backend / Reporting
**Milestone:** M12 - Reporting, Analytics & District Dashboards
**Owner role:** Data & Research Lead
**Depends on:** Issues 39, 41
**Estimate:** 3 days
**Status:** Planned

## Context

Reports must never scan the live ticket table across a date range: a heavy report competing with a
Call Next is exactly the wrong trade at 08:00. The nightly aggregation keeps dashboards fast and makes
every published figure reproducible.

## Scope

- `daily_queue_stats`: site, queue, date, ticket count, average wait, median wait, no-show count, cancel count, channel mix
- Nightly `arq` job with idempotent re-runs and catch-up for missed days
- Backfill command for historical dates
- Advisory lock so multiple instances cannot double-aggregate
- Data-quality checks flagging impossible values (negative waits, counts exceeding tickets)

## Acceptance criteria

- [ ] Re-running the job for a past date produces identical figures
- [ ] A missed day is caught up automatically on the next run
- [ ] Aggregation for a full pilot day completes in under a minute
- [ ] Two concurrent runs cannot double-count, enforced by the advisory lock
- [ ] Data-quality checks fail loudly rather than writing a wrong row
- [ ] Backfilling a month of history is a single documented command

## Files touched

- `workers/stats_worker.py`
- `app/database/models/daily_queue_stats.py`
- `tests/integration/test_stats_aggregation.py`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #88
