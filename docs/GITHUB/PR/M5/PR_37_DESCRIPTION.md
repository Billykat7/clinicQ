# PR: Filter private clinics by the payment methods and medical aids they report (Issue 37 / M5-37)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#37](https://github.com/Billykat7/clinicQ/issues/37) · **Builds on:** #23 (sites, merged),
#32 (PR #149), and #31, #34, #35, #36 in the stack (PRs #147, #148, #150, #151)

> **Merge order:** after PRs #147–#151. This branch is stacked on them, so the Conventions check fails
> on their commits until they merge. Every test job passes.
>
> **Who did what:** F owns the issue (the profile, its rules and its data); C's part is the filter on
> the discovery page. Both halves are in this PR, split along that line in the commits.

Sending a patient to a private clinic that will not take their scheme is worse than not filtering at
all. So this is explicitly a **self-reported directory tag, not a claims integration**. A private
clinic declares whether it takes cash and cards and which medical schemes it accepts. Patients can
filter by that **under Private only**, and every place the data appears carries *Reported by the
clinic. Please confirm with the clinic before you travel.* Nothing checks anyone's membership, benefit
or claim; that is [backlog item 1](../../ISSUES/BACKLOG/BACKLOG_01_medical_aid_integration.md). It
all ships behind `PAYMENT_FILTER_ENABLED`, off by default, so it can go live after the MVP with a
configuration change.

## Summary

- **The profile** (migration `0016`): cash, card, accepted schemes from a controlled list of 15 South
  African schemes plus *Other* with its name typed, a co-payment notice, and when it was last
  confirmed. Saving confirms it; *Still correct* re-confirms it unchanged.
- **Only a private clinic can hold one, enforced on the server.** A public clinic's `PUT` or
  `confirm` is a `409` whatever the client sends, and a clinic whose sector changes to public loses
  its profile in the same request.
- **Stale after six months.** A profile not confirmed in six calendar months carries a *Not confirmed
  by the clinic since …* tag on the card and `stale: true` in the APIs.
- **The filter exists only under Private.** It sits in a slot that the results fragment swaps out of
  band: under Public or All the slot is empty, so its controls are not in the page at all. The API
  refuses a payment filter outside Private with a `422` that says why.
- **The notice everywhere:** above the filter, on each card, on the detail page, in
  `/api/v1/clinics/nearby`, in `/api/v1/clinics/{slug}` and in the clinic-side API.
- **A clinic-side editor** at `/dashboard/sites/{site_id}/settings/payment` for the manager, which
  explains, and shows no form, for a public clinic.
- **Behind a flag.** With `PAYMENT_FILTER_ENABLED` off, no patient-facing surface shows or filters by
  payment and the editor page is not served. The sites API still accepts a profile, so clinics can
  fill theirs in before launch.

## Design notes

**Rows, not a list column.** Each accepted scheme is a `site_payment_medical_aid` row with a
`(scheme, site_id)` index, so "private clinics that accept Bonitas" is an indexed `EXISTS` inside the
same `nearby_statement()` the GiST index already serves. Schemes combine with **or**: a patient who
ticks Bonitas and GEMS wants a clinic that takes either.

**Why the server, twice.** A check constraint cannot see the site's sector, so the rule "only private
clinics" lives in `src/modules/sites/payment_profile.py`: `save_profile` and `confirm_profile` refuse
a public clinic, and `remove_if_public` runs inside `PUT /api/v1/sites/{site_id}` when the sector
changes. Discovery repeats the rule in its read (`published_profiles` joins on `sector = private`), so
even a stray row could not surface on a public clinic.

**"Absent, not hidden" on a page that swaps with htmx.** The filter form's radios swap `#results`,
so a hidden `<fieldset>` would still be in the DOM under Public. Instead the fieldset is rendered into
`#payment-filter-slot`, and `_results_swap.html` sends that slot back with `hx-swap-oob`. Moving to
Private fills it; moving away empties it. The browser run counts the inputs: 0 under All, 17 under
Private, 0 under Public, with no reload. A payment choice left over from Private is dropped by the page
(it has no filter there) and by `page_href`, so it never lingers in the address; the JSON API, which
has no page to fall back on, refuses it instead.

**Staleness in calendar months.** `is_stale` compares the Johannesburg date with six calendar months
earlier, clamped to the end of a shorter month (31 August back to 28 February). A profile confirmed
on 13 March is current on 13 September and stale on the 14th (unit-tested).

