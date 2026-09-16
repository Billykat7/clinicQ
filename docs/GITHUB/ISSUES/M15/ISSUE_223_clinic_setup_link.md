# Issue 223: The clinic setup link — add a clinic now, let them finish it themselves

> **In short:** An operator adds a clinic and sends one link. The clinic follows it — no account needed — and completes its own rooms, services, hours and board settings against a checklist, then submits itself for verification.

| | |
|---|---|
| **Milestone** | [M15: Clinic Onboarding & Patient Sign-in](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) |
| **Sprint** | 16 (weeks 31–32) |
| **Owner** | A, Backend Lead · D, Frontend/Clinic |
| **Area** | Backend + Frontend / Onboarding |
| **Estimate** | 4 days |
| **Status** | Planned |
| **Depends on** | [Issue 22](../M3/ISSUE_22_staff_invitations_account_settings.md): the single-use invitation link<br>[Issue 29](../M4/ISSUE_29_clinic_onboarding_verification.md): the onboarding state machine<br>[Issue 221](ISSUE_221_rbac_sites_onboarding.md): `sites.onboarding`<br>[Issue 222](ISSUE_222_admin_clinics_console.md): where the link is sent from |
| **Unblocks** | Nothing in this milestone. The pilot rollout kit (Issue 106) uses it. |

## Context

Onboarding today has exactly two shapes and a hole between them.

1. **The clinic finds us**: `/register-clinic` → `POST /sites/register` → a `pending_verification`
   clinic with default queues and a default catalogue → a platform admin approves it.
2. **The operator types it in**: `POST /sites` → a `draft`.

In both, the clinic's *own* configuration — which rooms it runs, whether it has a pharmacy, what
hours it keeps, what the waiting-room board may show — is either left at the defaults or done later
by whoever gets invited as the first manager, in a settings area they have to be told to find. Nobody
is told what is still missing. A clinic that goes live on defaults shows patients queues it does not
run and hours it does not keep, which is worse than not being listed.

What the pilot needs is the middle: **add the clinic now, send one link, let them finish it.** The
staff invitation (Issue 22) is the pattern — the row is the authority and the link is a typed JWT
carrying only its id, so the deadline and what it opens are read from the row when it is used, and a
used row is refused however valid the signature still looks.

## Starting point

- `src/database/models/staff_invitation.py`: the row-is-the-authority pattern, `expires_at`,
  `accepted_at`, `accepted_user_id`, kept after use.
- `src/modules/staff/invitations.py` and `src/core/email_send.py:send_staff_invitation_email`.
- `src/modules/sites/onboarding.py`: `ALLOWED_TRANSITIONS` and `transition()`, the only writer of
  `site.status`; `submit_registration` already calls `create_default_queues` and
  `seed_default_catalogue`.
- What the clinic must fill in, all of which has an API and a dashboard page already:
  queues/rooms (`src/modules/queues`), services incl. pharmacy (`src/modules/sites/catalogue.py`),
  hours and closures (`hours_service.py`), display and privacy (`settings.py`), the first staff
  invitation (`src/modules/staff`).
- `src/templates/dashboard/settings_*.html`: the pages the wizard reuses rather than reinvents.

## Scope

- Migration `0048`: `site_setup_link` (`id`, `site_id`, `email`, `phone_e164` nullable, `issued_by`,
  `expires_at`, `accepted_at`, `completed_at`, `revoked_at`, `created_at`), and
  `site.setup_completed_at` on the clinic.
- `SETUP_LINK_EXPIRE_HOURS`, default `168` (a week — a clinic manager is not at a desk, and a weekend
  must not expire it), beside `STAFF_INVITE_EXPIRE_HOURS` in `Settings`.
- Issue, list and revoke a setup link: `POST/GET/DELETE
  /api/v1/sites/{site_id}/setup-links[/{id}]`, gated `sites.onboarding` + `update`, so a platform
  admin can send one for any clinic and a clinic manager can re-send their own. Sent by email, and by
  SMS when a number is given.
