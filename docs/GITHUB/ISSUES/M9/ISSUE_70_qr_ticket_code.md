# Issue 70: QR ticket code for kiosk check-in and reception lookup

**Area:** Backend / Patient
**Milestone:** M9 - Notifications & Patient PWA
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issues 39, 68
**Estimate:** 1 day
**Status:** Planned

## Context

One code that reception can scan, a kiosk can accept in M11, and a patient can show without any
signal. It removes the 'what was your number again?' exchange that slows every front desk.

## Scope

- QR code encoding the ticket reference, rendered on the ticket page and the printed stub
- Reception lookup by scan or by typing the short reference
- Reference codes short, unambiguous (no 0/O or 1/I) and safe to read aloud
- Offline-capable QR so it displays with no network
- Codes single-use per visit and expiring at the end of the service day

## Acceptance criteria

- [ ] Scanning the QR at reception opens that exact ticket
- [ ] The short code is readable aloud without ambiguity
- [ ] The QR renders offline from cached data
- [ ] A code from a previous day is rejected with a clear message
- [ ] The code cannot be reverse-engineered into another patient's ticket
- [ ] Both the printed stub and the on-screen ticket carry the same code

## Files touched

- `app/services/ticket_codes.py`
- `app/templates/queue/_qr.html`
- `app/web/dashboard/lookup.py`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #70
