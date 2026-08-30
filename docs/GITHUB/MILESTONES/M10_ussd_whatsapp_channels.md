# Milestone 10: USSD & WhatsApp Channels

**Status:** 📋 planned · **Phase:** Semester 2 · Sprint 10–11 · **Suggested tag:** `v0.10.0`
**Primary owner:** Backend (Integrations) Dev
**Depends on:** M5, M6, M9
**Blocks:** None

## Goal

Open the two doors that reach the patients a smartphone app never will: a USSD menu that works on any phone with no data, and a WhatsApp bot in the app most people already have open, both calling the same discovery and queue services as the web.

## Why this milestone exists

This is the feature that separates ClinicQ from every global queue vendor. Qminder, Qless, Qmatic and
the NHS App all assume a smartphone with data. In this market that assumption excludes a large share
of exactly the public-clinic population the product is for.

The engineering rule that keeps it affordable is **thin adapters, shared services**: a channel adapter
may translate a menu keypress into a service call and format the reply, and nothing else. If a channel
needs business logic, that logic belongs in the service layer where all four channels get it. The
parity test suite exists to enforce that rule mechanically.

## Scope

- Channel adapter framework with Redis-backed session state and a shared intent router
- USSD webhook: menu tree for find clinic → select → join → check status → cancel
- USSD session resume, timeout handling and gateway signature verification
- WhatsApp Business Cloud API webhook with quick-reply buttons and list messages
- WhatsApp template registration, opt-in capture and 24-hour session-window handling
- i18n framework and menu translations: English, isiZulu, isiXhosa, Afrikaans, Sesotho
- Offline simulators (`simulate_ussd.py`, `simulate_whatsapp.py`) so development needs no gateway credentials
- Cross-channel parity tests and channel-mix analytics

## Issues

| # | Title |
|---|-------|
| 72 | Channel adapter framework with Redis session state |
| 73 | USSD webhook and menu tree (find, join, status, cancel) |
| 74 | USSD session resume, timeouts and gateway signature verification |
| 75 | WhatsApp Cloud API webhook with quick-reply flows |
| 76 | WhatsApp templates, opt-in capture and session-window handling |
| 77 | i18n framework and menu translations (5 languages) |
| 78 | Channel simulators for credential-free local development |
| 79 | Cross-channel parity tests and channel-mix analytics |

## Exit criteria

- [ ] A feature phone can find a clinic and hold a ticket number entirely over USSD, with no data
- [ ] A WhatsApp user can join, check position and cancel using buttons only, never free text
- [ ] A dropped USSD session resumes at the same step within the session timeout
- [ ] Unsigned or replayed gateway webhooks are rejected
- [ ] The parity suite proves all four channels produce identical ticket state for the same input
- [ ] The whole channel layer can be developed and tested locally with no gateway account

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M10/)
