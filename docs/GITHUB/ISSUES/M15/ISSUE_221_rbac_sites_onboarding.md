# Issue 221: RBAC review — a `sites.onboarding` resource, and the grants the clinic journey needs

> **In short:** Approving a clinic currently requires the grant that **deletes** clinics. Give onboarding its own resource and its own grants before the console and the setup link are built on top of the wrong one.

| | |
|---|---|
| **Milestone** | [M15: Clinic Onboarding & Patient Sign-in](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) |
| **Sprint** | 15 (weeks 29–30) |
| **Owner** | A, Backend Lead (backup: E, DevOps/QA) |
| **Area** | Backend / RBAC |
| **Estimate** | 2 days |
| **Status** | Planned |
| **Depends on** | [Issue 18](../M3/ISSUE_18_rbac_roles_enforcement.md): the RBAC catalog and module manifests<br>[Issue 29](../M4/ISSUE_29_clinic_onboarding_verification.md): the onboarding state machine |
| **Unblocks** | [Issue 222](ISSUE_222_admin_clinics_console.md), [Issue 223](ISSUE_223_clinic_setup_link.md) |

## Context

A review of `src/modules/sites` against what M15 adds found one real defect and two gaps.

**The defect.** `PUT /api/v1/sites/{site_id}/verification` — approve, reject, ask for more, suspend —
is gated by `SitesDelete`, that is `sites` + `delete` at the `business` tier:

```python
# src/modules/sites/router.py
SitesDelete = Annotated[None, Depends(require("sites", "delete", scope=GrantScope.BUSINESS))]

def decide_verification(..., _authz: SitesDelete) -> VerificationQueueItemOut:
```

Verbs are cumulative (`read < create < update < delete`), so this is the widest grant the resource
has. Two consequences, both wrong:

- nobody can be given authority to **check clinics** without also being given authority to **remove**
  them from the directory. A verification officer is exactly the role a pilot needs, and it cannot be
  expressed;
- the audit trail and `/admin/rbac` describe an approval as an exercise of `sites:delete`, so the one
  place an operator goes to answer "who may approve clinics?" gives a misleading answer.

**The gaps.** There is no resource for a clinic's *setup* — the checklist, and the link that lets a
clinic complete it ([Issue 223](ISSUE_223_clinic_setup_link.md)) — and no grant that lets a
`clinic_manager` see how far their own clinic's setup has got. Both belong under onboarding.

Everything else in the sites manifest reviewed clean: the `profile` / `settings` / `display` / `staff`
/ `reports` / `audit` split does what it claims, the `assigned` vs `business` line is drawn in the
right place (a clinic manager reaches their clinic by id and holds nothing cross-clinic), and the
aliases (`SiteHoursRead = SiteProfileRead`, `SiteServicesUpdate = SiteProfileUpdate`) are deliberate
and documented. `patients.self` needs no new resource for [Issue 219](ISSUE_219_patient_email_sign_in.md):
an address is part of the record that grant already covers.

## Starting point

- `src/modules/sites/rbac_manifest.py`: the tree and its `RoleGrant`s.
- `src/modules/sites/router.py`: `SitesDirectory` / `SitesCreate` / `SitesDelete`, and the
  `require_site_access(...)` aliases.
- `src/core/rbac_manifest_registry.py`: `ALL_MANIFESTS`, one import and one tuple entry per module.
- `make seed-rbac` (idempotent, run after `migrate-up`), `make rbac-snapshot`, `make rbac-matrix`.
- `tests/test_rbac_matrix.py` fails when the matrix is stale; `scripts/generate_rbac_snapshot.py`
  writes the golden decision snapshot.

## Scope

- A `sites.onboarding` child in the sites manifest: *"Whether a clinic is listed, and how far its own
  setup has got."*
- `decide_verification` and `verification_queue` move onto it — `sites.onboarding` + `update` at
  `business` for the decision, `read` at `business` for the queue — so approving is no longer
  `sites:delete`. `DELETE /sites/{site_id}` keeps `sites:delete`; that one really is a removal.
- **No new grants.** The cascade already delivers the right answers, and the decision snapshot proves
  it: `platform_admin`'s `sites:delete` at `business` reaches `sites.onboarding` at `business`, which
  is what the verification routes ask for, and `clinic_manager`'s `sites:update` at `assigned` reaches
  it at `assigned`, which is what the setup link ([Issue 223](ISSUE_223_clinic_setup_link.md)) will ask
  for. A grant that changes no decision is noise in a catalog an operator has to trust, so none is
  added — the resource is the whole change.
- What the split buys is a grant an operator can now **hand out on its own**: `sites.onboarding`
  without `sites` approves clinics and deletes none. That is the verification officer a pilot wants,
  and it could not be expressed while approving *was* `sites:delete`.
- A clinic manager reaches `sites.onboarding` only at `assigned`, and the verification routes ask for
  `business`, so a manager still cannot decide their own clinic's listing. The setup link itself
  authenticates by its token, not by a role, exactly as a staff invitation does (Issue 22).
- The RBAC decision snapshot and `docs/architecture/rbac-matrix.md` regenerated, and the deltas
  explained in the PR description.

## Out of scope

- A `verification_officer` role. This issue makes one *expressible* (a grant on `sites.onboarding`
  without one on `sites`); seeding it is a separate decision with the pilot.
- Any change to the `own` / `assigned` / `business` tier ladder, or to the site guard (Issue 19).
- The patients manifest: `patients.self` already covers an email on a patient's own record.

## Acceptance criteria

- [ ] `sites.onboarding` appears in the catalog, in `/admin/rbac`, and in the regenerated matrix
- [ ] Approving, rejecting, asking for more and suspending a clinic all resolve through
      `sites.onboarding:update`, and the audit rows say so
- [ ] A principal holding `sites.onboarding:update` at `business` and **not** `sites:delete` can
      approve a clinic and is refused `DELETE /sites/{id}`
- [ ] A principal holding `sites:delete` at `business` — every platform admin today — can still do
      everything it could before this change: the snapshot diff shows additions only, no removals
- [ ] A clinic manager cannot decide their own clinic's listing: they hold `sites.onboarding` at
      `assigned` and the routes ask for `business`
- [ ] A clinic manager reads their own clinic's onboarding state and another clinic's id is a 404
- [ ] `make seed-rbac-check`, `make rbac-snapshot-check` and `tests/test_rbac_matrix.py` pass
- [ ] `tests/unit/security/test_api_route_gates.py` still declares a gate for every protected route

## How to verify

1. `make migrate-up && make seed-rbac && make rbac-snapshot && make rbac-matrix`
2. `TZ=UTC pytest tests/unit/security tests/integration/rbac tests/integration/sites tests/test_rbac_matrix.py`
3. `make check`

## Files touched

- `src/modules/sites/rbac_manifest.py`
- `src/modules/sites/router.py`
- `docs/architecture/rbac-matrix.md`, the RBAC decision snapshot

---

**Refs:** [M15 milestone](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) · [how to read this spec](../README.md)

Closes #221
