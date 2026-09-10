# Issue 5: Base UI shell: Jinja2 layout, Tailwind build, htmx + Alpine wiring

> **In short:** The shared look of every screen: one base layout and three variants (patient, dashboard, board) built from one set of design tokens.

| | |
|---|---|
| **Milestone** | [M1: Foundation & Local CI](../../MILESTONES/M1_foundation_local_ci.md) |
| **Sprint** | 1 (weeks 1–2), with D on the same issue |
| **Owner** | C, Frontend/Patient (backup: D, Frontend/Clinic) |
| **Area** | Frontend / Foundation |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 1](../M1/ISSUE_1_repo_scaffold_app_factory.md): Repository scaffold, Python 3.14 + FastAPI app factory, typed settings |
| **Unblocks** | [Issue 69](../M9/ISSUE_69_pwa_shell_service_worker.md): PWA shell: manifest, service worker, offline last-known ticket |

## Context

Three very different surfaces (the patient pages, the clinic dashboard and the waiting-room board)
share one design system. Building that shell once, before any feature, is what stops the project ending
up with three visual languages and three copies of the same button.

## Starting point

- `src/templates/base.html`, `src/static/css/site.css` (the design tokens), `admin.css`, `landing.css` and htmx (`src/static/vendor/`) already exist, with light and dark themes.
- The kernel styles with hand-written CSS tokens, not Tailwind, and a guard test (`tests/unit/platform/test_no_inline_styles.py`) enforces the CSP rule of no inline styles. Decide Tailwind versus tokens before writing new CSS; see [open decisions](../README.md#open-decisions).
- Alpine.js is not in the repo yet.

## Scope

- `app/templates/base.html` with blocks for title, head, content and scripts
- Tailwind CSS build producing one compiled stylesheet, with design tokens for colour, type scale and spacing
- htmx and a small Alpine.js bundle served locally (no CDN dependency at runtime)
- A component partial library: button, card, badge, empty state, toast, skeleton loader
- Three layout variants: patient (mobile-first), dashboard (dense, desktop), board (huge type, kiosk)

## Out of scope

- The dashboard screens themselves (M7) and the board (M8).
- Patient pages (Issues 32, 68).

## Acceptance criteria

- [ ] A page extending `base.html` renders with compiled Tailwind and no unstyled flash
- [ ] An htmx fragment swap works end to end on a demo route
- [ ] The three layouts are visually distinct but share tokens, proven by a screenshot in the PR
- [ ] No runtime CDN request is made for CSS or JS
- [ ] Components are documented on a `/dev/components` page available only in debug mode
- [ ] Base type and contrast meet WCAG 2.2 AA in the default theme

## How to verify

1. Open the demo route in light and dark themes: no unstyled flash, no console errors.
2. The network tab shows no request to a CDN for CSS or JS.
3. The PR carries a screenshot of each of the three layouts.

## Files touched

- `src/templates/base.html`
- `src/templates/layouts/`
- `src/templates/components/`
- `src/static/css/site.css`
- `src/static/vendor/`

---

**Refs:** [M1 milestone](../../MILESTONES/M1_foundation_local_ci.md) · [product docs](../../../PRODUCT/13-tech-implementation.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #5
