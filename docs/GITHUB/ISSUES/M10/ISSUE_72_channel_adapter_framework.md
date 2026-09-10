# Issue 72: Channel adapter framework with Redis session state

> **In short:** The thin layer that lets USSD and WhatsApp reuse the exact same find, join, status and cancel logic as the web, holding only short-lived session state.

| | |
|---|---|
| **Milestone** | [M10: USSD & WhatsApp Channels](../../MILESTONES/M10_ussd_whatsapp_channels.md) |
| **Sprint** | 7 (weeks 13–14) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Channels |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 31](../M5/ISSUE_31_clinics_nearby_postgis_search.md): `GET /api/clinics/nearby`: PostGIS radius search with distance and ETA<br>[Issue 40](../M6/ISSUE_40_join_queue_service_api.md): Join-queue service and API (all channels), with abuse guards |
| **Unblocks** | [Issue 73](../M10/ISSUE_73_ussd_menu_tree.md): USSD webhook and menu tree (find, join, status, cancel)<br>[Issue 75](../M10/ISSUE_75_whatsapp_webhook_quick_replies.md): WhatsApp Cloud API webhook with quick-reply flows<br>[Issue 77](../M10/ISSUE_77_i18n_menu_translations.md): i18n framework and menu translations (5 languages)<br>[Issue 79](../M10/ISSUE_79_channel_parity_tests.md): Cross-channel parity tests and channel-mix analytics |

## Context

The rule that keeps four channels affordable: **thin adapters, shared services**. An adapter may
translate a keypress into a service call and format the reply, and nothing else. Any adapter that starts
holding business logic is a bug, and the parity suite in Issue 79 is what proves it.

## Starting point

- New module: `src/modules/channels/`. Adapters call the discovery (Issue 31) and queue (Issue 40) services and nothing else.
- Redis is already a dependency; session keys must expire (a TTL) and hold only the current step.
- B designs this in sprints 1–2 during provider research, before there is code to call.

## Scope

- Adapter interface: parse inbound → resolve intent → call service → format reply
- Redis-backed session state with a TTL, keyed by MSISDN or WhatsApp id
- Shared intent router mapping find-clinic, join, status, cancel and help across channels
- Reply formatter with per-channel constraints (USSD character limits, WhatsApp button counts)
- A guard test asserting adapters contain no database queries or business rules

## Out of scope

- The USSD menus (Issue 73) and WhatsApp flows (Issue 75).
- Translations (Issue 77).

## Acceptance criteria

- [ ] An adapter can be written in under 200 lines because all logic lives in services
- [ ] Session state expires correctly and never leaks between patients
- [ ] The intent router is shared, with no channel-specific branching inside services
- [ ] The guard test fails if an adapter imports a model or writes to the database
- [ ] Adding a new channel requires no change to the queue or discovery services
- [ ] Session data holds no personal information beyond what the step needs

## How to verify

1. Import a model inside an adapter: the purity guard test fails.
2. Let a session sit past its TTL: the next input starts fresh, and no other patient's state is visible.
3. Count the lines of the first real adapter: under 200.

## Files touched

- `src/modules/channels/base.py`
- `src/modules/channels/session.py`
- `src/modules/channels/intents.py`
- `tests/unit/channels/test_adapter_purity.py`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #72
