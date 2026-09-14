# Issue 60: Audio chime and multi-language text-to-speech call announcements

> **In short:** When a number is called, the room hears a chime and the number and room spoken in the clinic's language, never a patient's name.

| | |
|---|---|
| **Milestone** | [M8: Waiting-room Display Monitor](../../MILESTONES/M8_display_monitor.md) |
| **Sprint** | 10 (weeks 19–20) |
| **Owner** | D, Frontend/Clinic (backup: C, Frontend/Patient) |
| **Area** | Frontend / Display |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 27](../M4/ISSUE_27_display_privacy_settings.md): Display and privacy settings per site<br>[Issue 57](../M8/ISSUE_57_board_sse_channel.md): SSE live update channel with reconnect, backoff and heartbeat<br>[Issue 58](../M8/ISSUE_58_board_privacy_rendering.md): Server-side privacy-mode rendering and consent gating |
| **Unblocks** | No other issue waits on this one. |

> **Note:** The dependency list originally named Issue 26 (the services catalogue), a typo for Issue 27 (display settings, which holds `board_language` and `announce_audio`), and left out Issue 58, whose projection the announcements read. Both are corrected above.

## Context

Patients look at their phones. A number that only ever appears silently on a screen gets missed, the
ticket is recalled, and the queue slows down. A chime plus a spoken announcement in the clinic's own
language is the single cheapest way to cut missed calls.

## Starting point

- Browser speech (`speechSynthesis`) with pre-recorded number clips as a fallback; the clips live under `src/static/audio/`.
- Announcements read the same projection as the screen (Issue 58), so a name is never available to speak.
- F sources and checks the translations (Issue 77).

## Scope

- Distinct chime on call, with a per-site volume setting and a mute option
- Text-to-speech announcement of the ticket number and room, using the browser speech API with a pre-rendered audio fallback
- Language selectable per site (English, isiZulu, isiXhosa, Afrikaans, Sesotho)
- Announcement queueing so simultaneous calls are spoken in order, never overlapped
- Announcements never speak a patient name: number and room only, regardless of display mode

## Out of scope

- The translation framework (Issue 77).

## Acceptance criteria

- [ ] A called ticket produces a chime followed by a spoken number and room
- [ ] Two simultaneous calls are announced sequentially, not overlapping
- [ ] The announcement is comprehensible in each supported language, verified by a native or fluent speaker on the team
- [ ] Announcements never include a patient name, proven by a test
- [ ] Audio can be muted per site without disabling the visual highlight
- [ ] A browser without speech support falls back to pre-rendered number audio

## How to verify

1. Call two tickets at once: two announcements, one after the other.
2. Set the site language to isiZulu: a fluent speaker on the team confirms it is understandable, and says so in the PR.
3. Mute audio for the site: the visual highlight still happens.

## Files touched

- `src/static/js/board-announce.js`
- `src/static/audio/`
- `src/modules/display/projection.py`

---

**Refs:** [M8 milestone](../../MILESTONES/M8_display_monitor.md) · [product docs](../../../PRODUCT/04-display-monitor.md) · [workload split](../../../TEAM/WORKLOAD_SPLIT.md) · [how to read this spec](../README.md)

Closes #60
