# Issue 83: Self check-in kiosk and QR arrival check-in

**Area:** Frontend / Patient
**Milestone:** M11 - Appointments, Check-in & Patient Care Extras
**Owner role:** Frontend (Patient) Dev
**Depends on:** Issues 70, 81
**Estimate:** 3 days
**Status:** Planned

## Context

The 07:30 reception bottleneck, borrowed straight from Qmatic and NHS outpatient kiosks. A patient who
already holds a ticket or a booking should be able to say 'I am here' without joining a second queue just
to talk to reception.

## Scope

- Kiosk check-in page for a clinic-door tablet: scan a QR or enter a reference or phone number
- Booking check-in converting a booking to a waiting ticket immediately
- Walk-in self-registration where a clinic enables it, with an optional phone number
- Very large touch targets, a short timeout back to the idle screen, and no keyboard requirement beyond digits
- Kiosk device registration reusing the M8 pairing mechanism

## Acceptance criteria

- [ ] A booked patient checks in by scanning their QR in under 15 seconds
- [ ] Checking in moves the patient to waiting and updates the board
- [ ] The kiosk returns to its idle screen after a short timeout, leaving no personal data on screen
- [ ] The screen is usable by someone with limited dexterity and no reading glasses
- [ ] The kiosk cannot be used to browse away from the check-in app
- [ ] An offline kiosk shows a clear 'please see reception' message rather than failing silently

## Files touched

- `app/web/kiosk/routes.py`
- `app/templates/kiosk/checkin.html`
- `app/services/checkin.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #83
