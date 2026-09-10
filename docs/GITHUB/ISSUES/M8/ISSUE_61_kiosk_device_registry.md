# Issue 61: Kiosk device registry, pairing codes and heartbeat monitoring

> **In short:** A clinic plugs in a small box, types a short code into the dashboard, and the board appears, with the team alerted if a box goes quiet.

| | |
|---|---|
| **Milestone** | [M8: Waiting-room Display Monitor](../../MILESTONES/M8_display_monitor.md) |
| **Sprint** | 10 (weeks 19–20) |
| **Owner** | E, DevOps/QA (backup: F, Data & Research) |
| **Area** | Infra / Display |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 23](../M4/ISSUE_23_sites_model_profile_crud.md): `sites` model with PostGIS location and clinic profile CRUD<br>[Issue 56](../M8/ISSUE_56_board_page_kiosk.md): Waiting-room board page (kiosk) with now-serving and up-next panels |
| **Unblocks** | [Issue 62](../M8/ISSUE_62_board_resilience_tests.md): Board resilience: cached last-known state, stale banner, recovery tests<br>[Issue 106](../M14/ISSUE_106_pilot_rollout_kit.md): Pilot rollout kit: site survey, install guide, staff training pack |

> **Note:** WORKLOAD_SPLIT lists this under both D (48–62) and E; the spec and the sprint plan (sprint 10) give it to E.

## Context

A kiosk box is installed once and then ignored for months. It has to pair without anyone typing a URL
or credentials at the clinic, and the team has to know from the office when a screen has gone dark,
before the clinic phones to say the board is blank.

## Starting point

- Greenfield. Device tokens should use the kernel's hashed-secret pattern (as refresh tokens do in `src/core/security.py`), never a guessable URL.
- The heartbeat alert can reuse the monitoring from Issue 14.

## Scope

- `display_devices`: site, queue selection, label, pairing code, last heartbeat, app version
- Short-lived pairing code flow: the box shows a code, a manager enters it in the dashboard to bind it
- Device-scoped token so a board URL is not guessable or shareable
- Heartbeat every minute, with an alert when a device is silent for 10 minutes
- Kiosk provisioning guide: auto-login, browser kiosk flags, auto-launch on boot, screen-blanking disabled

## Out of scope

- Buying and shipping the boxes (Issue 106's pilot kit).

## Acceptance criteria

- [ ] A new box pairs with a code in under 2 minutes and needs no URL typed at the clinic
- [ ] An unpaired or revoked device cannot display a board
- [ ] A device silent for 10 minutes raises an alert naming the site
- [ ] The box relaunches the board automatically after a power cut
- [ ] Device status is visible in the platform-admin view
- [ ] The provisioning guide has been followed successfully by someone who did not write it

## How to verify

1. Pair a fresh browser as a device: under 2 minutes, no URL typed.
2. Revoke the device: it stops showing the board.
3. Stop a device's heartbeat for 10 minutes: an alert names the site.
4. Someone who did not write `KIOSK_SETUP.md` follows it and gets a working board.

## Files touched

- `src/modules/display/devices.py`
- `src/database/models/display_device.py`
- `alembic/versions/NNNN_display_devices.py`
- `docs/OPS/KIOSK_SETUP.md`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/07-devices-and-bom.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #61
