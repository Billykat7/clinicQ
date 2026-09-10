# Issue 21: Consent capture and withdrawal (display, notifications, board comment)

> **In short:** A patient's name or reason only reaches a public screen, or a message only reaches their phone, if they said yes, and saying no again takes effect immediately.

| | |
|---|---|
| **Milestone** | [M3: Identity, Auth, RBAC & Consent](../../MILESTONES/M3_identity_auth_rbac.md) |
| **Sprint** | 3 (weeks 5–6) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / Compliance |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 17](../M3/ISSUE_17_patient_identity_otp.md): Patient identity: phone-first records with OTP verification |
| **Unblocks** | [Issue 27](../M4/ISSUE_27_display_privacy_settings.md): Display and privacy settings per site<br>[Issue 58](../M8/ISSUE_58_board_privacy_rendering.md): Server-side privacy-mode rendering and consent gating<br>[Issue 63](../M9/ISSUE_63_notification_service_adapters.md): Notification service, transport adapters and delivery log<br>[Issue 67](../M9/ISSUE_67_notification_preferences_quiet_hours.md): Notification preferences, quiet hours and opt-out enforcement |

## Context

The display board (M8) and the notification service (M9) both have to ask the same question before
they act: did this patient agree to this? Consent is therefore modelled per patient, per purpose, with a
timestamp and a withdrawal path, not as a single boolean on the patient row.

## Starting point

- Nothing for patient consent exists yet. It belongs with the `patients` module from Issue 17.
- The kernel already checks every notification against user preferences in one place (`resolve` in `src/modules/notifications/preferences.py`). Call `has_consent()` from there, so no send path can skip it.

## Scope

- `patient_consents`: patient, purpose (`display_name`, `display_comment`, `notifications`, `feedback_survey`), granted, source channel, timestamp
- Capture at join time, worded in plain language and defaulting to the privacy-preserving option
- Withdrawal endpoint and USSD/WhatsApp menu path that takes effect immediately
- A `has_consent(patient, purpose)` helper that the board and notification services must call
- Consent history retained for proof, while the effective state is a single current value

## Out of scope

- The board's privacy projection (Issue 58), which reads this consent.
- Retention of consent history (Issue 95).

## Acceptance criteria

- [ ] Consent defaults to the most private option on every channel
- [ ] Withdrawing display consent removes the patient's name from the board on the next render
- [ ] Withdrawing notification consent stops sends immediately, proven by a test
- [ ] Consent capture is recorded with the channel it came from
- [ ] The board and notification services cannot bypass `has_consent()`, enforced by a guard test
- [ ] Consent wording is reviewed against POPIA plain-language expectations and committed

## How to verify

1. Join a queue on each channel without touching the consent options: the most private option is stored.
2. Withdraw display consent: the next board render shows the number only.
3. Withdraw notification consent: the next queued message is not sent, and the log says why.

## Files touched

- `src/modules/patients/consent.py`
- `src/database/models/patient_consent.py`
- `src/templates/patient/consent.html`
- `tests/integration/patients/test_consent.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #21
