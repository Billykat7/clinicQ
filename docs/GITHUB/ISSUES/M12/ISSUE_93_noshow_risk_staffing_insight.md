# Issue 93: No-show risk insight and staffing recommendation

> **In short:** An honest, explainable look at what predicts a no-show, turned into staffing suggestions a manager can check for themselves.

| | |
|---|---|
| **Milestone** | [M12: Reporting, Analytics & District Dashboards](../../MILESTONES/M12_reporting_analytics.md) |
| **Sprint** | 12 (weeks 23–24) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Reporting |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 82](../M11/ISSUE_82_appointment_reminders_reply.md): Appointment reminders with confirm/cancel by reply<br>[Issue 88](../M12/ISSUE_88_daily_stats_worker.md): Nightly `daily_queue_stats` aggregation worker |
| **Unblocks** | [Issue 94](../M12/ISSUE_94_reporting_contract_accuracy.md): Reporting contract and figure-accuracy tests |

## Context

The analytical piece of the capstone, and a genuinely useful one: which factors actually predict a
no-show here, and what would a second doctor on Monday mornings be worth? Deliberately an interpretable
model, because a clinic manager has to be able to argue with it.

## Starting point

- `pandas` and `matplotlib` are already in `requirements.txt`; add `scikit-learn` only if the analysis needs it.
- The notebook is research output for the capstone; the product only ever shows the plain-language recommendation.

## Scope

- Feature set: channel, lead time, day and hour, reminder delivery status, previous no-shows, distance
- An interpretable model (logistic regression or a shallow tree) with published coefficients
- Per-clinic staffing recommendation derived from arrival patterns and service times
- Honest evaluation: train/test split, baseline comparison, and stated limitations
- Recommendations presented as guidance with reasoning, never as an automated decision

## Out of scope

- Treating any individual patient differently because of a score (never allowed).

## Acceptance criteria

- [ ] The model beats a naive baseline on held-out data, with the margin reported
- [ ] Coefficients or feature importances are published and interpretable
- [ ] Limitations, including sample size and bias risks, are stated plainly
- [ ] The staffing recommendation explains its reasoning in a sentence a manager can check
- [ ] No individual patient is ever labelled or treated differently on the basis of a score
- [ ] The analysis is reproducible from the fixture dataset for the capstone report

## How to verify

1. Re-run the notebook from the fixture dataset: the same results.
2. The write-up reports the margin over a naive baseline and states the limitations.
3. Read a staffing recommendation aloud to a teammate: they can check its reasoning.

## Files touched

- `src/modules/reporting/insights.py`
- `notebooks/noshow_analysis.ipynb`
- `docs/RESEARCH/NOSHOW_ANALYSIS.md`

---

**Refs:** [M12 milestone](../../MILESTONES/M12_reporting_analytics.md) · [product docs](../../../PRODUCT/05-clinic-dashboard.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #93
