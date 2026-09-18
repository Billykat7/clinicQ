# Issue 237: Find the clinic's Smart TV on the network and send it the board

> **In short:** A second way to set a waiting-room board up, for a clinic whose television has Chromecast built in: the server finds the screen on the clinic's own network, a manager picks it from a list, and the board appears. Nobody reads a code off a wall.

| | |
|---|---|
| **Milestone** | [M8: Waiting-room Display Monitor](../../MILESTONES/M8_display_monitor.md) |
| **Sprint** | 5 (weeks 9–10) |
| **Owner** | E, Backend/Platform |
| **Area** | Backend / Display |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 61](ISSUE_61_kiosk_device_registry.md): Kiosk device registry, pairing codes and heartbeat monitoring<br>[Issue 56](ISSUE_56_board_page_kiosk.md): Waiting-room board page (kiosk) |

## Context

[Issue 61](ISSUE_61_kiosk_device_registry.md) set a board up the way that works with every television
ever made: a small box on the HDMI port shows a six-character code, and a clinic manager types it
into the dashboard. That stays the default, because it depends on nothing — not the TV, not the
network, not a vendor.

It also asks a clinic to buy and mount a box. A television with Chromecast built in (most Android TV
and Google TV sets) is already a computer on the clinic's network, and already announces itself to
anything that asks. For those clinics the box is a cost with no purpose, and the code is a walk
across the room.

## Starting point

- The device registry, the pairing code and the board are all Issue 61's and unchanged. This adds a
  second way *into* the same registry, not a second registry.
- Discovery is a multicast question, so it is asked by **the server** and only reaches televisions on
  the server's own network. That is a real limit, not a bug to fix: a cloud instance shares no
  network with a clinic, and the feature is off unless a deployment says otherwise.
- A Chromecast runs a *receiver application* registered with Google, not any page you point it at.
  Finding and reaching a screen works without that registration; showing it the board does not.

## Scope

- Find Chromecast-capable screens on the server's network and list them for a clinic manager
- Send a chosen screen this clinic's board, pairing it without anyone typing a code
- A claim the screen spends itself, single-use, so a reservation is never a working credential
- The Cast receiver page the registration points at, with a policy of its own
- An empty result that says *which* of its causes it is, including the one macOS causes silently
- A command an operator can run on the server itself when the dashboard finds nothing
- Both setup guides say when to choose which

## Out of scope

- Registering the Cast receiver application with Google (a one-off account action, documented)
- Screens that are not Chromecast-capable: they are found and listed where the network allows, and
  pointed at the claim link, but nothing is pushed to them
- An on-prem agent that would let a cloud deployment see a clinic's network
- Speakers as a separate paired device, and per-device announcement settings

## Acceptance criteria

- [ ] A manager searching from the dashboard sees the clinic's Chromecast-capable screens
- [ ] Picking one pairs it and shows this clinic's board, with nothing typed on either side
- [ ] A claim code works once, and cannot be spent on the code-typing flow (or the other way round)
- [ ] A screen that never answers leaves a visible, removable row, and the attempt is audited
- [ ] An empty search says why: switched off, package missing, nothing answered, or the macOS gate
- [ ] Searching and connecting need the grant that pairs a screen; the front desk cannot
- [ ] The deployment's other pages keep the strict Content-Security-Policy
- [ ] A television with a browser but no Chromecast can be paired by opening one link

## How to verify

1. Search from **Clinic settings → Display boards** on a server sharing a network with a Cast screen.
2. Press **Send the board**: the screen opens the clinic's board.
3. Open `/display/claim?code=…` twice: the first pairs, the second goes back to the pairing page.
4. Turn the screen off and connect: the row reads *pairing* and the note says the screen did not answer.
5. Run `python -m scripts.find_screens` on the server; compare with `dns-sd -B _googlecast._tcp local.`

## Files touched

- `src/modules/display/discovery.py`, `src/modules/display/casting.py`
- `src/modules/display/devices.py`, `src/modules/display/router.py`
- `src/web/display.py`, `src/templates/display/cast.html`, `src/static/js/cast-receiver.js`
- `src/templates/dashboard/settings_devices.html`, `src/static/js/display-screen-finder.js`
- `scripts/find_screens.py`, `docs/OPS/SMART_TV_SETUP.md`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [kiosk setup](../../../OPS/KIOSK_SETUP.md) · [Smart TV setup](../../../OPS/SMART_TV_SETUP.md) · [how to read this spec](../README.md)

Closes #237
