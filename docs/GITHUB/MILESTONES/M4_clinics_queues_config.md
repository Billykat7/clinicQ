# Milestone 4: Clinics, Queues & Configuration

**Status:** 📋 planned · **Phase:** Semester 1 · Sprint 4–5 · **Suggested tag:** `v0.4.0`
**Primary owner:** Backend Lead · Frontend (Clinic) Dev
**Depends on:** M3
**Blocks:** M5 (discovery reads `sites`), M6 (tickets belong to `queues`), M7, M8. This is the **widest bottleneck in the project**, see the workload split doc.

## Goal

Model the clinic itself: its location, sector, opening hours, rooms and services, its named queues, its display and privacy settings, and the workflow by which a new clinic is onboarded and verified.

## Why this milestone exists

Almost every other module reads a `site` or a `queue`. Discovery searches sites by distance, tickets
hang off queues, the display board renders a site's privacy mode, and reporting aggregates by site
and queue. That makes M4 the single most blocking milestone in the plan, and the reason the
workload split asks the Backend Lead to land **Issue 23 (sites) and Issue 25 (queues) in the first
three days of Sprint 4**, ahead of the rest of the milestone, so four other people can unblock.

Multi-room queueing is not a nice-to-have: a real clinic visit is triage → doctor → pharmacy, and a
system that models one line per clinic will be abandoned by week two of a pilot.

## Scope

- `sites`: name, sector (public/private), `geography(Point, 4326)` location, address, phone, status
- Opening hours, public-holiday calendar, and an operator-triggered temporary-closure broadcast
- `queues`: named per site (Triage, Doctor Room 1–3, Pharmacy), ordered, activatable
- Services/departments catalogue with a per-service expected service time that seeds wait estimates
- Display and privacy settings per site: `display_mode`, `display_show_comment`, retention window
- Staff-to-site assignment and room assignment
- Clinic self-service onboarding with platform-admin verification before the listing goes public
- Hand-written `sites` OpenAPI contract plus a drift test

## Issues

| # | Title |
|---|-------|
| 23 | `sites` model with PostGIS location and clinic profile CRUD |
| 24 | Opening hours, holiday calendar and temporary-closure broadcast |
| 25 | `queues` model: multi-room, multi-service queues per site |
| 26 | Services catalogue with expected service times |
| 27 | Display and privacy settings per site |
| 28 | Staff-to-site and room assignment |
| 29 | Clinic onboarding and platform-admin verification workflow |
| 30 | `sites` OpenAPI contract and module tests |

## Exit criteria

- [ ] A clinic can be created with a real coordinate and appears in a PostGIS distance query
- [ ] A site carries 1..n named queues, and a queue can be deactivated without deleting its history
- [ ] Opening hours drive an `is_open_now` flag that respects public holidays and ad-hoc closures
- [ ] Display mode defaults to `number_only` on every newly created site, never anything else
- [ ] A new clinic stays invisible in discovery until a platform admin verifies it
- [ ] `sites.yaml` documents every route the sites router serves, enforced by a drift test

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M4/)
