# Issue 83: Self check-in kiosk and QR arrival check-in

> **In short:** A tablet at the clinic door lets booked patients check in by scanning their QR code, instead of queueing to tell reception they have arrived.

| | |
|---|---|
| **Milestone** | [M11: Appointments, Check-in & Patient Care Extras](../../MILESTONES/M11_appointments_checkin_patient_care.md) |
| **Sprint** | 9 (weeks 17–18) |
| **Owner** | C, Frontend/Patient (backup: D, Frontend/Clinic) |
| **Area** | Frontend / Patient |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 70](../M9/ISSUE_70_qr_ticket_code.md): QR ticket code for kiosk check-in and reception lookup<br>[Issue 81](../M11/ISSUE_81_booking_reschedule_auto_ticket.md): Book, reschedule, cancel and auto-convert an appointment into a ticket |
| **Unblocks** | No other issue waits on this one. |

## Context

The 07:30 reception bottleneck, borrowed straight from Qmatic and NHS outpatient kiosks. A patient who
already holds a ticket or a booking should be able to say 'I am here' without joining a second queue just
to talk to reception.

## Starting point

- Device pairing reuses the board's mechanism from Issue 61; QR resolving comes from Issue 70.
- It is a public page on a paired device, not a signed-in staff screen.

## Scope

- Kiosk check-in page for a clinic-door tablet: scan a QR or enter a reference or phone number
- Booking check-in converting a booking to a waiting ticket immediately
- Walk-in self-registration where a clinic enables it, with an optional phone number
- Very large touch targets, a short timeout back to the idle screen, and no keyboard requirement beyond digits
- Kiosk device registration reusing the M8 pairing mechanism

## Out of scope

- The board and dashboard updates, which follow automatically from the ticket moving to waiting.

## Acceptance criteria

- [ ] A booked patient checks in by scanning their QR in under 15 seconds
- [ ] Checking in moves the patient to waiting and updates the board
- [ ] The kiosk returns to its idle screen after a short timeout, leaving no personal data on screen
- [ ] The screen is usable by someone with limited dexterity and no reading glasses
- [ ] The kiosk cannot be used to browse away from the check-in app
- [ ] An offline kiosk shows a clear 'please see reception' message rather than failing silently

## How to verify

1. Scan a booked patient's QR: checked in in under 15 seconds, and the board updates.
2. Leave the kiosk after checking in: it returns to the idle screen with nothing personal showing.
3. Unplug the network: "please see reception" appears.

## Files touched

- `src/modules/appointments/checkin.py` (arrivals, walk-ins at the door), `conversion.py` (`convert_one`)
- `src/web/kiosk.py`, `src/templates/kiosk/checkin.html`, `src/static/js/checkin.js`, `src/static/css/kiosk.css`
- `alembic/versions/0044_kiosk_checkin.py`, `src/database/models/display_device.py`, `ticket.py`, `site.py`
- `src/modules/display/devices.py` and `router.py` (a device's kind), `src/web/display.py` (where a paired device lands)
- `src/modules/sites/router.py` (the walk-in switch), `src/web/dashboard/settings.py`, `settings_devices.html`
- `contracts/sites.yaml`, `docs/OPS/KIOSK_SETUP.md`, `tests/integration/appointments/test_kiosk_checkin.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #83
