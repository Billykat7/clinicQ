# Milestone 8: Waiting-room Display Monitor

**Status:** 📋 planned · **Phase:** Semester 2 · Sprint 9 · **Suggested tag:** `v0.8.0`
**Primary owner:** Frontend (Clinic) Dev · DevOps/QA Lead (device provisioning)
**Depends on:** M6, M7
**Blocks:** M14 pilot

## Goal

Ship the screen every patient in the waiting room stares at: now serving plus up next, live over SSE, privacy-mode aware, accessible at 5 metres, audible for patients who are not watching, and running unattended on a cheap kiosk box.

## Why this milestone exists

This is the most visible part of the product and the one with the sharpest privacy edge. Putting a
person's **name next to their symptom** on a public screen is health information about an identifiable
person, displayed publicly, squarely inside what POPIA treats as special personal information. So the
board's rules are architectural, not cosmetic:

- `number_only` is the hard default for every new site.
- A comment may appear **only** beside a ticket number, never beside a full name, unless the patient
  gave an explicit per-visit consent.
- The renderer applies the site's privacy mode **server-side**, so a mis-styled template can never
  leak a name that the mode forbids.

Accessibility is treated the same way. A board that a patient with low vision cannot read, or that a
patient looking at their phone never notices, has failed at its only job; hence the contrast, type-size
and audio-announcement requirements.

## Scope

- Kiosk board page: now serving, up next (3–5), per-queue panels, clock
- SSE update channel with exponential-backoff reconnect and a visible heartbeat
- Server-side privacy-mode rendering (`number_only` / `name_lite` / `full`) with consent gating
- Accessibility: WCAG 2.2 AA contrast, 5-metre legibility, colour-blind-safe status colours, reduced motion
- Audio chime and multi-language text-to-speech announcement of the called number and room
- Device registry: pairing code, kiosk provisioning, auto-launch on boot, heartbeat monitoring
- Resilience: last-known board cached locally, explicit stale banner, load-shedding recovery

## Issues

| # | Title |
|---|-------|
| 56 | Waiting-room board page (kiosk) with now-serving and up-next panels |
| 57 | SSE live update channel with reconnect, backoff and heartbeat |
| 58 | Server-side privacy-mode rendering and consent gating |
| 59 | Accessibility pass: contrast, type scale, 5-metre legibility, reduced motion |
| 60 | Audio chime and multi-language text-to-speech call announcements |
| 61 | Kiosk device registry, pairing codes and heartbeat monitoring |
| 62 | Board resilience: cached last-known state, stale banner, recovery tests |

## Exit criteria

- [ ] The board updates within 2 seconds of Call Next, and recovers on its own after a 10-minute outage
- [ ] With `number_only` set, no request to the board endpoint returns a patient name in the payload at all
- [ ] A comment never renders alongside a full name unless per-visit consent is recorded
- [ ] Ticket numbers are legible at 5 metres on a 32-inch screen and pass AA contrast in a bright room
- [ ] A called ticket triggers a chime and a spoken announcement in the site's configured language
- [ ] An offline kiosk shows a stale banner with the timestamp of the last good update

---

**Navigation:** [GitHub docs index](../README.md) · [Implementation plan](../../PLAN/IMPLEMENTATION_PLAN.md) · [Workload split](../../TEAM/WORKLOAD_SPLIT.md) · [Issues](../ISSUES/M8/)
