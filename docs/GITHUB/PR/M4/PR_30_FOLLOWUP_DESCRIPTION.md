# PR: Bring the progress bars up to a finished milestone (Issue 30 / M4-30 follow-up)

**Milestone:** [Milestone 4: Clinics, Queues & Configuration](https://github.com/Billykat7/clinicQ/milestone/4) (closed) ·
**Issue:** [#30](https://github.com/Billykat7/clinicQ/issues/30) (closed by #145; this is its
follow-up) · **Tag to cut after merge:** `v0.4.0`

Every M4 issue (#23–#30) is closed and every pull request (#138–#145) merged, so the generated
progress bars can finally be regenerated against the truth. This is the bookkeeping that trails the
last merge of a milestone: three numbers that are read from GitHub, and one paragraph that has
outlived what it was written to explain.

No source file changes. Documentation only.

## Summary

- **`make milestone-progress ARGS='--close-completed'` regenerated three bars.** M4 goes
  **12% → 100%** (1/8 → 8/8), the project total **23/109 → 30/109 (21% → 28%)**, and the milestone
  count **3 → 4 of 14**. Every one of those figures is read from GitHub's own issue states by
  `scripts/update_milestone_progress.py`, never typed by hand.
- **`M4_clinics_queues_config.md` loses the note explaining why its two header rows disagreed** —
  it now argues against a number that no longer exists (below).
- **Its Status row picks up the partial-exit-criterion clause** that M2 and M3 already carry.

## Why the note had to go

While M4 was landing, the milestone document's two header rows said different things, and the
document explained why:

> **On the two rows above disagreeing.** The **Progress** bar is generated from GitHub […] it reads
> 12% because M4 shipped as eight **stacked** pull requests (#138–#145) and only the first has
> merged. […] Merge #139 → #140 → #141 → #142 → #143 → #144 → #145 in that order; the `Conventions`
> check is red on each until its predecessors are in.

Every sentence of that was true when it was written and none of it is true now. The bar reads 100%,
so there is no disagreement left to explain; the merge order it instructs the reader to follow is
done; and the `Conventions` warning describes a state that cannot recur on a closed milestone. A
note that explains a discrepancy is worse than useless once the discrepancy is gone — a reader who
believes it goes looking for a 12% that is not on the page. **None of M1–M3 carries one**, because
each of them was tidied the same way when it closed.

What replaces it is not a new invention. M2 and M3 both note a partly-met exit criterion in the
**Status** row, and M4 has exactly one, so it now says the same kind of thing in the same place:

> ✅ Done: issues 23–30 closed on 2026-09-12, release note [`v0.4.0`] . One exit criterion is met
> only in part, because the drift test cannot see a handler's error statuses (below)

The detail was already written out under **Exit criteria** and is unchanged — the drift test
compares method, path and the *concrete* statuses FastAPI's own document declares, so the
`403`/`404`/`409` a handler **raises** are invisible to it, and that half of the contract is proved
instead by `tests/integration/sites/test_sites_module.py` driving each documented refusal over HTTP.
The Status row now points at that the way the other two milestones do.

## Changes

- **`docs/GITHUB/MILESTONES/M4_clinics_queues_config.md`:** Progress bar 12% → 100%; the stale note
  removed; the Status row's partial-criterion clause added.
- **`README.md`:** the delivery table's M4 row and the `v1.0.0` total.
- **`docs/GITHUB/README.md`:** the milestone index's M4 row (**🚧 in progress → ✅ done**) and the
  footer total.

## Testing

- [x] **The committed bars match GitHub**, which is the only claim this PR makes. `make
      milestone-progress-check` is the generator run in `--check` mode, and it reports every
      milestone including the one this PR changes:

```
$ make milestone-progress-check
python scripts/update_milestone_progress.py --check
  M1  🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** (8/8 issues)
  M2  🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** (6/6 issues)
  M3  🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** (8/8 issues)
  M4  🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩 **100%** (8/8 issues)
  M5  ⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ **0%** (0/8 issues)
  …
14 milestone(s): up to date
```

      Before this PR the same command failed on M4, which is what a stale bar is supposed to do.

- [x] **The removed note was quoted from, not paraphrased.** The block above is the text as it stood
      in the file, so a reviewer can judge the removal without opening the diff.
- [x] No source file is touched, so the suites are unaffected; they were green on #145 at
      **1431 passed, 27 skipped, 9 xfailed**, and this branch adds no code to change that.

## Risk and rollback

Documentation only: no migration, no route, no dependency. The worst failure mode is a number being
wrong, and the number is generated and checked by CI rather than asserted here. Rollback is a revert,
which puts back a 12% bar that disagrees with a closed milestone.

**Follow-up:** with this merged, the `v0.4.0` tag's precondition holds — every issue closed, every
PR merged, the release note in `docs/GITHUB/RELEASES/RELEASE_v0_4_0.md`, and the bars agreeing with
GitHub.

Refs #30 (already closed by #145; this PR closes nothing new)
