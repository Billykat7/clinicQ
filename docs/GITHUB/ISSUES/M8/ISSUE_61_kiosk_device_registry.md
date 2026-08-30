# Issue 61: Kiosk device registry, pairing codes and heartbeat monitoring

**Area:** Infra / Display
**Milestone:** M8 - Waiting-room Display Monitor
**Owner role:** DevOps/QA Lead
**Depends on:** Issues 56, 23
**Estimate:** 2 days
**Status:** Planned

## Context

A kiosk box is installed once and then ignored for months. It has to pair without anyone typing a URL
or credentials at the clinic, and the team has to know from the office when a screen has gone dark,
before the clinic phones to say the board is blank.

## Scope

- `display_devices`: site, queue selection, label, pairing code, last heartbeat, app version
- Short-lived pairing code flow: the box shows a code, a manager enters it in the dashboard to bind it
- Device-scoped token so a board URL is not guessable or shareable
- Heartbeat every minute, with an alert when a device is silent for 10 minutes
- Kiosk provisioning guide: auto-login, browser kiosk flags, auto-launch on boot, screen-blanking disabled

## Acceptance criteria

- [ ] A new box pairs with a code in under 2 minutes and needs no URL typed at the clinic
- [ ] An unpaired or revoked device cannot display a board
- [ ] A device silent for 10 minutes raises an alert naming the site
- [ ] The box relaunches the board automatically after a power cut
- [ ] Device status is visible in the platform-admin view
- [ ] The provisioning guide has been followed successfully by someone who did not write it

## Files touched

- `app/database/models/display_device.py`
- `app/services/device_pairing.py`
- `docs/OPS/KIOSK_SETUP.md`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/07-devices-and-bom.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #61
