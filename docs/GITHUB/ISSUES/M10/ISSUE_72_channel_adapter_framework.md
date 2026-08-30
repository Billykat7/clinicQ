# Issue 72: Channel adapter framework with Redis session state

**Area:** Backend / Channels
**Milestone:** M10 - USSD & WhatsApp Channels
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issues 31, 40
**Estimate:** 3 days
**Status:** Planned

## Context

The rule that keeps four channels affordable: **thin adapters, shared services**. An adapter may
translate a keypress into a service call and format the reply, and nothing else. Any adapter that starts
holding business logic is a bug, and the parity suite in Issue 79 is what proves it.

## Scope

- Adapter interface: parse inbound → resolve intent → call service → format reply
- Redis-backed session state with a TTL, keyed by MSISDN or WhatsApp id
- Shared intent router mapping find-clinic, join, status, cancel and help across channels
- Reply formatter with per-channel constraints (USSD character limits, WhatsApp button counts)
- A guard test asserting adapters contain no database queries or business rules

## Acceptance criteria

- [ ] An adapter can be written in under 200 lines because all logic lives in services
- [ ] Session state expires correctly and never leaks between patients
- [ ] The intent router is shared, with no channel-specific branching inside services
- [ ] The guard test fails if an adapter imports a model or writes to the database
- [ ] Adding a new channel requires no change to the queue or discovery services
- [ ] Session data holds no personal information beyond what the step needs

## Files touched

- `channels/base.py`
- `channels/session.py`
- `channels/intents.py`
- `tests/unit/test_adapter_purity.py`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #72
