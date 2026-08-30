# Issue 75: WhatsApp Cloud API webhook with quick-reply flows

**Area:** Backend / Channels
**Milestone:** M10 - USSD & WhatsApp Channels
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issue 72
**Estimate:** 3 days
**Status:** Planned

## Context

WhatsApp is where most patients already are, so this is the lowest-friction door in the product. The
flow is button-driven rather than free-text on purpose: buttons remove spelling, language and parsing
problems in one move.

## Scope

- WhatsApp Business Cloud API webhook: verification handshake, inbound messages, delivery statuses
- Button and list-message flows for find, select, join, status and cancel
- Location-message support so a patient can share their position for a nearby search
- Media-free, text-and-button interaction, no free-text parsing on the critical path
- Graceful handling of an unexpected free-text message, steering back to the menu

## Acceptance criteria

- [ ] A patient completes find → join → status → cancel using buttons only
- [ ] Sharing a WhatsApp location performs a nearby search
- [ ] Unexpected free text produces a helpful menu rather than an error
- [ ] The webhook verification handshake succeeds against the Meta sandbox
- [ ] Delivery statuses update the notification log
- [ ] The flow goes through the same services as the web, proven by a parity test

## Files touched

- `channels/whatsapp/handler.py`
- `channels/whatsapp/flows.py`
- `tests/integration/test_whatsapp_flows.py`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #75
