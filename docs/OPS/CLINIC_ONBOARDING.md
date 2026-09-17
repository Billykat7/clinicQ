# Getting a clinic onto ClinicQ

> **In short:** add the clinic, send one link, and the clinic does the rest. You check it when they
> say it is ready.

This is the whole journey, end to end, for whoever runs the pilot. It needs no engineer and no
database access.

There are two ways a clinic arrives. Both end in the same place — a platform admin deciding whether
patients should see it — and the difference is only who types the first screen.

| | **The clinic finds us** | **We add them** |
|---|---|---|
| Who starts | Anyone, at [`/register-clinic`](../../src/templates/web/register_clinic.html) | An operator, at `/admin/clinics` |
| What it creates | A clinic waiting to be checked | A **draft**: invisible to patients |
| Who finishes the setup | The first manager, once invited | The same, from the setup link |
| Who decides | A platform admin, at `/admin/verification` | The same |

The second is the one a pilot uses, and it is what follows.

## 1. Add the clinic — 2 minutes

`/admin/clinics` → **New clinic**.

You need the name, the street address and the town. Press **Find on the map** and pick the right
candidate; if the lookup cannot answer, type the latitude and longitude in — the fields are always
editable, and the point matters, because patients find a clinic by distance.

What you get is a **draft**. It is invisible on `/discover`, nobody can join a queue there, and it
already has ClinicQ's standard room set and services catalogue, so the clinic starts from something
rather than from nothing.

> **The web address (slug) is the one field to get right first.** It is the clinic's public handle —
> `hillbrow-chc` — and it is in every link a patient is given. It can be changed, but not after
> patients have the link.

## 2. Send the setup link — 30 seconds

Open the clinic in the list, and under **Send the setup link** put in the work email address of
whoever runs it. Press **Send the link**.

That link is a single-use staff invitation. Following it:

1. creates that person's account and makes them the clinic's **manager**;
2. lands them on **Setup** — a checklist of what the clinic still needs.

It expires (`STAFF_INVITE_EXPIRE_HOURS`, 72 hours by default) and can only be used once. If it is
not used in time, send another; nothing is lost.

> **Send it to a person, not to a shared mailbox.** The account it creates is theirs, and every
> change that person makes is recorded against their name. A clinic that needs more than one account
> invites the rest from its own Staff tab — which is step 6 of their checklist.

## 3. The clinic sets itself up — 20 minutes, their time not yours

The checklist has six things on it, and each one opens the tab that handles it:

| | What they do | Why it matters |
|---|---|---|
| **Check your clinic's details** | Confirm the name, address and map point you typed | You typed it from what they told you on the phone. This is them looking at it. |
| **Name your rooms** | Rename, remove or add to the standard set | A room is a queue. These are the lines patients wait in. |
| **Say what you offer** | Including the pharmacy or dispensary | It is what a walk-in is asked which of, and what patients search for. |
| **Set your opening hours** | The ordinary week | **Until this is set, patients are told the clinic is closed** and nobody can join from a phone. |
| **Decide what the screen shows** | Ticket numbers only, or more | It shows numbers and nothing else until they choose otherwise. Anything more is a decision about their patients' privacy, and it is theirs. |
| **Invite the people who run it** | Reception, nurses, doctors | A clinic that depends on one account stops when that person is away. |

They can stop and come back; nothing is lost, and the checklist is worked out from what the clinic
actually has, so a change made anywhere shows up on it.

When all six are done, **Put this clinic forward** becomes available. Before that it refuses, and
says exactly what is still missing.

## 4. Check it — 5 minutes

The clinic appears in `/admin/verification` under **Waiting**. Open it, look at what they have set
up, and:

- **Verify** — patients can find it and join its queues from that moment;
- **Send it back** — say what is wrong. **Whatever you write is shown to them**, so write it for
  them, not as a note to yourself;
- **Suspend** — stops new joins immediately, on every channel at once.

Whoever is listed as the clinic's contact is told either way.

## When something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| "The web address … is already in use" | Another clinic holds that slug, listed or not | Choose another. It does not say which clinic, on purpose. |
| The clinic says the link does not work | It has been used, revoked, or has expired | Send another from `/admin/clinics`. |
| They cannot put the clinic forward | Something on the checklist is unfinished | The button's refusal names every step still missing. |
| The clinic is verified but patients cannot join | Almost always the opening hours, or no queue takes remote joins | Their Hours tab, then their Queues tab. |
| A clinic has to come off the directory now | | **Suspend** it in `/admin/verification` — immediate, and reversible. Archiving in `/admin/clinics` is for an entry that should never have existed. |

## What this is built from

Nothing here is a new way into a clinic. The link is the staff invitation of Issue 22; the setup
tabs are the clinic settings of Issue 54; the checklist is derived from the clinic's own rows; and
the listing decision is Issue 29's state machine, whose only writer is `onboarding.transition`. A
clinic can ask to be listed and can never list itself: submitting is the clinic's grant at the
`assigned` tier, and the decision is the operator's at `business` (Issue 221).

---

**See also:** [Issue 222](../GITHUB/ISSUES/M15/ISSUE_222_admin_clinics_console.md) (the console) ·
[Issue 223](../GITHUB/ISSUES/M15/ISSUE_223_clinic_setup_link.md) (the setup link) ·
[Issue 29](../GITHUB/ISSUES/M4/ISSUE_29_clinic_onboarding_verification.md) (verification)
