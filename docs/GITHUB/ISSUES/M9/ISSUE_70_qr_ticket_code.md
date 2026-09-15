# Issue 70: QR ticket code for kiosk check-in and reception lookup

> **In short:** Every ticket carries a QR code and a short code that reception can scan or type to pull it up instantly.

| | |
|---|---|
| **Milestone** | [M9: Notifications & Patient PWA](../../MILESTONES/M9_notifications_patient_pwa.md) |
| **Sprint** | 8 (weeks 15–16); the sprint plan puts this in **C**'s lane, see the note below |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Patient |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 39](../M6/ISSUE_39_tickets_model_sequence.md): `tickets` model and concurrency-safe daily sequence numbering<br>[Issue 68](../M9/ISSUE_68_patient_ticket_page.md): Patient ticket page: live position, ETA countdown, cancel |
| **Unblocks** | [Issue 71](../M9/ISSUE_71_notifications_contract_tests.md): Notification OpenAPI contract, delivery and retry tests<br>[Issue 83](../M11/ISSUE_83_kiosk_self_checkin.md): Self check-in kiosk and QR arrival check-in |

> **Note:** The spec names B as owner; the sprint plan puts the QR ticket in C's lane (sprint 8). Agree before sprint 8.

## Context

One code that reception can scan, a kiosk can accept in M11, and a patient can show without any
signal. It removes the 'what was your number again?' exchange that slows every front desk.

## Starting point

- The reference code itself is created with the ticket (Issue 39); this issue draws and resolves it.
- `qrcode` is already in `requirements.txt`.

## Scope

- QR code encoding the ticket reference, rendered on the ticket page and the printed stub
- Reception lookup by scan or by typing the short reference
- Reference codes short, unambiguous (no 0/O or 1/I) and safe to read aloud
- Offline-capable QR so it displays with no network
- Codes single-use per visit and expiring at the end of the service day

## Out of scope

- Kiosk check-in by scanning (Issue 83).

## Acceptance criteria

- [ ] Scanning the QR at reception opens that exact ticket
- [ ] The short code is readable aloud without ambiguity
- [ ] The QR renders offline from cached data
- [ ] A code from a previous day is rejected with a clear message
- [ ] The code cannot be reverse-engineered into another patient's ticket
- [ ] Both the printed stub and the on-screen ticket carry the same code

## How to verify

1. Scan the QR from a ticket page at reception: that exact ticket opens.
2. Read the short code aloud to a teammate: they type it correctly first time.
3. Try yesterday's code: rejected with a clear message.

## Files touched

- `src/modules/queue/ticket_codes.py`
- `src/templates/queue/_qr.html` (drawn on `queue/ticket.html`, `patient/offline.html` and `print/ticket_stub.html`)
- `src/web/dashboard/lookup.py`, `src/templates/dashboard/lookup.html`
- `src/modules/queue/router.py` (`GET /sites/{site_id}/tickets/lookup`), `contracts/queue.yaml`
- `docs/OPS/RECEPTION_LOOKUP.md`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/03-booking-and-queue.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #70
