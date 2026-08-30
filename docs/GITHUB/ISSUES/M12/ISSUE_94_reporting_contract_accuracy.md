# Issue 94: Reporting contract and figure-accuracy tests

**Area:** Backend / Quality
**Milestone:** M12 - Reporting, Analytics & District Dashboards
**Owner role:** Data & Research Lead
**Depends on:** Issues 88–93
**Estimate:** 2 days
**Status:** Planned

## Context

Numbers in a capstone report have to be defensible under questioning. Pinning every figure to a fixed
fixture dataset means any claim in the write-up can be re-derived on demand, by anyone, from the
repository.

## Scope

- Hand-written `contracts/reporting.yaml` with a drift test
- A fixed fixture dataset with hand-calculated expected figures
- Accuracy tests asserting every reported figure against the hand calculations
- Cross-checks that exports, on-screen figures and the API agree
- A documented method note explaining how each metric is defined

## Acceptance criteria

- [ ] Every reported metric is asserted against a hand-calculated expected value
- [ ] Screen, export and API all produce identical figures for the same period
- [ ] The contract documents every reporting route
- [ ] The method note defines each metric unambiguously, including edge cases
- [ ] A change in a metric definition fails a test rather than silently shifting the numbers
- [ ] Figures cited in the capstone report are reproducible from the repository

## Files touched

- `contracts/reporting.yaml`
- `tests/integration/test_reporting_accuracy.py`
- `docs/RESEARCH/METRIC_DEFINITIONS.md`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #94
