# Issue 17: Patient identity: phone-first records with OTP verification

**Area:** Backend / Auth
**Milestone:** M3 - Identity, Auth, RBAC & Consent
**Owner role:** Backend Lead
**Depends on:** Issues 4, 15
**Estimate:** 3 days
**Status:** Planned

## Context

Patients must never be asked to create an account. A phone number plus a one-time PIN is the entire
identity; anything heavier breaks the USSD and WhatsApp channels in M10 and excludes exactly the
population the public-clinic side of the product exists for.

## Scope

- `patients` model: normalised E.164 phone, optional WhatsApp id, display name, consent flags, timestamps
- OTP issue and verify with a short expiry, hashed storage, attempt limits and resend cooldown
- Phone-number normalisation and validation for South African formats, plus international fallback
- Session issuance for a verified patient that works on web, and a channel-trusted path for USSD/WhatsApp
- Duplicate-prevention so one phone number is one patient record

## Acceptance criteria

- [ ] A patient joins a queue with only a phone number and a 6-digit OTP; no password exists
- [ ] OTPs expire within the configured window and are single-use
- [ ] More than N verification attempts locks the code and requires a fresh request
- [ ] `+27821234567`, `0821234567` and `27821234567` resolve to the same patient record
- [ ] OTP values never appear in logs, proven by a test
- [ ] A USSD session is trusted via the gateway MSISDN without a second OTP challenge, and this is documented as a deliberate decision

## Files touched

- `app/database/models/patient.py`
- `app/services/patient_identity.py`
- `app/core/phone.py`
- `tests/integration/test_patient_otp.py`

---

**Refs:** [M3 milestone](../../MILESTONES/M3_identity_auth_rbac.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #17
