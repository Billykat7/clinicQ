# Issue 78: Channel simulators for credential-free local development

> **In short:** Anyone on the team can run a full USSD or WhatsApp conversation on their laptop, with no gateway account and no chance of sending a real message.

| | |
|---|---|
| **Milestone** | [M10: USSD & WhatsApp Channels](../../MILESTONES/M10_ussd_whatsapp_channels.md) |
| **Sprint** | 7 (weeks 13–14) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Tooling |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 73](../M10/ISSUE_73_ussd_menu_tree.md): USSD webhook and menu tree (find, join, status, cancel)<br>[Issue 75](../M10/ISSUE_75_whatsapp_webhook_quick_replies.md): WhatsApp Cloud API webhook with quick-reply flows |
| **Unblocks** | [Issue 79](../M10/ISSUE_79_channel_parity_tests.md): Cross-channel parity tests and channel-mix analytics |

## Context

Gateway credentials are slow to obtain, cost money, and cannot be shared across six laptops. The
simulators mean the entire channel layer can be built and tested before a single provider account
exists, and they keep working as regression tools afterwards.

## Starting point

- The kernel's `LoggingSmsProvider` is the model: a mode where the app runs end to end with outbound calls replaced.

## Scope

- `scripts/simulate_ussd.py`: an interactive terminal USSD session against the local webhook
- `scripts/simulate_whatsapp.py`: send inbound message and button payloads to the local webhook
- Recorded fixtures of real gateway payload shapes for realistic tests
- A simulator mode flag so no outbound provider call is ever made locally
- Documentation showing a new team member a full join flow in under 5 minutes

## Out of scope

- Live gateway testing on staging (handled with the providers' sandboxes).

## Acceptance criteria

- [ ] A full USSD join can be driven from a terminal with no gateway account
- [ ] The WhatsApp simulator reproduces button and list interactions faithfully
- [ ] Fixtures match documented provider payload shapes
- [ ] Simulator mode makes outbound provider calls impossible
- [ ] A teammate unfamiliar with the channel code completes a simulated join in under 5 minutes
- [ ] The simulators are used by the automated test suite, not only by hand

## How to verify

1. A teammate who has never touched the channel code completes a simulated join in under 5 minutes, and says so in the PR.
2. With simulator mode on, force an outbound provider call: it is refused.
3. The automated suite uses the recorded fixtures.

## Files touched

- `scripts/simulate_ussd.py`
- `scripts/simulate_whatsapp.py`
- `tests/fixtures/gateway_payloads/`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #78
