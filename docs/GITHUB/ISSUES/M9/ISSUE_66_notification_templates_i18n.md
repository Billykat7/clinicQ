# Issue 66: Multi-language notification templates and admin editor

> **In short:** Every message reads well in the patient's own language, on every channel, and any message sent can be reproduced word for word later.

| | |
|---|---|
| **Milestone** | [M9: Notifications & Patient PWA](../../MILESTONES/M9_notifications_patient_pwa.md) |
| **Sprint** | 4 (weeks 7–8) |
| **Owner** | B, Integrations (backup: A, Backend Lead) |
| **Area** | Backend / Notifications |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 63](../M9/ISSUE_63_notification_service_adapters.md): Notification service, transport adapters and delivery log<br>[Issue 77](../M10/ISSUE_77_i18n_menu_translations.md): i18n framework and menu translations (5 languages) |
| **Unblocks** | [Issue 71](../M9/ISSUE_71_notifications_contract_tests.md): Notification OpenAPI contract, delivery and retry tests<br>[Issue 76](../M10/ISSUE_76_whatsapp_templates_optin.md): WhatsApp templates, opt-in capture and session-window handling |

> **Note:** This issue depends on Issue 77 (translations, sprint 8) but is scheduled in sprint 4. The workable order is: ship the registry, versioning, editor and English templates in sprint 4, and fill in the other four languages when Issue 77 lands. Confirm with F.

## Context

A 'you are next' message that arrives in a language the patient does not read is a message that did
not arrive. Templates are versioned, translated and previewable, so a clinic manager can see exactly what
their patients will receive.

## Starting point

- `src/modules/notifications/templates.py` already maps `(template_key, channel, context)` to a rendered message and re-renders on retry from the stored context. Add language and version to that registry rather than building a new one.

## Scope

- Template registry keyed by event and language, with variable interpolation and a strict variable contract
- Translations for English, isiZulu, isiXhosa, Afrikaans and Sesotho
- Per-channel variants: short for SMS, richer for WhatsApp and push
- Admin editor with a live preview and a character counter for SMS
- Template versioning so a sent message can be reproduced exactly later

## Out of scope

- The translation framework itself (Issue 77).
- WhatsApp template approval (Issue 76).

## Acceptance criteria

- [ ] Every event has a template in all five languages
- [ ] SMS variants fit in one segment in every language
- [ ] A missing variable fails at template-registration time, not at send time
- [ ] The preview shows exactly what the patient will receive, per channel
- [ ] The version used for a send is recorded in the notification log
- [ ] Translations have been checked by a fluent speaker and the reviewer is credited

## How to verify

1. Register a template missing a variable: registration fails, not the send.
2. Preview each event per channel in the editor: exactly what the patient receives.
3. Send a message, edit the template, then look the send up: the ledger names the version that was used.

## Files touched

- `src/modules/notifications/templates.py`, `src/modules/notifications/template_registry.py`
- `src/modules/notifications/template_router.py`
- `src/locales/` (`en/notifications.toml`, `notifications.lock.json`)
- `alembic/versions/0035_notification_template_versions.py`
- `src/templates/admin/template_editor.html`, `src/templates/admin/notification_templates.html`

---

**Refs:** [M9 milestone](../../MILESTONES/M9_notifications_patient_pwa.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #66
