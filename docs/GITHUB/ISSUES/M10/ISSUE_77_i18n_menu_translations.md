# Issue 77: i18n framework and menu translations (5 languages)

**Area:** Backend / i18n
**Milestone:** M10 - USSD & WhatsApp Channels
**Owner role:** Data & Research Lead
**Depends on:** Issue 72
**Estimate:** 3 days
**Status:** Planned

## Context

Multi-language menus are listed in the benchmark as a gap global vendors leave open, and in this
market they are not a nicety. Language is chosen once and then respected everywhere: menus, messages
and the waiting-room announcement.

## Scope

- `gettext`-based i18n across web pages, channel menus and notification templates
- Translations for English, isiZulu, isiXhosa, Afrikaans and Sesotho
- Language chosen per patient (remembered) and a default per site
- Language selector on first contact in every channel
- A translation-completeness test that fails on a missing key

## Acceptance criteria

- [ ] Every user-facing string is translatable, no hard-coded English in a template
- [ ] The completeness test fails when a key is missing in any language
- [ ] Language preference persists across channels for the same patient
- [ ] Translations have been reviewed by a fluent speaker, credited in the PR
- [ ] Text expansion does not break any layout or exceed the USSD character limit
- [ ] The site default applies when a patient has expressed no preference

## Files touched

- `app/core/i18n.py`
- `app/locales/`
- `tests/unit/test_translation_completeness.py`

---

**Refs:** [M10 milestone](../../MILESTONES/M10_ussd_whatsapp_channels.md) · [product docs](../../../PRODUCT/06-channels-app-ussd-whatsapp-web.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #77
