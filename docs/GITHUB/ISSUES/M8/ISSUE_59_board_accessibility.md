# Issue 59: Accessibility pass: contrast, type scale, 5-metre legibility, reduced motion

> **In short:** The board is readable by everyone in the room, including people with poor eyesight, colour blindness or sensitivity to motion.

| | |
|---|---|
| **Milestone** | [M8: Waiting-room Display Monitor](../../MILESTONES/M8_display_monitor.md) |
| **Sprint** | 10 (weeks 19–20) |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Display |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 56](../M8/ISSUE_56_board_page_kiosk.md): Waiting-room board page (kiosk) with now-serving and up-next panels |
| **Unblocks** | [Issue 101](../M13/ISSUE_101_accessibility_audit_remediation.md): WCAG 2.2 AA accessibility audit and remediation |

## Context

A board a patient with low vision cannot read has failed at its only job. Contrast, type size, colour
independence and motion sensitivity are treated as acceptance criteria here rather than as a polish pass
that never happens.

## Starting point

- Works on Issue 56's page and stylesheet. The evidence gathered here feeds the accessibility audit (Issue 101).

## Scope

- WCAG 2.2 AA contrast in both a bright daylight room and a dim one
- Type scale validated at 4–5 metres on the target screen sizes
- Status conveyed by shape and text as well as colour, for colour-blind viewers
- `prefers-reduced-motion` respected, with a non-animated highlight alternative
- A high-contrast board theme selectable per site

## Out of scope

- The accessibility audit of the rest of the product (Issue 101).

## Acceptance criteria

- [ ] Automated contrast checks pass on every board state
- [ ] Ticket numbers are readable at 5 metres, verified by a physical test documented in the PR
- [ ] The board is fully interpretable in greyscale
- [ ] Reduced-motion users get a static highlight that is equally noticeable
- [ ] The high-contrast theme is selectable from clinic settings
- [ ] Findings and evidence are recorded for the M13 accessibility audit

## How to verify

1. Run an automated contrast check on every board state: all pass WCAG 2.2 AA.
2. Switch the display to greyscale: every state is still understandable.
3. Turn on reduced motion in the OS: the new-call highlight is static but just as obvious.

## Files touched

- `src/static/css/board.css`
- `src/templates/display/board.html`
- `tests/a11y/display/test_board_contrast.py`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #59
