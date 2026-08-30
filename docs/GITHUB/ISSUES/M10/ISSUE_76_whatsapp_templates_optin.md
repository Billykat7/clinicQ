# Issue 76: WhatsApp templates, opt-in capture and session-window handling

**Area:** Backend / Channels
**Milestone:** M10 - USSD & WhatsApp Channels
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issues 75, 66
**Estimate:** 2 days
**Status:** Planned

## Context

WhatsApp only allows a business to message outside a 24-hour window using a pre-approved template,
with recorded opt-in. Getting this wrong means 'you are next' silently fails for the channel most
patients use, so the fallback to SMS is part of the design.

## Scope

- Template registration and approval tracking for each notification event
- Opt-in capture and storage, satisfying the platform's requirements
- 24-hour session-window tracking, choosing a session message or a template accordingly
- Automatic fallback to SMS when no template is approved or opt-in is missing
- Template variables aligned with the M9 template registry so wording stays consistent

## Acceptance criteria

- [ ] An outbound message outside the window uses an approved template
- [ ] Missing opt-in falls back to SMS rather than failing
- [ ] Session-window state is tracked accurately per patient
- [ ] Template approval status is visible to the team
- [ ] Wording matches the SMS and push versions of the same event
- [ ] A rejected template is detected and raises an alert rather than failing at send time

## Files touched

- `channels/whatsapp/templates.py`
- `app/services/notification_prefs.py`
- `docs/OPS/WHATSAPP_TEMPLATES.md`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #76