**The words live in one place.** `CLINIC_REPORTED_NOTICE` moves from the detail page code (#35) into
the payment module and is served by the API, so the editor shows the manager the sentence patients
will see.

**Two fixes found by the accessibility run.**

- **`discover.css` was broken from #32.** The edit that turned the filter selects into radio groups
  had left the results and card rules duplicated inside an **unclosed** `@media (max-width: 22rem)`.
  The first copy still applied, so #32's and #35's screens looked right, but every rule appended after
  that point only applied below 352 px. This PR's checkbox sizes silently did nothing, and axe reported
  a 13 px touch target. The duplicate is removed on the #32 branch
  (`Issue 32: Remove a duplicated block…`) and merged forward through #35 and #36; braces now balance.
- **Touch targets:** scheme checkboxes are 24 px, with a row gap, and axe is clean.

**Kernel CSS touched:** `admin.css` gains a `.check-grid` column layout and drops the browser's
default frame on `fieldset.field`. The display settings page (#27) uses `fieldset.field` too and
loses the same frame.

**Out of scope:** any claims, eligibility or billing check (backlog), and public clinics, which never
carry a profile.

## Changes

- **`alembic/versions/0016_site_payment_profile.py`** and **`src/database/models/site_payment_profile.py`**
  (new): `SitePaymentProfile`, `SitePaymentMedicalAid`.
- **`src/modules/sites/payment_profile.py`** (new): `save_profile`, `confirm_profile`,
  `remove_if_public`, `published_profiles`, `is_stale`, `SCHEME_LABELS`, `CLINIC_REPORTED_NOTICE`.
- **`src/modules/sites/router.py`:** `GET`/`PUT /{site_id}/payment-profile`,
  `POST /{site_id}/payment-profile/confirm`, and profile removal on a change to public.
  **`schemas.py`:** `PaymentProfileIn`/`Out`. **`contracts/sites.yaml`:** the three routes and schemas.
- **`src/commons/enums.py`:** `MedicalAidScheme`. **`src/core/config.py`:** `PAYMENT_FILTER_ENABLED`;
  **`.env.example`** regenerated.
- **`src/modules/discovery/service.py`:** `PaymentFilter`, `PaymentFilterRefusedError`, the filter in
  `nearby_statement`, `NearbyClinic.payment`. **`profile.py`**, **`schemas.py`**, **`router.py`:**
  payment on results and profiles; `accepts_cash`, `accepts_card` and `medical_aid` on `/nearby`.
- **`src/web/discover.py`:** `PaymentFilterView`, `PaymentLines`, payment carried through the page,
  the URL and the swap. **`src/templates/discover/`:** `_payment_filter.html`, `_results_swap.html`
  (new); `list.html`, `_clinic_card.html`, `detail.html`. **`src/static/css/discover.css`**.
- **`src/web/routes.py`**, **`src/templates/dashboard/settings_payment.html`**,
  **`src/static/js/site-payment-profile.js`** (new): the editor. **`src/static/css/admin.css`**.
- **Tests (new, 25):** `tests/integration/discovery/test_payment_filter.py`,
  `tests/integration/sites/test_payment_profile_api.py`, `tests/unit/sites/test_payment_profile_rules.py`.
  **`tests/integration/security/test_cross_tenant.py`:** cases for both models.
  **`tests/integration/discovery/test_clinic_detail.py`:** the payment section now depends on the flag.

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (225 files); the template
      punctuation check clean; the contract drift test passes with the new routes.
- [x] Full suite with PostgreSQL and Redis required, in UTC: **1620 passed, 9 xfailed**, twice.
      One earlier run failed `test_a_public_clinic_cannot_hold_a_payment_profile…` under xdist:
      the M4 fixture's clinics take their sector from the factory's position in the demo list, so a
      test run after others could start with a *private* Clinic A. The test now sets the sector it
      needs.
- [x] **Browser run** on the migrated, seeded `clinicq_m5_verify` database with
      `PAYMENT_FILTER_ENABLED=true`, driven by Playwright on the system Chrome. The demo manager was
      assigned to Medicross Randburg, whose profile was seeded as card and GEMS, confirmed 213 days
      earlier. Transcript:

```text
== The clinic declares it (manager@clinicq.example, PAYMENT_FILTER_ENABLED=true)
  sign in: 200
  PUT a payment profile for Hillbrow CHC (public): 409 Only a private clinic can list payment methods and medical aids. Public clinics never carry a payment profile.
  editor for Hillbrow CHC: Public clinics do not list payment methods or medical aids. This is decided by the clinic's sector, not by this page. | form hidden: True
  editor for Medicross Randburg, before: Last confirmed 12 February 2026. Patients see this as not confirmed in six months.
axe editor (dashboard): 0 violations, 36 rules passed
  saved: Saved and confirmed. | Last confirmed 13 September 2026.

== The patient's list
  All:     payment inputs in the page = 0
  Private: payment inputs in the page = 17 | no reload: True | notice: Payment methods and medical aids are reported by each clinic, not checked by ClinicQ. Please confirm with the clinic before you travel.
  Bonitas ticked -> /discover?lat=-26.2&lon=28.02&sector=private&radius_m=20000&medical_aid=bonitas | ['Randburg Medicross']
  card says: Takes cash and card. Medical aids: GEMS, Bonitas, Umvuzo Health. Reported by the clinic. Please confirm with the clinic before you travel.
axe discover under Private with the filter: 0 violations, 47 rules passed
  Public:  payment inputs in the page = 0 | slot children: 0 | URL: /discover?lat=-26.2&lon=28.02&sector=public&radius_m=20000 | no reload: True
  API, sector=public&medical_aid=bonitas: 422 Payment and medical-aid filters apply only to private clinics. Choose Private to use them.

== Detail page: Payment and medical aid | Reported by the clinic. Please confirm with the clinic before you travel. | Takes cash and card | Medical aids: GEMS, Bonitas, Umvuzo Health | A co-payment may apply for some plans.
axe detail page with payment: 0 violations, 36 rules passed
```

- [x] **Screenshots** from that run (390 px at 2x). The dashboard's fixed header and rail appear
      mid-image in the full-page editor captures; that is how a full-page screenshot draws fixed
      elements, not the page.

      Under Private with Bonitas ticked (the notice above the filter, the card's reported payment),
      and under Public, where the filter is gone:

      | Private | Public |
      |---|---|
      | ![Discovery under Private with the payment filter and a card showing reported payment](https://github.com/Billykat7/clinicQ/blob/c44a779304d756727c3313522c1cfafa80e2f479/docs/GITHUB/PR/M5/assets/pr37/private-filter-light.png?raw=true) | ![Discovery under Public with no payment filter](https://github.com/Billykat7/clinicQ/blob/c44a779304d756727c3313522c1cfafa80e2f479/docs/GITHUB/PR/M5/assets/pr37/public-no-filter-light.png?raw=true) |

      The clinic editor: stale before, saved and confirmed after, and for a public clinic:

      | Stale | Saved | Public clinic |
      |---|---|---|
      | ![The payment editor showing a profile not confirmed in six months](https://github.com/Billykat7/clinicQ/blob/c44a779304d756727c3313522c1cfafa80e2f479/docs/GITHUB/PR/M5/assets/pr37/editor-stale.png?raw=true) | ![The payment editor after saving](https://github.com/Billykat7/clinicQ/blob/c44a779304d756727c3313522c1cfafa80e2f479/docs/GITHUB/PR/M5/assets/pr37/editor-saved.png?raw=true) | ![The payment editor for a public clinic, with no form](https://github.com/Billykat7/clinicQ/blob/c44a779304d756727c3313522c1cfafa80e2f479/docs/GITHUB/PR/M5/assets/pr37/editor-public.png?raw=true) |

      The detail page's section, with the notice first:

      ![The clinic detail page's payment and medical aid section](https://github.com/Billykat7/clinicQ/blob/c44a779304d756727c3313522c1cfafa80e2f479/docs/GITHUB/PR/M5/assets/pr37/detail-payment.png?raw=true)

## Acceptance criteria

- [x] **Payment filters are completely hidden when the Public sector is selected.** They are absent,
      not hidden: `test_the_payment_filter_is_absent_unless_private_is_selected` (no filter view under
      Public or All, and a leftover choice dropped from the URL), and in the browser 0 inputs and 0
      children in the slot under Public. `test_the_api_refuses_a_payment_filter_outside_private` shows
      the server refusing one.
- [x] **Medical-aid data is displayed with a clinic-reported disclaimer everywhere it appears.**
      `test_payment_data_carries_the_clinic_reported_notice_everywhere_it_appears`: the card, the
      detail page, the nearby API and the profile API. The filter carries its own notice, and the
      editor shows the manager the sentence.
- [x] **Only private clinics can hold a payment profile, enforced server-side.**
      `test_a_public_clinic_cannot_hold_a_payment_profile_whatever_the_client_sends` (409, nothing
      stored) and `test_a_clinic_that_becomes_public_loses_its_profile`. The transcript's `PUT` shows
      the same over the wire.
- [x] **A profile not confirmed in six months is marked stale in the UI.**
      `test_a_profile_not_confirmed_in_six_months_is_marked_stale` (card tag and API flag) and
      `test_a_profile_not_confirmed_in_six_months_is_stale_until_reconfirmed`; the editor screenshot
      shows the stale state before saving.
- [x] **Scheme tags come from a controlled list with a free-text 'other' option.** `MedicalAidScheme`
      closes the list; `other` requires its name and a name requires `other`
      (`test_the_request_keeps_other_honest_and_the_list_closed`); *Umvuzo Health* is stored and shown
      by name.
- [x] **The feature is behind a flag so it can ship after the MVP without a code change.**
      `test_with_the_flag_off_nothing_patient_facing_shows_or_filters_by_payment` and
      `test_the_editor_page_is_served_only_with_the_feature_switched_on`. `PAYMENT_FILTER_ENABLED` is
      in `.env.example`.

## Risk and rollback

**Migration `0016`** adds two tables and changes nothing existing, and it is reversible. With the
flag off (the default) nothing a patient sees changes. The only effect is the sites API accepting a
profile and removing one when a clinic turns public. The `discover.css` repair changes no rule that
applied before. Rollback is a revert; #33 and #38 are stacked on this.

**Follow-ups:** the scheme list should be reviewed against the Council for Medical Schemes register
each year (F); a profile's staleness could prompt the clinic manager (Issue 63's notifications); the
demo seed has no payment profiles, so a demo with the flag on needs one entered through the editor.

Closes #37
