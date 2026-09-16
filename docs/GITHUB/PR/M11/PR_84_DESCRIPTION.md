# PR: One phone, a household: joining and booking for a dependant, with consent and a code (Issue 84 / M11-84)

**Milestone:** [Milestone 11: Appointments, Check-in & Patient Care Extras](https://github.com/Billykat7/clinicQ/milestone/11) ·
**Issue:** [#84](https://github.com/Billykat7/clinicQ/issues/84) · **Builds on:** #17 (patient identity and the
one-time code), #21 (consent), #40 (the join service), #63 (notifications), #81 (booking, PR #210), all
merged · **Unblocks:** nothing waits on this one

One household often shares one smartphone. A parent joins the queue for a child; a daughter books for her
mother. With this PR, ClinicQ says who the visit is for, and keeps one rule throughout: **the ticket belongs
to the person being seen.** With this PR:

- **"Who is this for?"** sits above the join form and the booking form: *Me*, or somebody this phone acts for.
- **Linking somebody who has a phone needs the code sent to that phone.** That is the verification step:
  typing a stranger's number achieves nothing.
- **Linking a child with no phone of their own** needs no code, because there is no number to prove and no
  existing record to take over: it makes a new, empty record that can never sign in.
- **The ticket and the booking are the dependant's**: their number, their reference, their name on the board
  under **their own** consent (#58's projection is untouched, and never shows the proxy).
- **The messages go to the phone that exists** — the proxy's — under that phone owner's own consent, quiet
  hours and opt-out.
- **Every proxy action names both people**: the audit row's actor is the proxy and its entity is the
  dependant, and `ticket.proxy_patient_id` and `appointment.proxy_patient_id` keep the pair on the record.
- **Ending the link is immediate, from either side.** The next action is refused; the tickets already taken
  stay with the dependant.

**Not done here, and not claimed:**

- **Guardianship is not verified**, as the issue says: a phone check is what this is. A clinic that must be
  sure who a child belongs to asks at the desk, as it does today.
- **Only the web** offers the picker. USSD and WhatsApp (M10) are not built; the service functions take the
  dependant the same way for any channel when they are.
- **The front desk cannot yet make a link for somebody.** The dashboard shows who acted on a ticket through
  the audit trail, but linking is the patient's own action on their phone.

## Summary

- **The model** (migration `0045`):
  - `patient_link`: proxy, dependant, relationship, `verified_at` (when the number was proved), `revoked_at`,
    `revoked_by`, with `uq_patient_link_pair`;
  - `ticket.proxy_patient_id` and `appointment.proxy_patient_id`;
  - `patient.phone_e164` becomes **nullable**: a dependant with no phone of their own. It stays unique, and
    everyone else has a number.
- **The service** (`src/modules/patients/proxy.py`):
  - `send_link_code` and `link_with_code` (the code path), `link_without_phone` (a child);
  - `dependants`, `acting_for` — **the one gate** every proxy action goes through — and `patient_or_dependant`
    for the routes;
  - `revoke` from either side, which also withdraws the dependant's consent;
  - `messages_for`, the answer the notification service asks for;
  - `record_action`, the audit row naming both people;
  - `MAX_DEPENDANTS` is 8: a household, not a village.
- **Consent:** a new `ConsentPurpose.PROXY_ACTIONS`, recorded against the **dependant** by the proxy when the
  link is made and withdrawn when it ends, with wording on the web and for USSD. `record_consent` gains an
  `actor` argument, because the person recording it is now sometimes another patient rather than staff.
- **The API** (`/api/v1/patients/me/dependants`): `GET` the list, `POST .../code` (send a code), `POST` (make
  the link), `DELETE /{link_id}` (end it). Joining and booking take `for_patient_id`; `GET /patients/me/appointments`
  and the cancel and move routes take it too, so a proxy reads and changes the dependant's bookings.
- **Notifications:** `reachable_patient` in the service and `answering_patient_id` in the preference gate.
  A dependant with no number is delivered to, and answered for by, their proxy — on the first attempt, the
  retry sweep and the fallback alike.
- **The pages:** `patient/_who.html` and `patient-dependants.js`, included by the join and booking pages,
  with the add-somebody panel (name, relationship, optional phone, code) and *Stop acting for them*.

## Design notes

**One gate, not a rule repeated in four routes.** `acting_for` is the only way a dependant is resolved, and
it refuses a link that does not exist, one that belongs to somebody else and one that has ended — the same
404 for the first two, so a patient cannot learn who else exists. Every route calls
`patient_or_dependant(db, caller, for_patient_id)` and gets back `(the patient seen, the proxy or None)`.

**Whose consent governs a dependant's message.** The message about a child's ticket rings on the parent's
phone. A child cannot agree to be messaged or set quiet hours, so the phone's owner answers for it: the
consent gate maps a **phone-less** dependant to their proxy, once, in `preferences.resolve`, which every send
path and retry goes through. A dependant who has their own number answers for themselves, like anyone.

**The abuse guard follows the phone.** `join_queue`'s per-number limit keyed on the patient's own phone, and a
dependant has none. It now keys on the phone that is doing the joining, so one number cannot take twenty
places by making twenty dependants.

**Revocation is a row, not a deletion.** The link stays with `revoked_at` set, so the tickets it explains are
still explicable. `acting_for` refuses from that moment, and the dependant's consent is withdrawn in the same
transaction.

**Two guards this PR does not weaken.** The board projection is untouched: it reads `ticket.patient_id`, so
the dependant's name shows under the dependant's own consent and the proxy never appears. And the
`patients.self` gate still admits only the signed-in patient; `for_patient_id` is checked against their own
links, never taken on trust.

## Changes

- **New:**
  - `src/modules/patients/proxy.py`, `src/database/models/patient_link.py`
  - `alembic/versions/0045_patient_links.py`
  - `src/templates/patient/_who.html`, `src/static/js/patient-dependants.js`
  - `tests/integration/patients/test_proxy_booking.py` (10)
- **Changed:**
  - Patients: `router.py` (four dependant routes), `schemas.py`, `consent.py` (`actor`), `consent_text.py`.
  - Models and enums: `patient.py`, `ticket.py`, `appointment_slot.py`; `src/commons/enums.py`
    (`ProxyRelationship`, `ConsentPurpose.PROXY_ACTIONS`).
  - Queue: `service.py` (`proxy=`, the guard's key), `router.py`, `schemas.py` (`for_patient_id`).
  - Appointments: `booking.py` (`proxy=`), `booking_router.py`, `schemas.py`.
  - Notifications: `service.py` (`reachable_patient`), `preferences.py` (`answering_patient_id`).
  - Web: `discover/join.html`, `discover/book.html`, `patient-join.js`, `patient-book.js`, `discover.css`.
  - Contracts: `contracts/queue.yaml` and `contracts/appointments.yaml` (`for_patient_id`).
- **Tests, updated:** `test_mutations_are_audited.py` — the code-sending route is listed with its reason (no
  link exists yet, and a row per number typed would itself be a log of who tried).
- **Docs:** the Issue 84 spec (files), `docs/OPS/PATIENT_APP_TESTING.md`, the M11 status row and bars
  (`--assume-closed 84`), the README Status block (80 of 111), and sprint 10's row.

## Testing

- [x] `ruff check .`, `ruff format --check .` and `mypy src/` (328 files) are clean.
- [x] **Migration:** `alembic check` reports "No new upgrade operations detected"; `downgrade 0044` and
  `upgrade head` both run cleanly, and 0045 ran on the demo database with #81 to #83's data in it.
- [x] **Full suite:** `TZ=UTC pytest tests/ -n auto --dist loadscope` on the Docker PostgreSQL 18 and Redis,
  with browsers, gave **2636 passed, 1 skipped, 9 xfailed**, with no failures.
- [x] **How to verify, on a running server.** Playwright drove the demo world on PostgreSQL
  (`clinicq_m11_demo` at 0045) in a phone-sized browser, as a parent with one phone:

  ```text
  1. added: ['Me', 'Lesedi (child)'] | Added. This visit is for them unless you change it above.
  2. ticket: T005
     ticket, who it belongs to, has no phone, who acted: ['T005|Lesedi|t|+27825553441']
  3. the desk calls: 200
     messages for the child, and whose phone they went to: ['ticket_leave_now -> +27825553441 sent']
  4. linked by code: ['Me', 'Lesedi (child)', 'Gogo (parent)']
     links: [child|verified=f|live=t, parent|verified=t|live=t]
  5. after ending: You no longer act for Lesedi (child). | ['Me', 'Gogo (parent)']
     the next action for them: 403 {'detail': 'You can no longer act for this person.', 'code': 'patients.link.revoked'}
  6. audit rows for the child:
      patient:01a0a797-090d… -> proxy_actions=granted via web
      patient:01a0a797-090d… -> link made: acts for this patient as their child (no phone of their own)
      patient:01a0a797-090d… -> joined Triage as T005 on behalf
      patient:01a0a797-090d… -> proxy_actions=withdrawn via web
      patient:01a0a797-090d… -> link ended: nobody acts for this patient through it any more
  ```

  - **Line 2** is the rule: the ticket is Lesedi's, Lesedi has no phone, and the number that acted is the
    parent's.
  - **Line 3**: the clinic runs a virtual waiting room, so the message about the child's ticket was the
    "time to leave" one — and it went to the parent's phone, and was **sent**, not suppressed. Before this
    PR it would have had nowhere to go.
  - **Line 6** is the whole trail for that child, and every row names the parent as the actor.
- [x] **Two bugs this demo found, that SQLite had not.** The consent row's audit was being written with the
  proxy's id in `actor_id`, which is a `varchar(36)` with a foreign key to `user`: a patient is not a user
  account. `record_consent` now takes an `actor` (the audit row's actor) separately from `recorded_by` (a
  staff user id). Both failures were PostgreSQL constraint errors that the SQLite suite cannot see.
- [x] **Over HTTP** (`test_proxy_booking.py`, 10 passed, with the real one-time-code store and a fake SMS
  provider):
  - **The code is the check:** finishing a link without a code is 422 and with a wrong code is 400, and no
    link exists either way.
  - **With the code:** the link is made, `verified_at` is set, the dependant's `proxy_actions` consent is
    recorded, and an audit row says "link made".
  - **A child with no phone:** the record has no number and the name the parent gave.
  - **Joining for a dependant:** the ticket is the dependant's, `proxy_patient_id` is the parent's, and every
    audit row for that child names the parent and says "on behalf".
  - **The messages:** the child's call is recorded against the child, sent to the parent's number, and not
    suppressed.
  - **Booking for a dependant:** the booking is the mother's; the proxy's own list is empty and
    `?for_patient_id=` shows hers.
  - **Ending it:** the next join is 403 `patients.link.revoked`, the list is empty, the ticket already taken
    stays, and the consent reads withdrawn.
  - **Either side may end it:** the dependant signs in and ends it themselves.
  - **Somebody else's dependant** and an invented id are the same 404 `patients.link.not_found`.
  - **Their own number** is 409 `patients.link.self`, and a ninth link is 409 `patients.link.too_many`.

| Adding a child with no phone | Who this visit is for | Linking a number, with its code | After ending the link |
|---|---|---|---|
| ![The add panel: their name, they are my child, phone left empty](https://github.com/Billykat7/clinicQ/blob/7b8046d03f06259335b139f0f300370c3b81a167/docs/GITHUB/PR/M11/assets/pr84/who-add-child-390.png?raw=true) | ![Who is this for? with Lesedi (child) chosen above the queue form](https://github.com/Billykat7/clinicQ/blob/7b8046d03f06259335b139f0f300370c3b81a167/docs/GITHUB/PR/M11/assets/pr84/who-picker-390.png?raw=true) | ![The add panel with a phone number and the code from that phone](https://github.com/Billykat7/clinicQ/blob/7b8046d03f06259335b139f0f300370c3b81a167/docs/GITHUB/PR/M11/assets/pr84/who-add-by-code-390.png?raw=true) | ![You no longer act for Lesedi (child), and the picker lists only Me and Gogo](https://github.com/Billykat7/clinicQ/blob/7b8046d03f06259335b139f0f300370c3b81a167/docs/GITHUB/PR/M11/assets/pr84/who-ended-390.png?raw=true) |

## Acceptance criteria

- [x] **A proxy can join a queue for a dependant, and the ticket belongs to the dependant:** `T005` is
  Lesedi's, with the parent recorded as who acted.
- [x] **Notifications reach the proxy's phone:** the message about the child's ticket was sent to the parent's
  number, under the parent's own consent and quiet hours.
- [x] **Every proxy action records who acted for whom, visible in the audit log:** five rows for that child,
  each naming the parent as actor and the child as the entity; the ticket and booking carry
  `proxy_patient_id` too.
- [x] **Revoking the link stops further proxy actions immediately:** 403 `patients.link.revoked` on the next
  attempt, from either side's revocation, with the earlier ticket untouched.
- [x] **The board shows the dependant under the site's display rules, never the proxy:** the projection reads
  `ticket.patient_id`, which is the dependant, and their own `display_name` consent. Unchanged by this PR,
  and #58's privacy sweep still passes.
- [x] **A patient cannot link themselves to an arbitrary phone number without a verification step:** the code
  goes to that number and nothing is written without it; their own number is refused outright.

## Risk and rollback

- **`patient.phone_e164` is now nullable.** Every existing patient has one; only a dependant created through
  a link has none, and the places that read a number handle its absence (the masked view, the abuse guard,
  the notification service).
- **`join_queue` and `booking.book` gain a `proxy` argument.** Every existing caller passes none.
- **One more consent purpose** appears on the patient's consent screen, answered only through a link.
- **Rollback:** revert, then `alembic downgrade 0044`. The links and the two proxy columns are dropped, and
  the column goes back to NOT NULL — a dependant created with no number must be removed first, which the
  migration's docstring says.

Closes #84
