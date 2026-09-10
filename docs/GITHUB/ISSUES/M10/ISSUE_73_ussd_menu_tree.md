# Issue 73: USSD webhook and menu tree (find, join, status, cancel)

> **In short:** Someone with a feature phone and no data dials a short code and leaves with a ticket number.

| | |
|---|---|
| **Milestone** | [M10: USSD & WhatsApp Channels](../../MILESTONES/M10_ussd_whatsapp_channels.md) |
| **Sprint** | 8 (weeks 15–16) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Channels |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 72](../M10/ISSUE_72_channel_adapter_framework.md): Channel adapter framework with Redis session state |
| **Unblocks** | [Issue 74](../M10/ISSUE_74_ussd_sessions_security.md): USSD session resume, timeouts and gateway signature verification<br>[Issue 78](../M10/ISSUE_78_channel_simulators.md): Channel simulators for credential-free local development<br>[Issue 79](../M10/ISSUE_79_channel_parity_tests.md): Cross-channel parity tests and channel-mix analytics |

## Context

The most inclusive channel in the product and the one no global queue vendor offers: any phone, no
data, no app. The design constraint is brutal: roughly 160 characters per screen and a single digit of
input, which forces genuine clarity about what each step is for.

## Starting point

- Inbound gateway calls are webhooks: follow `src/api/v1/routes/webhooks.py` for the route shape.
- Area search (Issue 34) is the GPS-free way in.
- Use the simulator (Issue 78) instead of a live gateway while building.

## Scope

- USSD webhook endpoint handling the gateway's session protocol
- Menu tree: find clinic (GPS-free, by area) → select → select queue → confirm join → ticket number
- Status check, cancel and language selection from the root menu
- Screens within the character limit, with paging for long clinic lists
- Confirmation screen stating clinic, queue and number before committing

## Out of scope

- Session resume, timeouts and signature checks (Issue 74).
- Translations (Issue 77).

## Acceptance criteria

- [ ] A feature phone completes find → join → receive a number entirely over USSD
- [ ] Every screen fits the character limit in all five languages
- [ ] Long result lists page correctly with 'next' and 'back'
- [ ] Status and cancel work from the root menu in under four keypresses
- [ ] The join goes through the same service as the web, proven by a parity test
- [ ] The menu tree is documented as a diagram in the repository

## How to verify

1. Drive find, join and ticket number end to end with `scripts/simulate_ussd.py`.
2. Render every screen in all five languages: each fits the character limit.
3. The parity test shows the USSD join produced the same ticket state as a web join.

## Files touched

- `src/modules/channels/ussd/handler.py`
- `src/modules/channels/ussd/menus.py`
- `docs/PRODUCT/06-channels-app-ussd-whatsapp-web.md`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #73
