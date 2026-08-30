# Issue 93: No-show risk insight and staffing recommendation

**Area:** Backend / Reporting
**Milestone:** M12 - Reporting, Analytics & District Dashboards
**Owner role:** Data & Research Lead
**Depends on:** Issues 88, 82
**Estimate:** 3 days
**Status:** Planned

## Context

The analytical piece of the capstone, and a genuinely useful one: which factors actually predict a
no-show here, and what would a second doctor on Monday mornings be worth? Deliberately an interpretable
model, because a clinic manager has to be able to argue with it.

## Scope

- Feature set: channel, lead time, day and hour, reminder delivery status, previous no-shows, distance
- An interpretable model (logistic regression or a shallow tree) with published coefficients
- Per-clinic staffing recommendation derived from arrival patterns and service times
- Honest evaluation: train/test split, baseline comparison, and stated limitations
- Recommendations presented as guidance with reasoning, never as an automated decision

## Acceptance criteria

- [ ] The model beats a naive baseline on held-out data, with the margin reported
- [ ] Coefficients or feature importances are published and interpretable
- [ ] Limitations, including sample size and bias risks, are stated plainly
- [ ] The staffing recommendation explains its reasoning in a sentence a manager can check
- [ ] No individual patient is ever labelled or treated differently on the basis of a score
- [ ] The analysis is reproducible from the fixture dataset for the capstone report

## Files touched

- `app/services/insights.py`
- `notebooks/noshow_analysis.ipynb`
- `docs/RESEARCH/NOSHOW_ANALYSIS.md`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #93
