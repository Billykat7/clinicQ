# PR: Time the open-now search as a median of seven runs (Issue 31 / M5-31 follow-up)

**Milestone:** [Milestone 5: Discovery & Geolocation](https://github.com/Billykat7/clinicQ/milestone/5) ·
**Issue:** [#31](https://github.com/Billykat7/clinicQ/issues/31) (closed by #147; this is its
follow-up) · **Unblocks:** the `Conventions` check on #149–#154

One test-only commit that was pushed to `Issue/31/clinics-nearby-postgis-search` after #147 merged,
so `main` does not have it. Two things follow from that:

- **`main` still has the flaky version of the test.** The open-now search is timed from a single
  measurement, which a shared CI runner pushed over its 200 ms budget twice during M5 (226 ms and
  240 ms) while the typical time stays near 60 ms.
- **Every later M5 pull request carries the commit.** Issue 34 merged it forward into the stack, so it
  is in #149–#154, where `Conventions` rejects it: `commit 'Issue 31: Time the open-now search as a
  median of seven runs' does not start with 'Issue 32: '`. Every other job on #149 is green. Once this
  merges, the commit is in `main` and drops out of those PRs' commit lists after `main` is merged
  forward into each, the same recipe that landed M4's stack.

No source file changes.

## Summary

- **`test_an_open_now_search_also_stays_inside_the_budget` takes the median of seven runs** after
  a warm-up, which is how the main 500-clinic budget test already measured. It asserts the median is
  under 200 ms and prints the slowest run, so a real regression still fails and a single noisy run
  does not.

## Changes

- **`tests/integration/discovery/test_nearby_search.py`:** the open-now timing test (commit `d9a54d6`).
- **`docs/GITHUB/PR/M5/PR_31_FOLLOWUP_DESCRIPTION.md`:** this description.
- A merge of `origin/main` into the branch, so the PR's diff is exactly the two above.

## Testing

- [x] The timing tests against PostgreSQL 18 + PostGIS, in UTC, on this branch:

```text
$ pytest -s tests/integration/discovery/test_nearby_search.py -k "open_now or 500 or budget or milliseconds"
500 clinics, 48 within 10 km: median 11.2 ms, slowest 16.8 ms
open_now over 500 candidates: median 57.0 ms, slowest 159.5 ms
3 passed, 17 deselected
```

      The slowest open-now run here was 159.5 ms against a 57.0 ms median. That spread on a quiet
      laptop is why a single sample on a shared CI runner can cross 200 ms.

## Risk and rollback

Test-only. Rollback is a revert, which brings the flaky single-sample timing back.

**Merge order:** merge this first. Then `main` is merged forward into #149 (Issue 32) and on up the
stack, one PR at a time.

Refs #31 (already closed by #147; this PR closes nothing new)
