# Issue 86: Virtual waiting room and travel-time-aware call-forward

**Area:** Backend / Patient
**Milestone:** M11 - Appointments, Check-in & Patient Care Extras
**Owner role:** Backend Lead
**Depends on:** Issues 42, 68
**Estimate:** 2 days
**Status:** Planned

## Context

The 'wait in your car' pattern from US urgent care, adapted for taxi and walking travel. Calling a
patient forward accounting for how long they say they need to arrive is what converts a queue position
into a genuinely usable instruction.

## Scope

- Patient states an expected travel time when joining remotely
- Call-forward alert timed so the patient arrives shortly before their turn, not an hour early
- 'On my way' acknowledgement visible to reception on the board
- Fallback behaviour when a patient does not acknowledge: the ticket holds until the recall rule applies
- Clinic setting to enable or disable the virtual waiting room per site

## Acceptance criteria

- [ ] A patient with a 30-minute travel time is alerted with enough lead time to arrive before their turn
- [ ] The acknowledgement is visible to reception on the dashboard
- [ ] An unacknowledged alert does not lose the patient's place before the recall rule applies
- [ ] The estimate used for the alert is the same one shown to the patient
- [ ] The feature can be disabled per site
- [ ] Travel-time input is optional and defaults sensibly

## Files touched

- `app/services/virtual_waiting.py`
- `app/templates/queue/_travel_time.html`
- `tests/integration/test_call_forward.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #86
