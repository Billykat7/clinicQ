# PR: The README's status is the progress bars, and M15 is in the table (Issue 232 / M15-232)

**Milestone:** [Milestone 15: Clinic Onboarding & Patient Sign-in](https://github.com/Billykat7/clinicQ/milestone/15) ·
**Issue:** [#232](https://github.com/Billykat7/clinicQ/issues/232) · **Builds on:** #229, #230, #231 —
the three issues whose bars this counts

`scripts/update_milestone_progress.py` writes each milestone's bar from **GitHub's own issue
states**, into three files, in the pull request that closes each issue, and
`make milestone-progress-check` fails CI when they disagree. That machinery works. Three things
around it did not.

## 1. M15 was not in the README's table at all

*Delivery at a glance* went 1 → 14 and then straight to the ⭐ roll-up. The milestone existed, its
issues were closed, the script wrote its bar into its own doc and into the milestone index — and the
README had no row for it to write into, so a reader of the README could not see that M15 existed.

```
| 14 | Production, Pilot & Go-live | 102–109, 197 | 13–14 | v0.14.0 | 🟩⬜⬜⬜⬜⬜⬜⬜⬜⬜ 11% (1/9 issues) |
+ | 15 | Clinic Onboarding & Patient Sign-in | 219–223, 229–232 | 15–16 | v0.15.0 | 🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 100% (9/9 issues) |
| ⭐ | First official release: every tracked issue closed | - | end of 14 | v1.0.0 | 🟩🟩🟩🟩🟩🟩🟩🟩⬜⬜ 75% (90/120 issues) |
```

## 2. The counts beside it were hand-typed, and stale

| Where | Said | Is |
|---|---|---|
| `README.md`, *Project documentation* | 14 milestones, 111 tracked issues | **15 milestones, 120 tracked issues** |
| `docs/GITHUB/README.md` | Total: 116 tracked issues across M1–M15 | **120** |

## 3. Status was eight paragraphs of prose restating the bars

About 1,400 words naming each milestone and what every issue in it delivered, with a tick-list under
it — the thing the bars were built to stop, and it had drifted exactly as you would expect:

- it said M11 **"has begun"**; M11 is finished;
- it said **"`v0.2.0` is the only tag cut so far"**; `git ls-remote --tags` says `v0.1.0`–`v0.9.0`
  and `v0.11.0` are all cut — which is how `0.11.0` came to be deployable at all;
- it said the notes for `v0.3.0`–`v0.9.0` were written but their tags uncut; both halves are wrong.

What replaces it is about 250 words of **what a bar cannot say**, opening by pointing at the bars
and saying where they come from:

- **Where the product is** — eleven of fifteen milestones finished, and what that means for a
  patient and for staff, in three sentences.
- **What is left** — M10, M12, M13, M14, and why each matters.
- **Tags** — which are cut, which release notes exist, and the one thing about them that keeps
  catching people: *a tag publishes an image; deploying it is a separate, manual decision.*
- A twelve-row ✅/⬜ table, one line per milestone group.

**Every claim is checked against the repository, not remembered:** the tags against
`git ls-remote --tags origin`, the release notes against `ls docs/GITHUB/RELEASES/`, the milestone
count against the roll-up line the script itself writes.

## Also

**`docs/GITHUB/MILESTONES/M15_…md` was written for five issues; there are nine.** Its table, header
rows, scope, order-of-work graph and exit criteria now carry 229–232, including the four exit
criteria they add:

- a browser signed in as staff **and** as a patient at once can write on both sides, and a leftover
  CSRF cookie never refuses a sign-in;
- a production deploy runs with no Environment secret and no variable set;
- signing in, signing up and resetting a password each have an address that can be linked to,
  reloaded and returned to;
- the README's status is the progress bars, M15 among them.

The milestone index's M15 row names the four new issues in its description column.

## Not done here, and not claimed

- **No change to `scripts/update_milestone_progress.py`.** It is not the thing that was wrong; it
  was writing correct bars into a file with no row to write them into.
- **No change to the plan.** No issue is added, moved or re-estimated.
- **The `v0.15.0` release note** is written when the milestone's last issue merges, not here.
- **The `?` rows in Status are not a second source of truth** — they group the bars, and the bars
  are what CI checks.

## Verification

- [x] `python scripts/update_milestone_progress.py --check --assume-closed 229,230,231,232` —
  **15 milestone(s): up to date**, so all three files agree with GitHub.
- [x] `git ls-remote --tags origin` → `v0.1.0`–`v0.9.0`, `v0.11.0`; `ls docs/GITHUB/RELEASES/` →
  notes for exactly those. Both match what Status now says.
- [x] `TZ=UTC pytest tests/unit tests/integration/public` — 1385 passed, 3 xfailed.
- [x] `ruff check .` clean.

## Acceptance criteria

- [x] ***Delivery at a glance* has a row for M15**, naming issues `219–223, 229–232`.
- [x] **`milestone-progress-check` passes** with the stack assumed closed.
- [x] **No hand-typed count disagrees with GitHub** — both corrected.
- [x] **Every factual claim in Status is checkable from the repository** — tags, notes and the
  milestone count all re-derived above.
- [x] **The M15 milestone doc describes nine issues**, and its order-of-work graph includes them.

## Risk and rollback

Documentation only: no code, no migration, no configuration. `milestone-progress-check` is what
keeps the bars honest afterwards, and it runs on every pull request.

Closes #232
