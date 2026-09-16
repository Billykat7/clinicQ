# Issue 222: The platform admin's clinics console — create, edit, archive

> **In short:** A clinic can be created, corrected and archived from the browser, not only from `curl`. The console the operator actually works in, beside the verification queue that already exists.

| | |
|---|---|
| **Milestone** | [M15: Clinic Onboarding & Patient Sign-in](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) |
| **Sprint** | 15 (weeks 29–30) |
| **Owner** | D, Frontend/Clinic (backup: A, Backend Lead) |
| **Area** | Frontend / Admin console |
| **Estimate** | 3 days |
| **Status** | Planned |
| **Depends on** | [Issue 23](../M4/ISSUE_23_sites_model_profile_crud.md): the sites API<br>[Issue 221](ISSUE_221_rbac_sites_onboarding.md): `sites.onboarding` |
| **Unblocks** | [Issue 223](ISSUE_223_clinic_setup_link.md): the setup link is sent from this console |

## Context

Every verb a clinic needs exists in the API — `POST /sites`, `GET /sites`, `GET/PUT /sites/{id}`,
`DELETE /sites/{id}` — and **none of them has a screen**. What a platform admin has today is
`/admin/verification`, which lists clinics that submitted themselves and approves or rejects them.
So:

- creating a clinic on behalf of one that phoned in, or fixing a typed address, means hand-writing
  JSON against the API with a session cookie;
- there is no list of *all* clinics anywhere in the product — the verification console is filtered to
  one listing status at a time, and the site switcher is built from a member's own assignments;
- the user journey "add the clinic now, let them finish the details later"
  ([Issue 223](ISSUE_223_clinic_setup_link.md)) has nowhere to start from.

The console toolkit this needs is already built and used by four other admin screens.

## Starting point

- `src/modules/sites/router.py`: `list_sites` (paged, narrowed to the caller's scope), `create_site`
  (always starts a `draft`, whatever the caller asks), `update_site`, `delete_site` (soft).
- `src/modules/sites/service.py` and `schemas.py`: `SiteIn` / `SiteOut`, `SlugAlreadyUsedError` (409).
- `POST /sites/geocode`: address → coordinate, with the operator asked for it when the service is
  unavailable (`GeocodingUnavailableError`).
- `src/templates/admin/verification.html` and `src/web/routes.py:408` — the pattern to follow, down to
  `docs/IDE/RULES/list-view-ui-pattern.mdc`: a tab per URL, a collapsible filter bar with remembered
  state, click-to-sort columns, a slideover beside the list rather than a navigation.
- `src/static/js/admin-crud.js`: the kernel's console toolkit.
- `src/core/nav_registry.py`: a destination must be declared here to be navigable and simulable.

## Scope

- `/admin/clinics`, tabbed by listing status (`all` · `draft` · `pending` · `verified` · `suspended`),
  each its own URL, with the bare path redirecting to `all`.
- Filter by name, suburb, city, province and sector; sortable columns; a row opens a slideover showing
  the clinic, its contact, its setup state and its links (public page, dashboard, verification).
- **New clinic**: name, slug, sector, address (geocoded, with the coordinate editable when geocoding
  is unavailable), province, contact person. It is created a `draft` — invisible to patients — with
  the default queues and services catalogue, exactly as a self-registration is.
- **Edit**: the profile fields, with the 409 on a taken slug shown in the operator's own words.
- **Archive**: the soft delete, behind a confirm naming the clinic, with what it does spelled out
  (it leaves the directory; its tickets and audit rows are kept).
- Every mutation is the existing API call, gated as it is gated today; the page hides an action the
  caller may not take with `can(resource, verb)` as a courtesy, never as the control.
- A nav entry under the admin group, and a breadcrumb.

## Out of scope

- The verification decision itself: `/admin/verification` keeps it, and this console links across.
- Editing hours, queues, services, display settings or the payment profile — those are the clinic's
  own dashboard settings pages, and the setup link ([Issue 223](ISSUE_223_clinic_setup_link.md)) is how
  a new clinic reaches them.
- Bulk import of clinics from a spreadsheet.
- Un-archiving. A soft-deleted clinic is restored by an operator with database access, as today.

## Acceptance criteria

- [ ] A platform admin creates a clinic from the browser; it is a `draft`, has the default queues and
      catalogue, and is invisible on `/discover`
- [ ] The listing pages, filters and sorts work, and each tab is its own URL that survives a refresh
- [ ] Editing a clinic's address re-geocodes it; with geocoding unavailable the operator is asked for
      the coordinate and the save still succeeds
- [ ] A taken slug is refused with the sentence the API gives, and nothing is created
- [ ] Archiving asks for confirmation naming the clinic, and the clinic then appears nowhere a patient
      can reach
- [ ] A `clinic_manager` who opens `/admin/clinics` is refused, and no nav entry is shown to them
- [ ] Every mutation writes its audit row
- [ ] The console is keyboard-navigable and fits a 1024 px screen

## How to verify

1. `TZ=UTC pytest tests/integration/sites tests/e2e/admin`
2. `make check`
3. Create, edit and archive a clinic at `/admin/clinics` as a platform admin.

## Files touched

- `src/web/routes.py`
- `src/templates/admin/clinics.html`
- `src/static/js/admin-clinics.js`
- `src/core/nav_registry.py`

---

**Refs:** [M15 milestone](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) · [how to read this spec](../README.md)

Closes #222
