# Issue 5: Base UI shell: Jinja2 layout, Tailwind build, htmx + Alpine wiring

**Area:** Frontend / Foundation
**Milestone:** M1 - Foundation & Local CI
**Owner role:** Frontend (Patient) Dev
**Depends on:** Issue 1
**Estimate:** 2 days
**Status:** Planned

## Context

Three very different surfaces (the patient pages, the clinic dashboard and the waiting-room board)
share one design system. Building that shell once, before any feature, is what stops the project ending
up with three visual languages and three copies of the same button.

## Scope

- `app/templates/base.html` with blocks for title, head, content and scripts
- Tailwind CSS build producing one compiled stylesheet, with design tokens for colour, type scale and spacing
- htmx and a small Alpine.js bundle served locally (no CDN dependency at runtime)
- A component partial library: button, card, badge, empty state, toast, skeleton loader
- Three layout variants: patient (mobile-first), dashboard (dense, desktop), board (huge type, kiosk)

## Acceptance criteria

- [ ] A page extending `base.html` renders with compiled Tailwind and no unstyled flash
- [ ] An htmx fragment swap works end to end on a demo route
- [ ] The three layouts are visually distinct but share tokens, proven by a screenshot in the PR
- [ ] No runtime CDN request is made for CSS or JS
- [ ] Components are documented on a `/dev/components` page available only in debug mode
- [ ] Base type and contrast meet WCAG 2.2 AA in the default theme

## Files touched

- `app/templates/base.html`
- `app/templates/layouts/*.html`
- `app/templates/components/*.html`
- `app/static/css/tailwind.css`
- `tailwind.config.js`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #5
