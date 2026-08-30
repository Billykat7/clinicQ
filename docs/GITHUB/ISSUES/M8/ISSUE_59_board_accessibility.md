# Issue 59: Accessibility pass: contrast, type scale, 5-metre legibility, reduced motion

**Area:** Frontend / Display
**Milestone:** M8 - Waiting-room Display Monitor
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issue 56
**Estimate:** 2 days
**Status:** Planned

## Context

A board a patient with low vision cannot read has failed at its only job. Contrast, type size, colour
independence and motion sensitivity are treated as acceptance criteria here rather than as a polish pass
that never happens.

## Scope

- WCAG 2.2 AA contrast in both a bright daylight room and a dim one
- Type scale validated at 4–5 metres on the target screen sizes
- Status conveyed by shape and text as well as colour, for colour-blind viewers
- `prefers-reduced-motion` respected, with a non-animated highlight alternative
- A high-contrast board theme selectable per site

## Acceptance criteria

- [ ] Automated contrast checks pass on every board state
- [ ] Ticket numbers are readable at 5 metres, verified by a physical test documented in the PR
- [ ] The board is fully interpretable in greyscale
- [ ] Reduced-motion users get a static highlight that is equally noticeable
- [ ] The high-contrast theme is selectable from clinic settings
- [ ] Findings and evidence are recorded for the M13 accessibility audit

## Files touched

- `app/static/css/board.css`
- `app/templates/display/board.html`
- `tests/a11y/test_board_contrast.py`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #59
