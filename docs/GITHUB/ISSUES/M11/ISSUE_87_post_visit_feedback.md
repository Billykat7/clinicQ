# Issue 87: Post-visit feedback survey and satisfaction reporting

> **In short:** After a visit, patients are asked one question, answerable with one tap, giving clinic managers a service-quality number they can act on.

| | |
|---|---|
| **Milestone** | [M11: Appointments, Check-in & Patient Care Extras](../../MILESTONES/M11_appointments_checkin_patient_care.md) |
| **Sprint** | 12 (weeks 23–24); the sprint plan puts this in **C**'s lane, see the note below |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Patient |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 41](../M6/ISSUE_41_ticket_lifecycle_state_machine.md): Ticket lifecycle state machine and illegal-transition rejection<br>[Issue 63](../M9/ISSUE_63_notification_service_adapters.md): Notification service, transport adapters and delivery log |
| **Unblocks** | No other issue waits on this one. |

> **Note:** C builds the feedback UI (sprint 12); F owns the survey design and reporting.

## Context

Modelled on the NHS Friends and Family Test: one question, one tap, sent after the visit. It gives a
clinic manager a number they can take to their district, and gives the capstone a genuine
service-quality metric rather than only technical ones.

## Starting point

- Triggered by the `done` transition (Issue 41) and sent through the notification service (Issue 63), which already respects opt-outs.
- Screen free text with the same redaction approach as Issue 6 before it reaches any report.

## Scope

- Feedback prompt sent after a ticket is marked done, on the patient's preferred channel
- One rating question plus an optional free-text comment, answerable in one interaction
- Free text screened for personal information before it reaches a clinic report
- Feedback aggregated per site, per queue and per staff member where relevant
- Consent-gated, opt-out respected, and never sent more than once per visit

## Out of scope

- Public clinic ratings ([backlog item 3](../BACKLOG/BACKLOG_03_clinic_ratings_reviews.md)).

## Acceptance criteria

- [ ] Feedback is requested once per completed visit and never repeats
- [ ] Answering takes one tap or one keypress on every channel
- [ ] Scores aggregate per site and per queue in the M12 reports
- [ ] Free-text comments are stored under the same retention rules as other patient text
- [ ] Patients who opted out receive no request
- [ ] Response rate is measurable so the team can judge whether the prompt works

## How to verify

1. Complete a visit: exactly one feedback request.
2. Answer with one tap on each channel: stored against the visit.
3. Write a phone number in the comment: it is removed before the clinic report shows it.

## Files touched

- `src/modules/appointments/feedback.py` (the request, the answer, the reply, retention, the report), `feedback_router.py`, `feedback_schemas.py`
- `src/database/models/feedback.py`, `alembic/versions/0041_visit_feedback.py`
- `src/modules/queue/lifecycle.py` (the done hook), `src/modules/patients/consent.py` (the survey consent gate)
- `src/core/log_redaction.py` (`screen_free_text`), `src/core/webhook_gateways/africastalking.py` (SMS replies)
- `src/web/feedback.py`, `src/templates/feedback/`, `src/static/js/feedback.js`; the ticket page and join page
- `contracts/feedback.yaml`, `tests/integration/appointments/test_feedback.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #87
