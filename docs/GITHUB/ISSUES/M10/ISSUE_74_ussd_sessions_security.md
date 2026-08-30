# Issue 74: USSD session resume, timeouts and gateway signature verification

**Area:** Backend / Channels
**Milestone:** M10 - USSD & WhatsApp Channels
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issue 73
**Estimate:** 2 days
**Status:** Planned

## Context

USSD sessions drop constantly: poor signal, a timeout, an incoming call. Resuming where the patient
left off is the difference between a usable channel and an abandoned one; verifying the gateway's
signature is what stops anyone from forging a join for someone else's number.

## Scope

- Session resume returning the patient to their last step within the timeout window
- Gateway timeout handling with a clear final message rather than a dead session
- Signature or IP-allowlist verification of gateway webhooks, with replay protection
- Idempotent handling so a retried gateway callback does not double-join
- Structured logging of session steps for debugging, with the MSISDN redacted

## Acceptance criteria

- [ ] A dropped session resumes at the same step within the timeout
- [ ] An unsigned or replayed webhook is rejected
- [ ] A retried callback produces exactly one ticket
- [ ] Session logs are debuggable without exposing full phone numbers
- [ ] A gateway outage produces a clear error rather than a hung session
- [ ] Session state is cleaned up on completion and on timeout

## Files touched

- `channels/ussd/security.py`
- `channels/session.py`
- `tests/integration/test_ussd_sessions.py`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #74