- `GET /setup/{token}`: the clinic's own setup journey, authenticated **by the token alone** — no
  account, no session, no role. It reaches exactly one clinic's setup and nothing else: not another
  clinic, not its tickets, not its patients, not its audit trail.
- The journey, as a checklist a clinic can leave and come back to, each step saved on its own:
  1. **Confirm the clinic** — name, address on the map, contact person;
  2. **Rooms** — the default queues, renamed, removed or added to (a room *is* a queue, Issue 25);
  3. **Services** — the default catalogue, with pharmacy / dispensary kept or removed, and expected
     minutes per service;
  4. **Hours** — the weekly schedule and public-holiday rule;
  5. **The waiting-room board** — display mode, language, theme, announcements. Created
     `number_only` and only ever changed *here*, by the clinic, never defaulted to anything else
     (non-negotiable 4);
  6. **Who runs it** — the first `clinic_manager` invited, which is the existing staff invitation;
  7. **Submit for checking** — `draft → pending_verification` through `transition()`, never by a
     direct write.
- The checklist's state is derived from the clinic's own rows, not a column per step, so a change made
  from the dashboard shows up here and nothing can disagree.
- A link is single-use in the sense that matters: `accepted_at` on first open, refused once revoked,
  expired or once the clinic is `verified`; re-openable until then by the person who holds it.
- `/admin/clinics` shows each clinic's setup state and offers **Send setup link** / **Re-send** /
  **Revoke**, with who it went to and when.
- Every step writes an audit row whose actor is the setup link (`setup-link:<id>`), so what a clinic
  configured for itself is attributable without inventing an account for it.
- `docs/OPS/CLINIC_ONBOARDING.md`: the journey end to end, for the person running the pilot.

## Out of scope

- Editing the clinic's slug from the setup journey: the public handle stays the operator's.
- Uploading a logo or photographs.
- Payment profile and medical-aid schemes (Issue 37's own settings page).
- A setup link that can invite more than one staff member, or assign rooms to them (Issue 28's page,
  once the first manager has an account).
- Automatic approval on submission. A platform admin still decides.

## Acceptance criteria

- [ ] An operator adds a clinic in `/admin/clinics` and sends a setup link; it arrives by email, and
      by SMS when a number was given
- [ ] Following the link opens the clinic's setup with no account and no sign-in, and completes all
      seven steps; a step saved is still saved after closing and re-opening the link
- [ ] The link reaches **only** that clinic's setup: another clinic's id inside the journey is a 404,
      and the token opens no ticket, patient, report or audit row
- [ ] An expired, revoked or already-completed link says so plainly and offers no form
- [ ] The board's display mode is `number_only` until the clinic itself changes it in step 5
- [ ] Submitting for checking moves `draft → pending_verification` through `transition()`, puts the
      clinic in `/admin/verification`, and the checklist is complete
- [ ] Every step's change is audited with the setup link as the actor
- [ ] The checklist reflects a change made from the clinic dashboard instead of the link
- [ ] The journey works on a 320 px screen and is completable by keyboard

## How to verify

1. `TZ=UTC pytest tests/unit/sites tests/integration/sites tests/e2e/onboarding`
2. `make migrate-up && make check`
3. Follow `docs/OPS/CLINIC_ONBOARDING.md` end to end against a local stack.

## Files touched

- `alembic/versions/0048_site_setup_link.py`
- `src/database/models/{site_setup_link,site}.py`
- `src/modules/sites/{setup,onboarding,router,schemas}.py`
- `src/web/setup.py`, `src/templates/setup/*.html`, `src/static/js/clinic-setup.js`
- `src/core/{config,email_send}.py`
- `src/templates/admin/clinics.html`
- `docs/OPS/CLINIC_ONBOARDING.md`

---

**Refs:** [M15 milestone](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) · [how to read this spec](../README.md)

Closes #223
