# Issue 86: Virtual waiting room and travel-time-aware call-forward

> **In short:** Patients can wait at home or nearby and be told when to leave, based on how long they say the trip takes.

| | |
|---|---|
| **Milestone** | [M11: Appointments, Check-in & Patient Care Extras](../../MILESTONES/M11_appointments_checkin_patient_care.md) |
| **Sprint** | 10 (weeks 19–20) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Patient |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 42](../M6/ISSUE_42_wait_time_estimation.md): Wait-time estimation service and `wait_time_samples`<br>[Issue 68](../M9/ISSUE_68_patient_ticket_page.md): Patient ticket page: live position, ETA countdown, cancel |
| **Unblocks** | No other issue waits on this one. |

## Context

The 'wait in your car' pattern from US urgent care, adapted for taxi and walking travel. Calling a
patient forward accounting for how long they say they need to arrive is what converts a queue position
into a genuinely usable instruction.

## Starting point

- Uses the same wait estimate the patient sees (Issue 42), so the alert and the screen never disagree.
- An unacknowledged alert falls back to the recall rules from Issue 43.

## Scope

- Patient states an expected travel time when joining remotely
- Call-forward alert timed so the patient arrives shortly before their turn, not an hour early
- 'On my way' acknowledgement visible to reception on the board
- Fallback behaviour when a patient does not acknowledge: the ticket holds until the recall rule applies
- Clinic setting to enable or disable the virtual waiting room per site

## Out of scope

- Live traffic or routing data: the patient's own travel estimate is the input.

## Acceptance criteria

- [ ] A patient with a 30-minute travel time is alerted with enough lead time to arrive before their turn
- [ ] The acknowledgement is visible to reception on the dashboard
- [ ] An unacknowledged alert does not lose the patient's place before the recall rule applies
- [ ] The estimate used for the alert is the same one shown to the patient
- [ ] The feature can be disabled per site
- [ ] Travel-time input is optional and defaults sensibly

## How to verify

1. Join with a 30-minute travel time: the alert arrives with enough lead to arrive before the turn.
2. Tap "on my way": reception sees it on the dashboard.
3. Disable the feature for a site: no travel-time question is asked.

## Files touched

- `src/modules/appointments/virtual_waiting.py`
- `src/templates/queue/_travel_time.html`
- `tests/integration/appointments/test_call_forward.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #86
