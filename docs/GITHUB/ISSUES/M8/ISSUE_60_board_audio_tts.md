# Issue 60: Audio chime and multi-language text-to-speech call announcements

**Area:** Frontend / Display
**Milestone:** M8 - Waiting-room Display Monitor
**Owner role:** Frontend (Clinic) Dev
**Depends on:** Issues 57, 26
**Estimate:** 2 days
**Status:** Planned

## Context

Patients look at their phones. A number that only ever appears silently on a screen gets missed, the
ticket is recalled, and the queue slows down. A chime plus a spoken announcement in the clinic's own
language is the single cheapest way to cut missed calls.

## Scope

- Distinct chime on call, with a per-site volume setting and a mute option
- Text-to-speech announcement of the ticket number and room, using the browser speech API with a pre-rendered audio fallback
- Language selectable per site (English, isiZulu, isiXhosa, Afrikaans, Sesotho)
- Announcement queueing so simultaneous calls are spoken in order, never overlapped
- Announcements never speak a patient name: number and room only, regardless of display mode

## Acceptance criteria

- [ ] A called ticket produces a chime followed by a spoken number and room
- [ ] Two simultaneous calls are announced sequentially, not overlapping
- [ ] The announcement is comprehensible in each supported language, verified by a native or fluent speaker on the team
- [ ] Announcements never include a patient name, proven by a test
- [ ] Audio can be muted per site without disabling the visual highlight
- [ ] A browser without speech support falls back to pre-rendered number audio

## Files touched

- `app/static/js/announce.js`
- `app/static/audio/`
- `app/services/board_projection.py`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md)

Closes #60
