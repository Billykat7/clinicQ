# Issue 58: Server-side privacy-mode rendering and consent gating

**Area:** Backend / Display
**Milestone:** M8 - Waiting-room Display Monitor
**Owner role:** Backend Lead
**Depends on:** Issues 27, 21
**Estimate:** 2 days
**Status:** Planned

## Context

The privacy rule is enforced **server-side** so that a mis-styled template can never leak a name the
site's mode forbids. Putting a person's name next to their stated symptom on a public screen is health
information about an identifiable person; the architecture, not the CSS, has to prevent it.

## Scope

- Server-side projection applying `display_mode` before serialisation: `number_only`, `name_lite` (first name + initial), `full`
- Comment rendering permitted only beside a ticket number, never beside a full name, and only with per-visit consent
- Name and comment fields absent from the payload entirely, not merely hidden, when the mode forbids them
- Consent re-check at render time so a withdrawal takes effect on the next board update
- A guard test that fails if any board response can contain a name under `number_only`

## Acceptance criteria

- [ ] Under `number_only`, no board response contains a name or comment field at all
- [ ] A comment never renders alongside a full name without recorded per-visit consent
- [ ] Withdrawing consent removes the name from the board on the next update
- [ ] The projection is applied in one place that every board response passes through
- [ ] The guard test fails if a template is given raw ticket data
- [ ] The rule is documented as a project non-negotiable

## Files touched

- `app/services/board_projection.py`
- `app/web/display/routes.py`
- `tests/unit/test_board_privacy.py`
- `docs/guideline.md`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #58
