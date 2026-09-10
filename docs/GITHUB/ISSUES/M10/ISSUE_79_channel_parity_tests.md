# Issue 79: Cross-channel parity tests and channel-mix analytics

> **In short:** Proof that no door into the queue is better than another: the same scenario through web, USSD, WhatsApp and reception gives the same result.

| | |
|---|---|
| **Milestone** | [M10: USSD & WhatsApp Channels](../../MILESTONES/M10_ussd_whatsapp_channels.md) |
| **Sprint** | 11 (weeks 21–22) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Quality |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 72](../M10/ISSUE_72_channel_adapter_framework.md): Channel adapter framework with Redis session state<br>[Issue 73](../M10/ISSUE_73_ussd_menu_tree.md): USSD webhook and menu tree (find, join, status, cancel)<br>[Issue 74](../M10/ISSUE_74_ussd_sessions_security.md): USSD session resume, timeouts and gateway signature verification<br>[Issue 75](../M10/ISSUE_75_whatsapp_webhook_quick_replies.md): WhatsApp Cloud API webhook with quick-reply flows<br>[Issue 76](../M10/ISSUE_76_whatsapp_templates_optin.md): WhatsApp templates, opt-in capture and session-window handling<br>[Issue 77](../M10/ISSUE_77_i18n_menu_translations.md): i18n framework and menu translations (5 languages)<br>[Issue 78](../M10/ISSUE_78_channel_simulators.md): Channel simulators for credential-free local development |
| **Unblocks** | No other issue waits on this one. |

## Context

The project's central architectural claim is 'one engine, four doors'. This suite is what makes that
claim testable: the same patient, joining the same queue through four different channels, must end up in
exactly the same state.

## Starting point

- Uses the simulators (Issue 78) and the join service (Issue 40).
- The ticket's `source` field (Issue 39) already records the channel; the analytics are a report over it.

## Scope

- Parity suite running the same scenarios through web, USSD, WhatsApp and reception walk-in
- Assertions on resulting ticket state, sequence position, notifications and audit rows
- Channel-mix analytics recording the source of every ticket
- A channel-mix report for the M12 dashboard
- Documented differences where a channel genuinely cannot match, with the reason

## Out of scope

- The reports UI (Issue 89), which shows channel mix.

## Acceptance criteria

- [ ] All four channels produce identical ticket state for the same scenario
- [ ] Sequence fairness holds across channels, no channel systematically gets a better position
- [ ] Channel mix is reportable per site and per day
- [ ] Every deliberate channel difference is documented and justified
- [ ] The suite runs in CI within the time budget
- [ ] A regression that breaks parity fails the build

## How to verify

1. Run the parity suite: identical ticket state, sequence position, notifications and audit rows across all four channels.
2. Change one adapter to skip the audit write: the suite fails.
3. `contracts/channels.yaml` lists every deliberate channel difference and why.

## Files touched

- `tests/integration/channels/test_channel_parity.py`
- `src/modules/discovery/analytics.py`
- `contracts/channels.yaml`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #79
