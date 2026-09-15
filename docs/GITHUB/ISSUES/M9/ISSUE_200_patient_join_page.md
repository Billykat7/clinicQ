# Issue 200: Patient web sign-in and join page

> **In short:** A patient signs in with their phone number and joins a clinic's queue from the clinic page, and the installed app can sign them in, instead of the "Join the queue" button leading to a 404.

| | |
|---|---|
| **Milestone** | [M9: Notifications & Patient PWA](../../MILESTONES/M9_notifications_patient_pwa.md) |
| **Sprint** | 10 (weeks 19–20) |
| **Owner** | C, Frontend/Patient (backup: D, Frontend/Clinic) |
| **Area** | Frontend / Patient |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 17](../M3/ISSUE_17_patient_identity_otp.md): patient sign-in by SMS code<br>[Issue 21](../M3/ISSUE_21_consent_capture_withdrawal.md): consent capture<br>[Issue 40](../M6/ISSUE_40_join_queue_service_api.md): join-queue service and API<br>[Issue 69](../M9/ISSUE_69_pwa_shell_service_worker.md): the installable app's start page |
| **Unblocks** | Nothing in this milestone. |

## Context

Found while writing `docs/OPS/PATIENT_APP_TESTING.md` (PR #199). The clinic page's **Join the queue**
button (Issue 35) links to `/discover/clinics/{slug}/join`, which Issue 40 was meant to serve. Issue 40
shipped the API only, so with `PATIENT_JOIN_ENABLED=true` the button answers 404. A patient cannot sign in
or join from the web, and the testing guide stands in for the page with a console snippet.

The installed app has the same gap. An iPhone Home Screen app keeps its own storage, apart from Safari's,
so a patient who joined in Safari opens the app at "No open ticket on this phone", with no way to sign in.

## Starting point

- `POST /api/v1/patients/otp/request` and `/otp/verify` (`src/modules/patients/router.py`): verify sets the
  patient session cookie and the readable CSRF cookie that unsafe requests echo in `X-CSRF-Token`.
- `PUT /api/v1/patients/me/consents/notifications`, with the wording in `src/modules/patients/consent_text.py`.
- `POST /api/v1/clinics/{site_id}/queues/{queue_id}/tickets` (`src/modules/queue/router.py`) answers `201`
  for a new ticket and `200` for the one the patient already holds, with `page_url`.
- `src/web/discover.py` builds the clinic page from `clinic_profile`; `/t/` is `src/web/ticket.py`.

## Scope

- `GET /discover/clinics/{slug}/join` on the patient layout: phone number, then the 6-digit code, then the
  notifications question, then the queue (only queues that take remote joins) with an optional reason, then
  the ticket page
- A patient already signed in starts at the queue; joining a queue they already hold opens that ticket
- Every refusal shown in the API's own plain words: a number that cannot be read, the resend cooldown and
  request budget (`429`, counting down), SMS switched off (`503 patients.otp.sms_disabled`), a wrong,
  expired or locked code, a closed or full queue
- The page when joining is not possible: the flag off, the clinic closed, or no queue taking remote joins,
  each with the reason the clinic page gives, and no form
- The installed app's `/t/` start page offers the same sign-in, kept inside the app's scope, and opens the
  patient's open ticket once signed in
- `docs/OPS/PATIENT_APP_TESTING.md` uses the page instead of the console snippet

## Out of scope

- Signing out, and changing the other consent answers (the patient's consent page, Issue 21).
- Booking an appointment for a time (Issues 80 and 81).
- A form that works with JavaScript switched off: the page says so, and the front desk still issues tickets.
- Switching `PATIENT_JOIN_ENABLED` on by default.

## Acceptance criteria

- [ ] With the flag on and the clinic open, a new patient goes from the clinic page to their ticket page by phone number, code, the notifications answer and a queue, in one browser, with no console
- [ ] The notifications answer given on the page is the one recorded, and the question is the wording in `consent_text.py`
- [ ] A signed-in patient opens the page at the queue step; joining a queue they already hold opens that ticket, not a second one
- [ ] Only queues that take remote joins are offered
- [ ] A second code within the cooldown, SMS switched off, an unreadable number and a wrong code each show the API's own sentence, and the resend waits out the cooldown
- [ ] With the flag off, the clinic closed, or no remote queue, the page says why and offers no form
- [ ] The `/t/` start page signs a patient in and opens their open ticket, without leaving `/t/`
- [ ] The page fits a 320 px screen, and every step is reachable by keyboard with its errors announced

## How to verify

1. `TZ=UTC pytest tests/integration/discovery/test_join_page.py tests/e2e/patient/test_join_page.py`
2. Follow `docs/OPS/PATIENT_APP_TESTING.md`, section 3, with `PATIENT_JOIN_ENABLED=true`.

## Files touched

- `src/web/join.py`
- `src/templates/discover/join.html`
- `src/templates/patient/home.html`
- `src/static/js/patient-sign-in.js`
- `src/static/js/patient-join.js`
- `docs/OPS/PATIENT_APP_TESTING.md`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [how to read this spec](../README.md)

Closes #200
