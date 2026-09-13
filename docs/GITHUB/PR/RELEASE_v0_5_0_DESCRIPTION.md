# Release v0.5.0: the M5 release note, brought up to the merged state

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Tag to cut after merge:** `v0.5.0`, once the earlier untagged releases are cut

Every M5 issue (#31–#38) is closed and every pull request (#147–#154) merged, with four follow-ups
(#155–#158) after them. `RELEASE_v0_5_0.md` was written in #154 **before** those merges, so it described
a stack still in review. This pull request rewrites the parts that merging changed, and corrects the
tag claims in the hand-written status, which named tags that were never cut.

Documentation only.

## Summary

- **`docs/GITHUB/RELEASES/RELEASE_v0_5_0.md`:**
  - **Date and merge record.** The date is 2026-09-14. "The tag is cut when the last pull request
    merges" becomes the merge order that actually happened, and names the four follow-ups.
  - **What shipped** gains the progress tooling (`--assume-closed`, #155/#156) and the committed editor
    rules (#154/#158). The nearby-search bullet now gives the `open_now` timing as well.
  - **Known issues** gains four items:
    - the `open_now` budget test failed #152's CI at a 200.04 ms median; this note records the fix and
      that CI's margin is not visible;
    - a platform admin's notification bell gets a `403`, found while taking #159's screenshot;
    - #159 is still open;
    - only `v0.2.0` has ever been tagged.

    The `.env` item is corrected: `DB_PASSWORD` now matches the native PostgreSQL the suite uses, and
    only `REDIS_URL` is still wrong.
  - **Verification** is re-run on `main` at `3ac264d`, not on the #154 branch.
- **Tag claims now match GitHub.** `origin` has exactly one tag, `v0.2.0` (`git ls-remote --tags
  origin`). The README said "latest tag `v0.3.0`" and ticked M1 and M3 with bare tags, and the
  workload split said "tags `v0.1.0`–`v0.5.0`". They now say the notes are written and the tags are
  still to cut, which is the form the milestone-progress rule asks for.

## Verification

On `main` at `3ac264d`, with PostgreSQL 18.6 + PostGIS and Redis 7.0 installed natively, both
required:

```text
TZ=UTC pytest -q -n auto tests        1665 passed, 9 xfailed in 142.01s
ruff check . / ruff format --check    clean (438 files)
mypy src/                             clean (227 files)

500 clinics, 48 within 10 km:         median 13.4 ms, slowest 16.1 ms
open_now over 500 candidates:         median 43.5 ms, slowest 61.8 ms

$ git ls-remote --tags origin
1bfa466…  refs/tags/v0.2.0

$ python scripts/update_milestone_progress.py --check
14 milestone(s): up to date
```

## After merge

1. **Close the GitHub milestone:** `make milestone-progress ARGS='--close-completed'`. M5's issues are
   all closed. #159 still counts as open against the milestone, because the API counts pull requests
   too, but the script counts issues only.
2. **Cut the tags in order**, from the commits their notes describe: `v0.1.0`, then `v0.3.0`, `v0.4.0`
   and `v0.5.0`. Each tag push builds and publishes an image and starts a staging deploy
   (`docs/CICD/RELEASE.md`), so each is a deliberate step, not a batch.

Closes nothing: issues 31–38 were closed by their own pull requests (#147–#154).
