# Issue 240: Make the claim address reachable from the dashboard

> **In short:** `GET /display/claim?code=…` has been in the code since Issue 237, documented as the way in for a television with a browser but no Chromecast — and nothing could produce a link for it. Give a manager a button that makes one.

| | |
|---|---|
| **Milestone** | [M8: Waiting-room Display Monitor](../../MILESTONES/M8_display_monitor.md) |
| **Sprint** | 11 |
| **Owner** | E, Backend/Platform |
| **Area** | Backend / Display |
| **Estimate** | 1 day |
| **Status** | Planned |
| **Depends on** | [Issue 237](ISSUE_237_smart_tv_casting.md): Find the clinic's Smart TV and send it the board<br>[Issue 61](ISSUE_61_kiosk_device_registry.md): Kiosk device registry and pairing codes |

## Context

[Issue 237](ISSUE_237_smart_tv_casting.md) shipped the claim route in both its forms: the `POST` the
Cast receiver spends, and a `GET` for a screen that can browse but will not be cast to. Its own
acceptance criteria included *"a television with a browser but no Chromecast can be paired by opening
one link"*, and `docs/OPS/SMART_TV_SETUP.md` documents the address.

The link was never reachable. Every claim code that has ever existed was minted inside the cast path
and handed to a screen over a Cast connection — it was never returned to anybody, and there was no
other way to make one. So the documented address had no source.

Three cases have no way in because of it:

- a television that browses the web but will not be cast to — the case the route was written for;
- a deployment with no registered `CAST_RECEIVER_APP_ID`, where a cast reports *reached, but no
  ClinicQ page to open* and the held row is then stranded;
- **trying the board out without a television at all.** "Send the board" only appears once a
  Chromecast has been discovered, so on a laptop no code can be minted by any route.

## Starting point

- `start_claimable_device()` already holds a row and mints a single-use code; only the cast path
  calls it.
- `ScreenConnectOut` returns `reached`, `showing`, `note` and the row — never the code, even when the
  cast failed and the code is still live.
- The **Screens on this network** card renders only where `SMART_TV_DISCOVERY_ENABLED` is true, which
  is exactly where this new path is *not* needed.
- A claim code is a credential for the ten minutes it lives, so where it may be shown is a decision,
  not a detail.

## Scope

- An endpoint that holds a row and returns the address a screen opens, contacting nothing
- A dashboard card offering it wherever a manager may pair a screen, discovery on or off
- The same address offered under a cast that was reached but did not show the board
- The address withheld once the board *is* showing, because the screen has spent the code

## Out of scope

- Changing who may pair a screen: the same `sites.display` update grant, the same audit row
- A QR code for the address (the obvious next step; a television is not a phone)
- Re-issuing an expired address in place, rather than making a new one

## Acceptance criteria

- [ ] A manager can make a claim address with no television, no Chromecast and no discovery
- [ ] The card renders where `SMART_TV_DISCOVERY_ENABLED` is false, and on `localhost`
- [ ] The page shows the whole address, the code inside it, the time left, and copies the address
- [ ] Opening the address in another browser makes that window the clinic's board
- [ ] The code is single-use: the second open lands on the ordinary pairing page, not an error
- [ ] A cast that was reached but did not show the board offers the same address
- [ ] A cast that *is* showing returns no code
- [ ] Making an address needs the grant that pairs a screen, and is audited
- [ ] The page never implies a form to type the code into: the code travels in the address

## How to verify

1. **Clinic settings → Display boards → Open the board on the screen itself → Make an address.**
2. Open the address in a second browser window: it becomes the clinic's board, and the screen list
   moves the row to *Showing the board*.
3. Open it again: back to `/display`.
4. With `SMART_TV_DISCOVERY_ENABLED=false`, the card is still there and **Screens on this network** is not.
5. Stub a cast that answers `reached, not showing`: the address is offered under the failure.

## Files touched

- `src/modules/display/router.py`, `src/web/dashboard/settings.py`
- `src/templates/dashboard/settings_devices.html`
- `src/static/js/display-claim-link.js`, `src/static/js/display-screen-finder.js`
- `contracts/sites.yaml`, `docs/OPS/SMART_TV_SETUP.md`
- `tests/integration/display/test_screen_discovery.py`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [Issue 237](ISSUE_237_smart_tv_casting.md) · [Smart TV setup](../../../OPS/SMART_TV_SETUP.md) · [how to read this spec](../README.md)

Closes #240
