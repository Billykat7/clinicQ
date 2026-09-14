# Issue 58: Server-side privacy-mode rendering and consent gating

> **In short:** The server decides what a public screen may show, so a patient's name or reason can never leak onto the board through a template mistake.

| | |
|---|---|
| **Milestone** | [M8: Waiting-room Display Monitor](../../MILESTONES/M8_display_monitor.md) |
| **Sprint** | 9 (weeks 17–18); the sprint plan puts this in **D**'s lane, see the note below |
| **Owner** | A, Backend Lead (backup: B, Integrations) |
| **Area** | Backend / Display |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 21](../M3/ISSUE_21_consent_capture_withdrawal.md): Consent capture and withdrawal (display, notifications, board comment)<br>[Issue 27](../M4/ISSUE_27_display_privacy_settings.md): Display and privacy settings per site |
| **Unblocks** | [Issue 56](../M8/ISSUE_56_board_page_kiosk.md): Waiting-room board page (kiosk) with now-serving and up-next panels |

> **Note:** The spec names A as owner; the sprint plan puts board privacy in D's lane (sprint 9). **Settled by the pull request for this issue:** A owns the server rule and its guard tests (`src/modules/display/projection.py`), because a guard-tested server rule belongs with the backend lead. D builds the board against its payload (Issues 56, 57 and 60).

## Context

The privacy rule is enforced **server-side** so that a mis-styled template can never leak a name the
site's mode forbids. Putting a person's name next to their stated symptom on a public screen is health
information about an identifiable person; the architecture, not the CSS, has to prevent it.

## Starting point

- This is [non-negotiable 4](../../../guideline.md): one projection function every board response passes through, with fields removed rather than hidden.
- Consent comes from `has_consent()` (Issue 21); display mode from the site settings (Issue 27).

## Scope

- Server-side projection applying `display_mode` before serialisation: `number_only`, `name_lite` (first name + initial), `full`
- Comment rendering permitted only beside a ticket number, never beside a full name, and only with per-visit consent
- Name and comment fields absent from the payload entirely, not merely hidden, when the mode forbids them
- Consent re-check at render time so a withdrawal takes effect on the next board update
- A guard test that fails if any board response can contain a name under `number_only`

## Out of scope

- Styling what is shown (Issues 56, 59).
- Spoken announcements, which never include a name in any mode (Issue 60).

## Acceptance criteria

- [ ] Under `number_only`, no board response contains a name or comment field at all
- [ ] A comment never renders alongside a full name without recorded per-visit consent
- [ ] Withdrawing consent removes the name from the board on the next update
- [ ] The projection is applied in one place that every board response passes through
- [ ] The guard test fails if a template is given raw ticket data
- [ ] The rule is documented as a project non-negotiable

## How to verify

1. Under `number_only`, fetch every board endpoint and search the JSON for the patient's name: absent, including the keys.
2. Withdraw display consent under `name_lite`: the next update shows the number only.
3. Pass a raw ticket to the board template in a test: the guard test fails.

## Files touched

- `src/modules/display/projection.py`
- `src/web/display.py`
- `tests/unit/display/test_board_privacy.py`
- `docs/guideline.md`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #58
