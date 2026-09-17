# PR: Approving a clinic no longer requires the grant that deletes clinics (Issue 221 / M15-221)

**Milestone:** [Milestone 15: Clinic Onboarding & Patient Sign-in](https://github.com/Billykat7/clinicQ/milestone/15) ·
**Issue:** [#221](https://github.com/Billykat7/clinicQ/issues/221) · **Builds on:** #18 (the RBAC catalog and
module manifests), #19 (the site guard), #29 (the onboarding state machine), all merged

A review before building M15's clinic console and setup link found that the verification routes — approve,
reject, ask for more, suspend — gate on `sites` + `delete`:

```python
SitesDelete = Annotated[None, Depends(require("sites", "delete", scope=GrantScope.BUSINESS))]

def decide_verification(..., _authz: SitesDelete) -> VerificationQueueItemOut:
```

Verbs are cumulative (`read < create < update < delete`), so that is the widest grant the resource has. Two
consequences, both wrong: nobody can be given authority to **check** clinics without also being given
authority to **remove** them, and `/admin/rbac` answers "who may approve a clinic?" with the delete grant.

With this PR:

- **`sites.onboarding` is its own resource** — *"whether a clinic is listed, and how far its own setup has
  got"* — and the verification queue and its decision gate on it.
- **A verification officer is expressible:** a grant on `sites.onboarding` at `business` without one on
  `sites` approves clinics and removes none. That grant could not be written before.
- **No decision moved.** The snapshot diff is 28 additions and **no removals** — every platform admin does
  exactly what it did before, through the cascade it already had.
- **A clinic can read where its own listing stands:** `GET /sites/{site_id}/onboarding`, site-scoped, so a
  manager sees their own clinic's status and the note the reviewing admin wrote *for them to read*, and
  another clinic's id is the guard's 404.

**Not done here, and not claimed:**

- **No `verification_officer` role is seeded.** This makes one expressible; who gets one is a decision for
  the pilot, and an operator can create it from `/admin/rbac` today.
- **The setup checklist itself** — rooms, services, hours, the board — is [Issue 223](https://github.com/Billykat7/clinicQ/issues/223).
  `SiteOnboardingOut` carries the listing state only; the checklist joins it there.
- **No change to the tier ladder, the site guard, or the patients manifest.** An email on a patient's own
  record ([Issue 219](https://github.com/Billykat7/clinicQ/issues/219)) is covered by `patients.self`, which
  already exists.

## Summary

- **The manifest** (`src/modules/sites/rbac_manifest.py`): a `sites.onboarding` child, and **no new grants**.
  The cascade already delivers every answer the routes need, and the snapshot proves it — a grant that moves
  no decision is noise in a catalog an operator has to trust. The comments now say which cascade reaches
  what, and why the split exists.
- **The API** (`src/modules/sites/router.py`):
  - `SitesOnboardingRead` / `SitesOnboardingUpdate` (`business` tier) replace `SitesDirectory` /
    `SitesDelete` on `GET /sites/verification` and `PUT /sites/{site_id}/verification`;
  - `DELETE /sites/{site_id}` keeps `sites:delete` — that one really is a removal;
  - `GET /sites/{site_id}/onboarding` → `SiteOnboardingOut` (`site_id`, `status`, `submitted_at`,
    `reviewed_at`, `review_note`, `visible_to_patients`, `can_submit_for_checking`), behind
    `require_site_access("sites.onboarding", "read")`.
- **The console** (`src/web/routes.py`): `/admin/verification/{section}` gates on
  `sites.onboarding:read @ business`, the same resource the API enforces, so the page and the API cannot
  drift apart.
- **The contract** (`contracts/sites.yaml`): the new path and `SiteOnboardingOut`, and the two verification
  descriptions corrected to name the grant they now ask for.
- **The generated artifacts:** `tests/snapshots/rbac_decisions.txt` and `docs/architecture/rbac-matrix.md`,
  regenerated from a database seeded exactly as a deployment is.
- **The spec** (`docs/GITHUB/ISSUES/M15/ISSUE_221_rbac_sites_onboarding.md`) corrected: it planned two
  explicit grants, and the snapshot showed both were redundant.

## How it was checked

- [x] **The grant that could not be written before.** Replacing `platform_admin`'s `sites:delete` with
  `sites.onboarding:update @ business` and asking the simulator:

  ```
  platform_admin, as shipped
    sites.onboarding:update  allow · business
    sites:delete             allow · business
  clinic_manager, as shipped
    sites.onboarding:update  allow · assigned
    sites:delete             deny  · assigned
  a verification officer: sites.onboarding:update @ business, and nothing on sites
    sites.onboarding:update  allow · business
    sites:delete             deny  · own
    sites.profile:update     deny  · own
  ```

- [x] **The same thing over real HTTP** (`test_deciding_a_listing_no_longer_needs_the_grant_that_deletes_clinics`):
  narrowed to `sites.onboarding:update`, the officer reads the queue (200), approves a clinic (200,
  `status: verified`) and is refused `DELETE /sites/{id}` (403).
- [x] **Nothing taken away** (`test_a_platform_admin_decides_and_removes_exactly_as_before`): the shipped
  grant still reads the queue (200), decides (200) and removes (204).
- [x] **The snapshot diff is additions only:** 28 new `sites.onboarding` lines, 0 removals, 0 moves — so no
  role's answer to any existing question changed.

  ```
  $ git diff tests/snapshots/rbac_decisions.txt | grep -c '^+resource'   → 28
  $ git diff tests/snapshots/rbac_decisions.txt | grep -c '^-resource'   → 0
  ```

- [x] **The clinic's own read** (`test_a_clinic_reads_where_its_own_listing_stands_and_what_the_admin_wrote`,
  `test_a_manager_reads_their_own_clinics_listing_and_no_other`): sent back with a note, the clinic's manager
  reads `pending_verification`, `visible_to_patients: false`, `can_submit_for_checking: false` and the note;
  the other clinic's id is 404.
- [x] **A manager still cannot decide** (`test_a_clinic_manager_reaches_onboarding_but_never_at_the_deciding_tier`):
  they hold `sites.onboarding:update` at `assigned`, the route asks for `business`, and the answer is 403.
- [x] **Cross-tenant:** `sites.onboarding` is now a real case in `tests/integration/security/test_cross_tenant.py`,
  not a `PENDING` entry — another clinic's `/onboarding` is 404 with the same bytes an id that never existed
  gets.
- [x] `make seed-rbac-check` — *in sync*; `tests/test_rbac_matrix.py`, the decision snapshot check, the
  contract suite and `tests/unit/security/test_api_route_gates.py` all pass.
- [x] `TZ=UTC pytest tests/unit tests/integration` green; `ruff check`, `ruff format --check` and `mypy src`
  (332 files) clean.

## Acceptance criteria

- [x] **`sites.onboarding` appears in the catalog, in `/admin/rbac`, and in the regenerated matrix** — the
  matrix gains one row: `| sites.onboarding | — | — | — | update · assigned | delete · business | — | — |`.
- [x] **Approving, rejecting, asking for more and suspending all resolve through `sites.onboarding:update`.**
- [x] **A principal holding `sites.onboarding:update` at `business` and not `sites:delete` can approve a
  clinic and is refused `DELETE /sites/{id}`.**
- [x] **A principal holding `sites:delete` at `business` can still do everything it could before** — the
  snapshot diff shows additions only.
- [x] **A clinic manager reads their own clinic's onboarding state and another clinic's id is a 404.**
- [x] **A clinic manager cannot decide their own clinic's listing** — `assigned`, and the routes ask
  `business`.
- [x] **`make seed-rbac-check`, the snapshot check and `tests/test_rbac_matrix.py` pass.**
- [x] **Every protected route still declares a gate.**

## Risk and rollback

- **No migration, no data change.** The catalog gains one resource row, applied by `make seed-rbac`, which
  the deploy sequence already runs after the migrations.
- **Deploy order matters once:** `seed-rbac` must run before the new build serves traffic, or
  `GET /sites/verification` and `PUT /sites/{id}/verification` resolve against a resource the catalog does
  not have yet. That is the existing sequence (decision 6 in `docs/GITHUB/ISSUES/README.md`), not a new step.
- **`seed-rbac` is additive and does not prune.** A deployment that seeded an earlier attempt at this issue
  would keep those rows; the two redundant grants were deleted by hand here before the snapshot was
  regenerated, and neither changes a decision either way.
- **Rollback:** revert. The `sites.onboarding` rows can stay — nothing reads them once the routes are back on
  `sites` — or be removed with
  `DELETE FROM clinicq.permissions WHERE resource = 'sites.onboarding';`.

Closes #221
