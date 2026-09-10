# Issue 77: i18n framework and menu translations (5 languages)

> **In short:** Every screen, menu, message and announcement works in English, isiZulu, isiXhosa, Afrikaans and Sesotho, and a missing translation fails the build.

| | |
|---|---|
| **Milestone** | [M10: USSD & WhatsApp Channels](../../MILESTONES/M10_ussd_whatsapp_channels.md) |
| **Sprint** | 8 (weeks 15–16) |
| **Owner** | F, Data & Research (backup: E, DevOps/QA) |
| **Area** | Backend / i18n |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 72](../M10/ISSUE_72_channel_adapter_framework.md): Channel adapter framework with Redis session state |
| **Unblocks** | [Issue 66](../M9/ISSUE_66_notification_templates_i18n.md): Multi-language notification templates and admin editor<br>[Issue 79](../M10/ISSUE_79_channel_parity_tests.md): Cross-channel parity tests and channel-mix analytics |

## Context

Multi-language menus are listed in the benchmark as a gap global vendors leave open, and in this
market they are not a nicety. Language is chosen once and then respected everywhere: menus, messages
and the waiting-room announcement.

## Starting point

- There is no translation framework in the repo yet (no gettext or Babel setup).
- Issue 66's notification templates are waiting on this; see the note on that issue.
- F owns the translations and their review; the plumbing can be paired with B.

## Scope

- `gettext`-based i18n across web pages, channel menus and notification templates
- Translations for English, isiZulu, isiXhosa, Afrikaans and Sesotho
- Language chosen per patient (remembered) and a default per site
- Language selector on first contact in every channel
- A translation-completeness test that fails on a missing key

## Out of scope

- Writing the notification templates (Issue 66).
- The board's spoken announcements (Issue 60), which use these strings.

## Acceptance criteria

- [ ] Every user-facing string is translatable, no hard-coded English in a template
- [ ] The completeness test fails when a key is missing in any language
- [ ] Language preference persists across channels for the same patient
- [ ] Translations have been reviewed by a fluent speaker, credited in the PR
- [ ] Text expansion does not break any layout or exceed the USSD character limit
- [ ] The site default applies when a patient has expressed no preference

## How to verify

1. Delete one key from the isiXhosa catalogue: the completeness test fails and names it.
2. Choose Sesotho on USSD, then open the web ticket page: still Sesotho.
3. The PR names the fluent reviewer for each language.

## Files touched

- `src/commons/i18n.py`
- `src/locales/`
- `tests/unit/commons/test_translation_completeness.py`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #77
