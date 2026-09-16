# Issue 219: Patient sign-in by email address, behind a feature flag

> **In short:** A patient signs in to `/t/` and the join page with an email address and a code, exactly as they do with a phone number today, when `PATIENT_EMAIL_SIGN_IN_ENABLED` is on.

| | |
|---|---|
| **Milestone** | [M15: Clinic Onboarding & Patient Sign-in](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) |
| **Sprint** | 15 (weeks 29–30) |
| **Owner** | A, Backend Lead (backup: C, Frontend/Patient) |
| **Area** | Backend / Patient identity |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 17](../M3/ISSUE_17_patient_identity_otp.md): patient identity and the OTP store<br>[Issue 200](../M9/ISSUE_200_patient_join_page.md): the sign-in the join page and `/t/` render |
| **Unblocks** | [Issue 220](ISSUE_220_patient_email_transport.md): email as a patient notification transport |

## Context

A patient's identity is a mobile number and nothing else (Issue 17). `/t/` opens at **"No open ticket
on this phone"** with one way in: *Sign in with your phone number*. That is right for the walk-in
population the product is built for, and wrong for three cases we hit as soon as we test:

- a patient whose number changed, whose ticket history is stranded on the old one;
- anyone testing or demoing the product without burning SMS credit or a real handset — today the
  only way in is a real number and a real SMS (`SMS_ENABLED`), so a demo needs a phone;
- a patient who has an email address and no airtime, for whom an emailed code costs the platform
  nothing where an SMS costs money per message.

`OtpSubjectKind` already has an `EMAIL` member and `src/core/otp_store.py` is already keyed by
`(kind, identifier)` — the store was built for this. `src/core/email_send.py` already sends a
one-time code (`send_otp_email`), for staff. What is missing is the column, the routes that accept
an address, and the page that offers it.

Off by default. A deployment switches it on deliberately, the way `PATIENT_JOIN_ENABLED` is.

## Starting point

- `src/database/models/patient.py`: `phone_e164` unique and nullable (nullable since Issue 84's
  dependants), `phone_verified_at`, `session_version`. **No password column, and the test
  `tests/integration/patients/test_patient_otp.py` checks the table for one** — that invariant stays.
- `src/modules/patients/service.py`: `send_code(raw_phone, ip)` and `verify_code(raw_phone, code)`;
  `get_or_create_patient` handles the two-sign-ins-race with a savepoint on the unique constraint.
- `src/modules/patients/router.py`: `POST /api/v1/patients/otp/request` (202) and `/otp/verify` (200,
  sets the session and CSRF cookies).
- `src/web/join.py`: `SignInView` / `sign_in_view()`, rendered by `patient/_sign_in.html` and driven
  by `src/static/js/patient-sign-in.js`.
- `src/core/email_send.py:send_otp_email`, and `src/core/config.py` for the flag's neighbours.

## Scope

- Migration `0047`: `patient.email` (`String(255)`, nullable, **unique**, lower-cased on write) and
  `patient.email_verified_at`. No backfill: every existing patient keeps `NULL`.
- `PATIENT_EMAIL_SIGN_IN_ENABLED` in `Settings`, default `false`, documented in `.env.example`.
- `POST /patients/otp/request` and `/otp/verify` accept **either** `phone` or `email`, exactly one of
  the two. An `email` body while the flag is off is refused `503 patients.otp.email_disabled`, in the
  same shape `patients.otp.sms_disabled` already uses.
- The email path is the phone path, subject for subject: the same OTP store, the same resend cooldown
  and per-identifier and per-IP budgets, the same `OtpVerification` outcomes and their sentences, the
  same "no patient is created until a code is verified", the same session and CSRF cookies.
- `get_or_create_patient` gains an email-keyed twin with the same savepoint race handling. A patient
  created by email has `email` set, `phone_e164` `NULL` and `email_verified_at` stamped — and can do
  everything a phone patient can: join a queue, hold a ticket, open `/t/`, answer consent.
- `_sign_in.html` offers both ways when the flag is on, phone first and selected, as two tabs; with
  the flag off the page is byte-for-byte what it is today (no tabs, no email field).
- `PatientOut` gains a masked `email` beside the masked `phone`; `mask_email` beside `mask_phone` in
  `src/commons/phone.py`'s neighbourhood, with the local part reduced (`b••••y@gmail.com`).
- The audit trail never carries the address, exactly as it never carries the number.

## Out of scope

- Delivering a patient's queue notifications by email — an email-only patient is unreachable by the
  transport chain until [Issue 220](ISSUE_220_patient_email_transport.md), which is why 220 follows
  this immediately and why the flag stays off by default until it lands.
- Merging a patient who signed in by email with the phone patient who is the same person. Two
  identifiers, two records, until someone specifies the merge.
- Adding or changing an email on an existing patient record from `/me` (a separate change, and it
  needs the verification dance staff email changes already have).
- USSD and WhatsApp: those channels are numbers by definition.
- Switching the flag on by default.

## Acceptance criteria

- [ ] With `PATIENT_EMAIL_SIGN_IN_ENABLED=false`, `/t/` and the join page render exactly what they
      render today, and an `email` body is refused `503 patients.otp.email_disabled`
- [ ] With the flag on, a new address goes from `/t/` to a signed-in session with a code by email, in
      one browser, with no console — and `/t/` then opens that patient's open ticket
- [ ] A patient created by email joins a queue, holds a ticket and opens `/t/` with no phone number on
      the record
- [ ] A request naming both `phone` and `email`, or neither, is `422`
- [ ] The resend cooldown, the per-identifier and per-IP budgets, and the wrong/expired/locked
      outcomes behave for an address exactly as they do for a number, with the same sentences
- [ ] Two simultaneous first sign-ins at one address create exactly one patient
- [ ] `patient` still has no password column, and no audit row or log line contains an address
- [ ] The tabs are reachable by keyboard, announce their errors, and fit a 320 px screen

## How to verify

1. `TZ=UTC pytest tests/unit/patients tests/integration/patients tests/e2e/patient`
2. `make check`
3. With `PATIENT_EMAIL_SIGN_IN_ENABLED=true` and the dev mail outbox, sign in at `/t/` by address and
   join a queue; then with the flag off, confirm the page has no email field.

## Files touched

- `alembic/versions/0047_patient_email_sign_in.py`
- `src/database/models/patient.py`
- `src/core/config.py`, `.env.example`
- `src/commons/phone.py` (or a new `src/commons/email.py` for `mask_email`)
- `src/modules/patients/{service,router,schemas}.py`
- `src/web/join.py`, `src/web/ticket.py`
- `src/templates/patient/_sign_in.html`, `src/static/js/patient-sign-in.js`
- `src/core/email_send.py`

---

**Refs:** [M15 milestone](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) · [how to read this spec](../README.md)

Closes #219
