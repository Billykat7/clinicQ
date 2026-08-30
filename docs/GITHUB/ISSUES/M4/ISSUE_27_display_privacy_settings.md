# Issue 27: Display and privacy settings per site

**Area:** Backend / Clinics
**Milestone:** M4 - Clinics, Queues & Configuration
**Owner role:** Backend Lead
**Depends on:** Issues 23, 21
**Estimate:** 1 day
**Status:** Planned

## Context

The waiting-room board is the sharpest privacy surface in the product. These settings are where a
clinic chooses what it shows, and the defaults are a deliberate safeguarding decision: every new site
starts at `number_only`, and nothing in the codebase may change that default.

## Scope

- Site settings: `display_mode` (`number_only` / `name_lite` / `full`), `display_show_comment`, `board_language`, `announce_audio`
- `number_only` as the hard default on creation, with a test that fails if the default is changed
- Changing display mode requires clinic-manager permission and writes an audit row
- A plain-language warning in the UI when a manager selects a mode that exposes names or comments
- Retention window setting for `reason_text`, feeding the M13 purge job

## Acceptance criteria

- [ ] A newly created site always has `display_mode = number_only`
- [ ] Enabling `full` mode requires an explicit confirmation and is audited
- [ ] `display_show_comment` cannot be enabled together with `full` name mode without per-visit patient consent
- [ ] The warning text states, in plain language, what will appear on a public screen
- [ ] The retention window is validated against the policy ceiling in the data map
- [ ] A guard test fails if any code path sets a non-default display mode at creation

## Files touched

- `app/database/models/site.py`
- `app/services/site_settings.py`
- `app/templates/dashboard/settings_display.html`
- `tests/unit/test_display_defaults.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #27
