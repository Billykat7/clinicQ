# Issue 74: USSD session resume, timeouts and gateway signature verification

> **In short:** USSD survives the real world: dropped sessions pick up where they left off, forged or replayed gateway calls are refused, and a retry never books twice.

| | |
|---|---|
| **Milestone** | [M10: USSD & WhatsApp Channels](../../MILESTONES/M10_ussd_whatsapp_channels.md) |
| **Sprint** | 9 (weeks 17–18) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Channels |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 73](../M10/ISSUE_73_ussd_menu_tree.md): USSD webhook and menu tree (find, join, status, cancel) |
| **Unblocks** | [Issue 79](../M10/ISSUE_79_channel_parity_tests.md): Cross-channel parity tests and channel-mix analytics |

## Context

USSD sessions drop constantly: poor signal, a timeout, an incoming call. Resuming where the patient
left off is the difference between a usable channel and an abandoned one; verifying the gateway's
signature is what stops anyone from forging a join for someone else's number.

## Starting point

- The kernel's payment webhooks already show signature verification on the raw body and idempotent event rows (`src/core/webhook_gateways/`); use the same approach for the USSD gateway.
- Redact the phone number in logs with the redaction from Issue 6.

## Scope

- Session resume returning the patient to their last step within the timeout window
- Gateway timeout handling with a clear final message rather than a dead session
- Signature or IP-allowlist verification of gateway webhooks, with replay protection
- Idempotent handling so a retried gateway callback does not double-join
- Structured logging of session steps for debugging, with the MSISDN redacted

## Out of scope

- The menu content (Issue 73).

## Acceptance criteria

- [ ] A dropped session resumes at the same step within the timeout
- [ ] An unsigned or replayed webhook is rejected
- [ ] A retried callback produces exactly one ticket
- [ ] Session logs are debuggable without exposing full phone numbers
- [ ] A gateway outage produces a clear error rather than a hung session
- [ ] Session state is cleaned up on completion and on timeout

## How to verify

1. Drop a simulated session mid-flow and redial: it resumes at the same step.
2. Send an unsigned callback, then replay a signed one: both refused.
3. Retry the same join callback: exactly one ticket.

## Files touched

- `src/modules/channels/ussd/security.py`
- `src/modules/channels/session.py`
- `tests/integration/channels/test_ussd_sessions.py`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #74
