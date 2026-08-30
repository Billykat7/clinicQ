# Issue 67: Notification preferences, quiet hours and opt-out enforcement

**Area:** Backend / Notifications
**Milestone:** M9 - Notifications & Patient PWA
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issues 63, 21
**Estimate:** 2 days
**Status:** Planned

## Context

Consent is not a one-time checkbox: a patient can change their mind, and nobody should receive a
queue notification at 02:00. The sender must consult preferences and quiet hours before every send, and
that must be impossible to bypass.

## Scope

- Per-patient preferences: preferred transport, language, quiet-hours window, per-event opt-outs
- Global opt-out honoured across every channel and every event type
- Quiet hours suppressing non-urgent notifications, with a documented urgent exception ('you are next')
- Preference management from the ticket page, USSD, WhatsApp and a reply keyword
- A send-time gate that every transport must pass through

## Acceptance criteria

- [ ] A send outside the allowed window or after an opt-out is blocked, proven by a test
- [ ] Opting out via an SMS reply keyword takes effect immediately across all channels
- [ ] The urgent exception is narrowly defined, documented and tested
- [ ] Preferences are editable without an account, using the ticket reference
- [ ] No transport can dispatch without passing the gate, enforced by a guard test
- [ ] Opt-out state survives the patient joining a queue at a different clinic

## Files touched

- `app/services/notification_prefs.py`
- `app/api/preferences.py`
- `tests/integration/test_quiet_hours.py`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #67
