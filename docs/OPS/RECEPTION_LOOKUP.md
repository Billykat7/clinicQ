# Finding a ticket at reception: the QR and the code

Every ticket carries one code (Issue 70). The patient's ticket page shows it as a QR and as six characters
to say aloud, the page's offline copy on the phone shows the same, and so does the printed stub. Reception
opens the ticket from either at **Find ticket** (`/dashboard/sites/<clinic>/lookup`, shortcut `f`), which the front desk and the
clinic manager can open (a nurse, whose grant covers only their own room, cannot).

## 1. The code

- Six characters in two groups, `K7M-4QP`, from `23456789ABCDEFGHJKMNPQRSTUVWXYZ`: no `0`, `O`, `1`, `I` or
  `L`, so nothing reads as something else. The ticket page and the stub also spell it out the way it is said
  across a counter: `Kilo 7 Mike, 4 Quebec Papa`.
- Typed in any case, with or without the dash or a space. A character that cannot be in a code (a zero, say)
  is refused as a typing mistake, never guessed at.
- The QR says `CLINICQ:K7M-4QP`: the code behind a prefix, so scanning anything else says "not a ticket code".
- Random, drawn with a cryptographic generator. Nothing about it follows from the ticket's number, time or
  the code before it. It opens a ticket only for staff signed in at that ticket's clinic, and it never opens
  the patient's ticket page, which has its own long link. A photographed stub shows nothing to whoever took
  the photo.

## 2. What a lookup answers

| Situation | The desk sees |
|---|---|
| Today's open ticket at this clinic | The number, the queue and room, where it stands, the wait, how it joined |
| A ticket that was transferred | The leg the visit is in now, "moved on from T004" |
| A ticket that has ended (done, cancelled, missed) | "Ticket has ended … Its code cannot be used again." |
| A code from an earlier day | "Code K7M-4QP was for Monday 14 September 2026 … the patient needs a new ticket today." |
| Another clinic's code, or no such code | "No ticket at this clinic has the code …", the same words either way |

The API behind the page is `GET /api/v1/sites/{site_id}/tickets/lookup?code=…` (`queues.tickets` `read`),
with refusals coded `queue.lookup.not_a_code`, `not_found`, `expired` and `ended`.

## 3. A desk QR scanner

Any USB or Bluetooth scanner that acts as a keyboard works; there is nothing to install.

1. Set it to **keyboard (HID) mode**, with **Enter** as the suffix. Most scanners ship this way.
2. Enable **QR Code**. Some one-dimensional scanners read only barcodes.
3. Set the keyboard layout to match the computer's (usually US or UK), or the `:` in `CLINICQ:` comes out as
   another character, and the page says "not a ticket code".
4. Open **Find ticket** and scan a patient's ticket page or stub. The field has focus when the page opens and
   again after every result, so the desk never has to click between patients.

A phone screen at full brightness scans best. If a scanner struggles with a screen, the patient can read the
code aloud.

## 4. Checked, and not checked

- The QR the page draws is read back by Chromium's own QR reader in `tests/e2e/patient/test_ticket_qr.py`
  (on macOS; Linux Chromium has no reader, where the unit tests still compare every module with the QR
  library's symbol).
- **Not yet done with a real desk scanner, or by reading a code aloud to a colleague** (the issue's steps 1
  and 2). Record the result here: scanner model, phone or stub, and whether the colleague typed the code right
  the first time.

---

**Refs:** [Issue 70](../GITHUB/ISSUES/M9/ISSUE_70_qr_ticket_code.md) · `src/modules/queue/ticket_codes.py` ·
`src/web/dashboard/lookup.py` · `src/templates/queue/_qr.html`
