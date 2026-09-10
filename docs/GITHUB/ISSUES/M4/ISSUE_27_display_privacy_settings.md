# Issue 27: Display and privacy settings per site

> **In short:** Each clinic controls what its waiting-room screen may show, and every new clinic starts with ticket numbers only.

| | |
|---|---|
| **Milestone** | [M4: Clinics, Queues & Configuration](../../MILESTONES/M4_clinics_queues_config.md) |
| **Sprint** | 5 (weeks 9–10) |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Clinics |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 21](../M3/ISSUE_21_consent_capture_withdrawal.md): Consent capture and withdrawal (display, notifications, board comment)<br>[Issue 23](../M4/ISSUE_23_sites_model_profile_crud.md): `sites` model with PostGIS location and clinic profile CRUD |
| **Unblocks** | [Issue 30](../M4/ISSUE_30_sites_openapi_contract_tests.md): `sites` OpenAPI contract and module tests<br>[Issue 54](../M7/ISSUE_54_manager_settings_ui.md): Clinic manager settings UI (profile, hours, display mode, staff, services)<br>[Issue 56](../M8/ISSUE_56_board_page_kiosk.md): Waiting-room board page (kiosk) with now-serving and up-next panels<br>[Issue 58](../M8/ISSUE_58_board_privacy_rendering.md): Server-side privacy-mode rendering and consent gating |

## Context

The waiting-room board is the sharpest privacy surface in the product. These settings are where a
clinic chooses what it shows, and the defaults are a deliberate safeguarding decision: every new site
starts at `number_only`, and nothing in the codebase may change that default.

## Starting point

- Greenfield: columns on `sites` plus a settings page. This is where the [privacy non-negotiable](../../../guideline.md) starts, so read rule 4 first.
- Build the settings page on the existing signed-in shell (`src/templates/base.html`) so it inherits the design tokens and CSP rules.

## Scope

- Site settings: `display_mode` (`number_only` / `name_lite` / `full`), `display_show_comment`, `board_language`, `announce_audio`
- `number_only` as the hard default on creation, with a test that fails if the default is changed
- Changing display mode requires clinic-manager permission and writes an audit row
- A plain-language warning in the UI when a manager selects a mode that exposes names or comments
- Retention window setting for `reason_text`, feeding the M13 purge job

## Out of scope

- The board rendering and server-side projection (Issues 56, 58).
- The purge job that uses the retention window (Issue 95).

## Acceptance criteria

- [ ] A newly created site always has `display_mode = number_only`
- [ ] Enabling `full` mode requires an explicit confirmation and is audited
- [ ] `display_show_comment` cannot be enabled together with `full` name mode without per-visit patient consent
- [ ] The warning text states, in plain language, what will appear on a public screen
- [ ] The retention window is validated against the policy ceiling in the data map
- [ ] A guard test fails if any code path sets a non-default display mode at creation

## How to verify

1. Create a site through every code path (API, onboarding, seed): each has `display_mode = number_only`.
2. Switch a site to `full` as a clinic manager: a plain-language warning, a confirmation, and an audit row.
3. Try the same switch as a receptionist: refused.

## Files touched

- `src/modules/sites/settings.py`
- `src/database/models/site.py`
- `src/templates/dashboard/settings_display.html`
- `tests/unit/sites/test_display_defaults.py`

---

**Refs:** [M4 milestone](../../MILESTONES/M4_clinics_queues_config.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #27
