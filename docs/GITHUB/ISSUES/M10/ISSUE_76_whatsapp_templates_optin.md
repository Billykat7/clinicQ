# Issue 76: WhatsApp templates, opt-in capture and session-window handling

> **In short:** Outbound WhatsApp messages follow Meta's rules (approved templates, opt-in, the 24-hour window) and fall back to SMS instead of failing.

| | |
|---|---|
| **Milestone** | [M10: USSD & WhatsApp Channels](../../MILESTONES/M10_ussd_whatsapp_channels.md) |
| **Sprint** | 10 (weeks 19–20) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Channels |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 66](../M9/ISSUE_66_notification_templates_i18n.md): Multi-language notification templates and admin editor<br>[Issue 75](../M10/ISSUE_75_whatsapp_webhook_quick_replies.md): WhatsApp Cloud API webhook with quick-reply flows |
| **Unblocks** | [Issue 79](../M10/ISSUE_79_channel_parity_tests.md): Cross-channel parity tests and channel-mix analytics |

## Context

WhatsApp only allows a business to message outside a 24-hour window using a pre-approved template,
with recorded opt-in. Getting this wrong means 'you are next' silently fails for the channel most
patients use, so the fallback to SMS is part of the design.

## Starting point

- Wording comes from the same template registry as SMS and push (Issue 66), so an event reads the same everywhere.

## Scope

- Template registration and approval tracking for each notification event
- Opt-in capture and storage, satisfying the platform's requirements
- 24-hour session-window tracking, choosing a session message or a template accordingly
- Automatic fallback to SMS when no template is approved or opt-in is missing
- Template variables aligned with the M9 template registry so wording stays consistent

## Out of scope

- Inbound flows (Issue 75).
- SMS delivery (Issue 65), which this falls back to.

## Acceptance criteria

- [ ] An outbound message outside the window uses an approved template
- [ ] Missing opt-in falls back to SMS rather than failing
- [ ] Session-window state is tracked accurately per patient
- [ ] Template approval status is visible to the team
- [ ] Wording matches the SMS and push versions of the same event
- [ ] A rejected template is detected and raises an alert rather than failing at send time

## How to verify

1. Message a patient 25 hours after their last reply: an approved template is used.
2. Message a patient with no opt-in: SMS is sent instead.
3. Mark a template as rejected: an alert fires before any send attempt.

## Files touched

- `src/modules/channels/whatsapp/templates.py`
- `src/modules/notifications/preferences.py`
- `docs/OPS/WHATSAPP_TEMPLATES.md`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #76
