# Issue 17: Patient identity: phone-first records with OTP verification

> **In short:** A patient is a phone number: they prove it with a 6-digit code and never create a password, on any channel.

| | |
|---|---|
| **Milestone** | [M3: Identity, Auth, RBAC & Consent](../../MILESTONES/M3_identity_auth_rbac.md) |
| **Sprint** | 3 (weeks 5–6) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Auth |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 4](../M1/ISSUE_4_shared_kernel_enums_time_errors.md): Shared kernel: enums, error envelope, IDs, Africa/Johannesburg datetimes<br>[Issue 15](../M3/ISSUE_15_staff_user_security_core.md): Staff user model and security core (JWT, refresh rotation, password hashing) |
| **Unblocks** | [Issue 21](../M3/ISSUE_21_consent_capture_withdrawal.md): Consent capture and withdrawal (display, notifications, board comment)<br>[Issue 39](../M6/ISSUE_39_tickets_model_sequence.md): `tickets` model and concurrency-safe daily sequence numbering<br>[Issue 84](../M11/ISSUE_84_proxy_dependant_booking.md): Proxy booking for dependants with recorded consent<br>[Issue 96](../M13/ISSUE_96_dsar_export_erasure.md): Data-subject access export and erasure workflow<br>[Issue 98](../M13/ISSUE_98_encryption_pii_protection.md): Encryption in transit and at rest, field-level encryption for patient contacts |

## Context

Patients must never be asked to create an account. A phone number plus a one-time PIN is the entire
identity; anything heavier breaks the USSD and WhatsApp channels in M10 and excludes exactly the
population the public-clinic side of the product exists for.

## Starting point

- There is no patient model yet; create a `patients` module (copy the shape of `src/modules/widgets/`).
- The kernel's email OTP (`src/core/otp_store.py`, `POST /auth/otp/request` and `/auth/otp/verify`) and its rate limiter are the pattern to reuse for phone OTP.
- Send the code through the SMS interface in `src/modules/notifications/sms.py`; its logging provider lets this work before an SMS account exists.
- The WORKLOAD_SPLIT gives the consent wording in this issue to F (Data & Research); the model and OTP flow stay with A.

## Scope

- `patients` model: normalised E.164 phone, optional WhatsApp id, display name, consent flags, timestamps
- OTP issue and verify with a short expiry, hashed storage, attempt limits and resend cooldown
- Phone-number normalisation and validation for South African formats, plus international fallback
- Session issuance for a verified patient that works on web, and a channel-trusted path for USSD/WhatsApp
- Duplicate-prevention so one phone number is one patient record

## Out of scope

- Consent capture and withdrawal (Issue 21).
- Booking for a dependant (Issue 84).
- The USSD and WhatsApp menus themselves (Issues 73, 75).

## Acceptance criteria

- [ ] A patient joins a queue with only a phone number and a 6-digit OTP; no password exists
- [ ] OTPs expire within the configured window and are single-use
- [ ] More than N verification attempts locks the code and requires a fresh request
- [ ] `+27821234567`, `0821234567` and `27821234567` resolve to the same patient record
- [ ] OTP values never appear in logs, proven by a test
- [ ] A USSD session is trusted via the gateway MSISDN without a second OTP challenge, and this is documented as a deliberate decision

## How to verify

1. Request a code for `0821234567`, then verify it as `+27821234567`: one patient record, signed in.
2. Enter a wrong code N+1 times: the code locks and a fresh request is needed.
3. Search the logs for the code value: it never appears.

## Files touched

- `src/modules/patients/`
- `src/database/models/patient.py`
- `src/commons/phone.py`
- `alembic/versions/NNNN_patients.py`
- `tests/integration/patients/test_patient_otp.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #17
