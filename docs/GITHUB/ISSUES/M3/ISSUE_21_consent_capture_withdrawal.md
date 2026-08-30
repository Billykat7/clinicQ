# Issue 21: Consent capture and withdrawal (display, notifications, board comment)

**Area:** Backend / Compliance
**Milestone:** M3 - Identity, Auth, RBAC & Consent
**Owner role:** Data & Research Lead
**Depends on:** Issue 17
**Estimate:** 2 days
**Status:** Planned

## Context

The display board (M8) and the notification service (M9) both have to ask the same question before
they act: did this patient agree to this? Consent is therefore modelled per patient, per purpose, with a
timestamp and a withdrawal path, not as a single boolean on the patient row.

## Scope

- `patient_consents`: patient, purpose (`display_name`, `display_comment`, `notifications`, `feedback_survey`), granted, source channel, timestamp
- Capture at join time, worded in plain language and defaulting to the privacy-preserving option
- Withdrawal endpoint and USSD/WhatsApp menu path that takes effect immediately
- A `has_consent(patient, purpose)` helper that the board and notification services must call
- Consent history retained for proof, while the effective state is a single current value

## Acceptance criteria

- [ ] Consent defaults to the most private option on every channel
- [ ] Withdrawing display consent removes the patient's name from the board on the next render
- [ ] Withdrawing notification consent stops sends immediately, proven by a test
- [ ] Consent capture is recorded with the channel it came from
- [ ] The board and notification services cannot bypass `has_consent()`, enforced by a guard test
- [ ] Consent wording is reviewed against POPIA plain-language expectations and committed

## Files touched

- `app/database/models/patient_consent.py`
- `app/services/consent.py`
- `app/templates/patient/consent.html`
- `tests/integration/test_consent.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #21
