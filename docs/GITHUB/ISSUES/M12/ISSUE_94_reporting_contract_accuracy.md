# Issue 94: Reporting contract and figure-accuracy tests

> **In short:** Every number the product reports is checked against a hand calculation, so the figures in the capstone report can be trusted and reproduced.

| | |
|---|---|
| **Milestone** | [M12: Reporting, Analytics & District Dashboards](../../MILESTONES/M12_reporting_analytics.md) |
| **Sprint** | 12 (weeks 23–24) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Quality |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 88](../M12/ISSUE_88_daily_stats_worker.md): Nightly `daily_queue_stats` aggregation worker<br>[Issue 89](../M12/ISSUE_89_clinic_reports_ui.md): Clinic reports UI: wait time, no-show, channel mix, heatmap<br>[Issue 90](../M12/ISSUE_90_live_operational_kpis.md): Live operational KPIs on the manager dashboard<br>[Issue 91](../M12/ISSUE_91_exports_scheduled_reports.md): CSV/PDF export and scheduled weekly email report<br>[Issue 92](../M12/ISSUE_92_district_aggregate_dashboard.md): Anonymised district aggregate dashboard with small-cell suppression<br>[Issue 93](../M12/ISSUE_93_noshow_risk_staffing_insight.md): No-show risk insight and staffing recommendation |
| **Unblocks** | No other issue waits on this one. |

## Context

Numbers in a capstone report have to be defensible under questioning. Pinning every figure to a fixed
fixture dataset means any claim in the write-up can be re-derived on demand, by anyone, from the
repository.

## Starting point

- Reuse the contract drift test from Issue 30.
- Build the fixture dataset once with the factories from Issue 8 and keep it in the repository.

## Scope

- Hand-written `contracts/reporting.yaml` with a drift test
- A fixed fixture dataset with hand-calculated expected figures
- Accuracy tests asserting every reported figure against the hand calculations
- Cross-checks that exports, on-screen figures and the API agree
- A documented method note explaining how each metric is defined

## Out of scope

- Performance of the reports (Issue 105).

## Acceptance criteria

- [ ] Every reported metric is asserted against a hand-calculated expected value
- [ ] Screen, export and API all produce identical figures for the same period
- [ ] The contract documents every reporting route
- [ ] The method note defines each metric unambiguously, including edge cases
- [ ] A change in a metric definition fails a test rather than silently shifting the numbers
- [ ] Figures cited in the capstone report are reproducible from the repository

## How to verify

1. Change a metric's definition in code without updating the test: the suite fails.
2. Compare the screen, the CSV and the API for one period: identical.
3. `docs/RESEARCH/METRIC_DEFINITIONS.md` defines every metric, including edge cases.

## Files touched

- `contracts/reporting.yaml`
- `tests/integration/reporting/test_reporting_accuracy.py`
- `docs/RESEARCH/METRIC_DEFINITIONS.md`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #94
