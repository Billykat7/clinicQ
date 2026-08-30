# Issue 66: Multi-language notification templates and admin editor

**Area:** Backend / Notifications
**Milestone:** M9 - Notifications & Patient PWA
**Owner role:** Backend (Integrations) Dev
**Depends on:** Issues 63, 77
**Estimate:** 2 days
**Status:** Planned

## Context

A 'you are next' message that arrives in a language the patient does not read is a message that did
not arrive. Templates are versioned, translated and previewable, so a clinic manager can see exactly what
their patients will receive.

## Scope

- Template registry keyed by event and language, with variable interpolation and a strict variable contract
- Translations for English, isiZulu, isiXhosa, Afrikaans and Sesotho
- Per-channel variants: short for SMS, richer for WhatsApp and push
- Admin editor with a live preview and a character counter for SMS
- Template versioning so a sent message can be reproduced exactly later

## Acceptance criteria

- [ ] Every event has a template in all five languages
- [ ] SMS variants fit in one segment in every language
- [ ] A missing variable fails at template-registration time, not at send time
- [ ] The preview shows exactly what the patient will receive, per channel
- [ ] The version used for a send is recorded in the notification log
- [ ] Translations have been checked by a fluent speaker and the reviewer is credited

## Files touched

- `app/services/templates.py`
- `app/locales/*/messages.po`
- `app/templates/admin/template_editor.html`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #66
