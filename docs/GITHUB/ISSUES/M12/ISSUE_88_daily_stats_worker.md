# Issue 88: Nightly `daily_queue_stats` aggregation worker

> **In short:** Each night the day's tickets are summarised into one row per queue, which every report reads instead of re-counting raw tickets.

| | |
|---|---|
| **Milestone** | [M12: Reporting, Analytics & District Dashboards](../../MILESTONES/M12_reporting_analytics.md) |
| **Sprint** | 9 (weeks 17–18) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Reporting |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 39](../M6/ISSUE_39_tickets_model_sequence.md): `tickets` model and concurrency-safe daily sequence numbering<br>[Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection |
| **Unblocks** | [Issue 89](../M12/ISSUE_89_clinic_reports_ui.md): Clinic reports UI: wait time, no-show, channel mix, heatmap<br>[Issue 90](../M12/ISSUE_90_live_operational_kpis.md): Live operational KPIs on the manager dashboard<br>[Issue 92](../M12/ISSUE_92_district_aggregate_dashboard.md): Anonymised district aggregate dashboard with small-cell suppression<br>[Issue 93](../M12/ISSUE_93_noshow_risk_staffing_insight.md): No-show risk insight and staffing recommendation<br>[Issue 94](../M12/ISSUE_94_reporting_contract_accuracy.md): Reporting contract and figure-accuracy tests |

## Context

Reports must never scan the live ticket table across a date range: a heavy report competing with a
Call Next is exactly the wrong trade at 08:00. The nightly aggregation keeps dashboards fast and makes
every published figure reproducible.

## Starting point

- New module: `src/modules/reporting/`.
- The spec says `arq`; the kernel's scheduler (`src/core/scheduler.py`) already provides the advisory lock the criteria ask for. See [open decisions](../README.md#open-decisions).

## Scope

- `daily_queue_stats`: site, queue, date, ticket count, average wait, median wait, no-show count, cancel count, channel mix
- Nightly `arq` job with idempotent re-runs and catch-up for missed days
- Backfill command for historical dates
- Advisory lock so multiple instances cannot double-aggregate
- Data-quality checks flagging impossible values (negative waits, counts exceeding tickets)

## Out of scope

- Any screen (Issues 89, 90).

## Acceptance criteria

- [ ] Re-running the job for a past date produces identical figures
- [ ] A missed day is caught up automatically on the next run
- [ ] Aggregation for a full pilot day completes in under a minute
- [ ] Two concurrent runs cannot double-count, enforced by the advisory lock
- [ ] Data-quality checks fail loudly rather than writing a wrong row
- [ ] Backfilling a month of history is a single documented command

## How to verify

1. Run the job twice for the same date: identical rows.
2. Skip a night, then run: the missed day is filled in.
3. Insert a ticket with a negative wait: the data-quality check fails loudly and writes nothing.

## Files touched

- `src/modules/reporting/stats.py`
- `src/database/models/daily_queue_stats.py`
- `src/core/scheduler.py`
- `alembic/versions/NNNN_daily_queue_stats.py`
- `tests/integration/reporting/test_stats_aggregation.py`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #88
