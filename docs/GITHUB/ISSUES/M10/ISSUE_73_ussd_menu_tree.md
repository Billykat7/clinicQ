# Issue 73: USSD webhook and menu tree (find, join, status, cancel)

**Area:** Backend / Channels
**Milestone:** M10 - USSD & WhatsApp Channels
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issue 72
**Estimate:** 3 days
**Status:** Planned

## Context

The most inclusive channel in the product and the one no global queue vendor offers: any phone, no
data, no app. The design constraint is brutal: roughly 160 characters per screen and a single digit of
input, which forces genuine clarity about what each step is for.

## Scope

- USSD webhook endpoint handling the gateway's session protocol
- Menu tree: find clinic (GPS-free, by area) → select → select queue → confirm join → ticket number
- Status check, cancel and language selection from the root menu
- Screens within the character limit, with paging for long clinic lists
- Confirmation screen stating clinic, queue and number before committing

## Acceptance criteria

- [ ] A feature phone completes find → join → receive a number entirely over USSD
- [ ] Every screen fits the character limit in all five languages
- [ ] Long result lists page correctly with 'next' and 'back'
- [ ] Status and cancel work from the root menu in under four keypresses
- [ ] The join goes through the same service as the web, proven by a parity test
- [ ] The menu tree is documented as a diagram in the repository

## Files touched

- `channels/ussd/handler.py`
- `channels/ussd/menus.py`
- `docs/PRODUCT/06-channels-app-ussd-whatsapp-web.md`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #73
