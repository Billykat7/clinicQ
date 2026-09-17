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

> **The journey changed during the work, and this section is the one that was rewritten.** The first
> draft gave the setup link a token of its own: a single-use JWT reaching a clinic's rooms, hours and
> waiting-room board with **no account at all**. That is a second authentication surface into a
> clinic's configuration, built beside `StaffInvitation`, which already does this job — and the
> person setting a clinic up needs an account the next morning anyway to run its queue.
>
> So the link **is** the staff invitation (Issue 22), issued for `clinic_manager` at the new clinic.
> Following it creates the account it was always going to need and lands them on the checklist. The
> operator still sends one link and the clinic still does the rest; there is no new way in, every
> step is audited under a real person, and every write goes through the settings pages, the API and
> the site guard that already exist. The issue asked for "any other journey that would be better for
> this"; this is it.

- Migration `0048`: `site.setup_confirmed_details`, `site.setup_confirmed_board` and
  `site.setup_completed_at`. **No new table**, and no `site_setup_link`: the invitation row is the
  link.
- The checklist is **derived** from the clinic's own rows on every read — its rooms, its services,
  its hours, its staff invitations — so a step finished on a settings tab, through the API or by an
  operator shows as done without anything being told twice. The three columns above are the
  exceptions, each because no other row can answer its question: two steps are a *decision* (the
  details somebody else typed; what the board shows, which is `number_only` by default whether or
  not anyone has chosen), and `setup_completed_at` is *when the clinic said it was finished*.
- `GET /api/v1/sites/{site_id}/setup` — the checklist, `sites.onboarding:read` at `assigned`.
- `POST /api/v1/sites/{site_id}/setup/details` and `/setup/board` — the two confirms. No body: each
  means one thing.
- `POST /api/v1/sites/{site_id}/setup/submit` — `draft → pending_verification` through
  `onboarding.transition`, the only writer of `status`. Refused `422` **with the list of what is
  still unfinished**. The *decision* stays the operator's at `business` (Issue 221), so a clinic can
  ask and can never list itself.
- A **Setup** tab, first in the clinic's settings, gated on `sites.onboarding:update`.
- **Send the setup link** in `/admin/clinics`, which posts the existing staff invitation for
  `clinic_manager`.
- `docs/OPS/CLINIC_ONBOARDING.md`: the journey end to end, for whoever runs the pilot.

## Out of scope

- **A setup token of its own.** See the note above: it was specified, and deliberately not built.
- Editing the clinic's slug from the setup journey: the public handle stays the operator's.
- Uploading a logo or photographs.
- Payment profile and medical-aid schemes (Issue 37's own settings page).
- Automatic approval on submission. A platform admin still decides.

## Acceptance criteria

- [ ] An operator adds a clinic in `/admin/clinics` and sends a setup link; it arrives by email
- [ ] Following the link creates the manager's account and their role at that clinic, and lands them
      on the checklist
- [ ] The checklist reflects a change made on a settings tab, on the next render, with nothing told
      twice
- [ ] Each of the six steps opens the tab that finishes it, and comes back
- [ ] The board's display mode is `number_only` until the clinic itself changes it, and confirming
      the step changes no setting
- [ ] An incomplete clinic is refused with the list of what is missing, and nothing moves
- [ ] A complete clinic moves `draft → pending_verification` through `transition()`, appears in
      `/admin/verification`, and the move is audited under the manager's name
- [ ] A clinic cannot approve itself, cannot submit twice, and a verified clinic has nothing to
      submit
- [ ] Another clinic's setup is the guard's 404, and the front desk reaches none of it

## How to verify

1. `TZ=UTC pytest tests/unit/sites tests/integration/sites tests/e2e/onboarding`
2. `make migrate-up && make check`
3. Follow `docs/OPS/CLINIC_ONBOARDING.md` end to end against a local stack.

## Files touched

- `alembic/versions/0048_clinic_setup.py`
- `src/database/models/site.py`
- `src/modules/sites/setup.py`, `src/modules/sites/{router,schemas}.py`
- `src/web/dashboard/settings.py`, `src/templates/dashboard/settings_setup.html`
- `src/templates/admin/clinics.html`, `src/static/js/admin-clinics.js`
- `src/static/css/admin.css`
- `contracts/sites.yaml`
- `docs/OPS/CLINIC_ONBOARDING.md`

---

**Refs:** [M15 milestone](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) · [how to read this spec](../README.md)

Closes #223
