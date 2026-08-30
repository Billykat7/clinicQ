# Issue 87: Post-visit feedback survey and satisfaction reporting

**Area:** Backend / Patient
**Milestone:** M11 - Appointments, Check-in & Patient Care Extras
**Owner role:** Data & Research Lead
**Depends on:** Issues 41, 63
**Estimate:** 2 days
**Status:** Planned

## Context

Modelled on the NHS Friends and Family Test: one question, one tap, sent after the visit. It gives a
clinic manager a number they can take to their district, and gives the capstone a genuine
service-quality metric rather than only technical ones.

## Scope

- Feedback prompt sent after a ticket is marked done, on the patient's preferred channel
- One rating question plus an optional free-text comment, answerable in one interaction
- Free text screened for personal information before it reaches a clinic report
- Feedback aggregated per site, per queue and per staff member where relevant
- Consent-gated, opt-out respected, and never sent more than once per visit

## Acceptance criteria

- [ ] Feedback is requested once per completed visit and never repeats
- [ ] Answering takes one tap or one keypress on every channel
- [ ] Scores aggregate per site and per queue in the M12 reports
- [ ] Free-text comments are stored under the same retention rules as other patient text
- [ ] Patients who opted out receive no request
- [ ] Response rate is measurable so the team can judge whether the prompt works

## Files touched

- `app/services/feedback.py`
- `app/database/models/feedback.py`
- `tests/integration/test_feedback.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #87
