# Issue 75: WhatsApp Cloud API webhook with quick-reply flows

> **In short:** Patients can find a clinic and join its queue inside WhatsApp, using buttons only, the app most of them already have open.

| | |
|---|---|
| **Milestone** | [M10: USSD & WhatsApp Channels](../../MILESTONES/M10_ussd_whatsapp_channels.md) |
| **Sprint** | 10 (weeks 19–20) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Channels |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 72](../M10/ISSUE_72_channel_adapter_framework.md): Channel adapter framework with Redis session state |
| **Unblocks** | [Issue 76](../M10/ISSUE_76_whatsapp_templates_optin.md): WhatsApp templates, opt-in capture and session-window handling<br>[Issue 78](../M10/ISSUE_78_channel_simulators.md): Channel simulators for credential-free local development<br>[Issue 79](../M10/ISSUE_79_channel_parity_tests.md): Cross-channel parity tests and channel-mix analytics |

## Context

WhatsApp is where most patients already are, so this is the lowest-friction door in the product. The
flow is button-driven rather than free-text on purpose: buttons remove spelling, language and parsing
problems in one move.

## Starting point

- Meta's webhook verification handshake and signed deliveries follow the same pattern as the kernel's payment webhooks.
- A shared WhatsApp location goes straight into the nearby search (Issue 31).

## Scope

- WhatsApp Business Cloud API webhook: verification handshake, inbound messages, delivery statuses
- Button and list-message flows for find, select, join, status and cancel
- Location-message support so a patient can share their position for a nearby search
- Media-free, text-and-button interaction, no free-text parsing on the critical path
- Graceful handling of an unexpected free-text message, steering back to the menu

## Out of scope

- Outbound templates and the 24-hour session window (Issue 76).

## Acceptance criteria

- [ ] A patient completes find → join → status → cancel using buttons only
- [ ] Sharing a WhatsApp location performs a nearby search
- [ ] Unexpected free text produces a helpful menu rather than an error
- [ ] The webhook verification handshake succeeds against the Meta sandbox
- [ ] Delivery statuses update the notification log
- [ ] The flow goes through the same services as the web, proven by a parity test

## How to verify

1. Complete find, join, status and cancel with `scripts/simulate_whatsapp.py`, pressing buttons only.
2. Send "hello?" mid-flow: a helpful menu, not an error.
3. Complete the verification handshake against the Meta sandbox.

## Files touched

- `src/modules/channels/whatsapp/handler.py`
- `src/modules/channels/whatsapp/flows.py`
- `tests/integration/channels/test_whatsapp_flows.py`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #75
