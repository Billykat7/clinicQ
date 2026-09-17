# Issue 232: The README's status, from the milestones and their progress bars

> **In short:** *Delivery at a glance* has no M15 row at all, the docs table still says "14 milestones, 111 tracked issues", and the Status section is eight paragraphs of prose that has to be rewritten by hand every time an issue closes — so it is wrong.

| | |
|---|---|
| **Milestone** | [M15: Clinic Onboarding & Patient Sign-in](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) |
| **Sprint** | 16 (weeks 31–32) |
| **Owner** | E, DevOps/QA (backup: A, Backend Lead) |
| **Area** | Docs |
| **Estimate** | ½ day |
| **Status** | Planned |
| **Depends on** | [229](ISSUE_229_csrf_sign_in_lockout.md), [230](ISSUE_230_deploy_on_self_hosted_runner.md), [231](ISSUE_231_auth_pages_not_modal.md): the issues whose bars this counts |
| **Unblocks** | The `v0.15.0` release note. |

## Context

`scripts/update_milestone_progress.py` writes each milestone's bar from **GitHub's own issue
states**, into three places — the milestone doc, the README's *Delivery at a glance* table and
`docs/GITHUB/README.md` — and `make milestone-progress-check` fails CI when they disagree. That
machinery works. Three things around it do not.

**M15 is not in the README's table.** The milestone exists, its issues are closed, the script writes
its bar into its own doc and into the milestone index, and the README's table has no row for it to
write into. A reader of the README cannot see that the milestone exists at all.

**The counts beside the table are hand-typed and stale.** "14 milestones, 111 tracked issues" in the
docs table; "Total: 116 tracked issues" in the milestone index. There are 15 milestones and 120
tracked issues.

**And the Status section is prose.** Eight paragraphs, about 1,400 words, restating what the bars
already say — which milestones are done and what each issue delivered, sentence by sentence, with a
tick-list under it. It was accurate when it was written. It says M11 "has begun" (it is finished),
it lists tags as uncut that have been cut (`v0.1.0`–`v0.9.0` and `v0.11.0` all exist), and it has to
be edited by hand on every merge, which is exactly the thing the bars were built to stop.

## Starting point

- `scripts/update_milestone_progress.py`: the three places it owns, and `--assume-closed`.
- `README.md`: *Delivery at a glance* (rows 1–14, and the ⭐ roll-up), *Project documentation*, and
  the *Status* section.
- `docs/GITHUB/README.md`: the milestone summary table, the total and the roll-up line.
- `docs/GITHUB/MILESTONES/M15_…md`: written for five issues; there are nine.
- `docs/GITHUB/RELEASES/`: which notes actually exist, and `git ls-remote --tags` for which tags do.

## Scope

- An **M15 row** in *Delivery at a glance*, so the script has somewhere to write and a reader can
  see the milestone.
- The hand-typed counts corrected, in both files.
- The Status section **replaced by what a bar cannot say**: where the product is, what is left, and
  which tags are cut — in about 250 words, each claim checked against the repository rather than
  remembered. It opens by pointing at the bars and saying where they come from.
- The M15 milestone doc extended to its nine issues: the table, the header rows, the scope, the
  order-of-work graph and the exit criteria.

## Out of scope

- Any change to `scripts/update_milestone_progress.py`. It is not the thing that is wrong.
- The milestone plan itself: no issue is added, moved or re-estimated here.
- The `v0.15.0` release note, which is written when the milestone's last issue merges.

## Acceptance criteria

- [ ] *Delivery at a glance* has a row for M15, naming issues `219–223, 229–232`
- [ ] `make milestone-progress-check ARGS='--assume-closed …'` passes — the bars in all three places
      agree with GitHub
- [ ] No hand-typed milestone or issue count in either README disagrees with GitHub
- [ ] Every factual claim in Status is checkable from the repository: the tags against
      `git ls-remote --tags`, the release notes against `docs/GITHUB/RELEASES/`, the milestone count
      against the roll-up the script writes
- [ ] The M15 milestone doc describes nine issues, and its order-of-work graph includes them

## How to verify

1. `make milestone-progress-check ARGS='--assume-closed 229,230,231,232'`
2. `git ls-remote --tags origin` and `ls docs/GITHUB/RELEASES/` against what Status claims
3. `make check`

## Files touched

- `README.md`
- `docs/GITHUB/README.md`
- `docs/GITHUB/MILESTONES/M15_clinic_onboarding_patient_sign_in.md`

---

**Refs:** [M15 milestone](../../MILESTONES/M15_clinic_onboarding_patient_sign_in.md) · [how to read this spec](../README.md)

Closes #232
