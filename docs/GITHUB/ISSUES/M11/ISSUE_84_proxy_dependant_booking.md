# Issue 84: Proxy booking for dependants with recorded consent

> **In short:** One phone can serve a household: a parent can book for a child, or a daughter for her mother, with the ticket belonging to the person being seen.

| | |
|---|---|
| **Milestone** | [M11: Appointments, Check-in & Patient Care Extras](../../MILESTONES/M11_appointments_checkin_patient_care.md) |
| **Sprint** | 10 (weeks 19–20) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Appointments |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 17](../M3/ISSUE_17_patient_identity_otp.md): Patient identity: phone-first records with OTP verification<br>[Issue 81](../M11/ISSUE_81_booking_reschedule_auto_ticket.md): Book, reschedule, cancel and auto-convert an appointment into a ticket |
| **Unblocks** | No other issue waits on this one. |

## Context

A daughter books for her mother; a parent books for a child. Modelled after NHS App linked profiles,
this is one of the highest-value features in a market where one household often shares a single
smartphone, and it needs a clear consent and audit trail to stay defensible.

## Starting point

- Builds on the patient identity (Issue 17) and consent (Issue 21) models in `src/modules/patients/`.
- Notifications go to the proxy's phone through the normal notification service; the board shows the dependant under the site's display rules.

## Scope

- `patient_links`: proxy patient, dependant, relationship, consent record, active flag
- Booking and joining on behalf of a dependant, with the acting patient recorded on the ticket
- Notifications routed to the proxy's phone while the ticket belongs to the dependant
- Consent capture for acting on behalf, and a revocation path
- Board and dashboard clearly showing the dependant's details, not the proxy's

## Out of scope

- Legal guardianship verification beyond a phone check (not in the capstone scope).

## Acceptance criteria

- [ ] A proxy can join a queue for a dependant, and the ticket belongs to the dependant
- [ ] Notifications reach the proxy's phone
- [ ] Every proxy action records who acted for whom, visible in the audit log
- [ ] Revoking the link stops further proxy actions immediately
- [ ] The board shows the dependant under the site's display rules, never the proxy
- [ ] A patient cannot link themselves to an arbitrary phone number without a verification step

## How to verify

1. Link a dependant, then join a queue for them: the ticket is theirs, the messages go to the proxy.
2. Revoke the link: the next proxy action is refused.
3. Try to link to a phone number without verifying it: refused.

## Files touched

- `src/modules/patients/proxy.py`
- `src/database/models/patient_link.py`
- `alembic/versions/NNNN_patient_links.py`
- `tests/integration/patients/test_proxy_booking.py`

---

**Refs:** [M11 milestone](../../MILESTONES/M11_appointments_checkin_patient_care.md) · [product docs](../../../PRODUCT/14-benchmark.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #84
