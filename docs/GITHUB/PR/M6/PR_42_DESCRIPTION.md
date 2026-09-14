# PR: Honest wait ranges from how fast the queue is really moving, measured (Issue 42 / M6-42)

**Milestone:** [Milestone 6: Queue Engine Core](https://github.com/Billykat7/clinicQ/milestone/6) ·
**Issue:** [#42](https://github.com/Billykat7/clinicQ/issues/42) · **Builds on:** #39 (tickets),
#41 (the lifecycle) and #26 (expected service times), all merged

Patients now see how long they will wait as **a range with a confidence**: "~15–25 min", or
"~30–60 min (approximate)" while a queue has too little history. The range comes from a pure
function in `src/modules/queue/estimate.py` (recent visits in, range out). It is built from how fast
this queue has actually been calling patients, with long outlier visits trimmed and more weight on
the same time of day. Every finished visit feeds the next estimate in the same transaction. The
single "average wait" column the snapshot had is gone, and a test fails if any API schema gives a
wait as one number.

The issue says an estimator nobody measured is a guess with a decimal point, so this one was
measured. Replayed against the fixture dataset (47,927 patients over ten weekdays), the actual wait
fell inside the range **87.2%** of the time. The range's midpoint missed by **6.2 minutes** on
average (median **2.3**), against **30.5** for the naive expected minutes times position.

## F's methodology note

The sprint plan gives F a wait-estimate methodology note in sprint 6, to be read before choosing N,
the trimming and the hour weighting. **There was no such note in the repository.** Rather than
invent parameters, this PR measures every candidate on the fixture replay and writes the result up
as [`docs/PRODUCT/wait-estimate-methodology.md`](../../../PRODUCT/wait-estimate-methodology.md),
marked a **draft for F's review**. Each parameter below cites the row of that note that chose it,
and any change is one line plus `python scripts/evaluate_wait_estimator.py`. F's review is a
follow-up, not a blocker: the method is conservative where the data cannot decide.

## Summary

- **`estimate_wait(samples, *, people_ahead, at_hour, expected_service_minutes)`** is pure: stdlib
  and `src.commons.enums` only, no database, clock or settings, with a guard test on its imports.
  It returns a `WaitEstimate` (`WaitRange`, `EstimateConfidence`, `EstimateBasis`, `label`).
  `WaitRange` moved into the estimator and still refuses `high <= low`.
- **What it measures: the call interval.** That is the gap between a patient's call and the previous
  one, counted only while the queue was busy. It already reflects how many rooms serve the queue and
  the no-shows between calls, so no clinic has to keep a "rooms open" setting correct.
- **The method** (methodology note, *The estimate*):
  - the last **N = 60** samples;
  - Tukey's fences (**1.5 IQR**);
  - Gaussian hour-of-day weights with **b = 2 h**;
  - centre `(k + ½)·m`, spread from the interval's variance and the mean's uncertainty;
  - a band of **±1.28 sd**, rounded outwards to five minutes.

  Below **8** effective samples it falls back to the queue's expected minutes with `confidence=low`,
  `basis=expected` and "(approximate)" in the label.
- **`wait_time_sample`** (migration `0020`) holds wait, service and interval minutes and the called
  hour. `transition_ticket()` writes it in the transaction that marks a ticket `done`, so the next
  estimate anywhere already includes that visit.
- **On every surface:**
  - the join response gains `wait {low_minutes, high_minutes, confidence, approximate, label}`;
  - discovery's `wait_range` (API and clinic profile) is filled in and gains `confidence`,
    `approximate` and `label`;
  - the web labels read *Wait about 5–25 min (approximate)* instead of *Wait estimate not available
    yet*;
  - the queue snapshot (table and Redis) stores the range and confidence in place of
    `average_wait_minutes`.
- **`scripts/evaluate_wait_estimator.py`** replays the dataset and prints the accuracy table. A small
  replay also runs in CI as a regression test.

## Design notes

**Why intervals rather than service minutes times position.** The product brief's first idea was
"average service time × position". It misses by 30.5 minutes on the fixture data, because four
doctors seeing 12-minute consultations move a line four times faster than one. Measuring the queue's
own call rate gets that right without knowing the room count.

**The spread is principled, then checked.** The centre `(k + ½)·m` counts the rest of the interval in
progress (half of one on average; 0.75 and 1.0 were measured and are worse) and one interval per
person ahead. The variance adds the variation of that many intervals to the uncertainty of `m`
itself, `((k + ½)·s)² / n`. A short history or a long queue therefore widens the band on its own.
`z = 1.28` is a theoretical 80% band, and the replay covers 87% because rounding outwards adds
margin. `z = 1.0` was 3 minutes narrower but let one wait in seven fall outside (methodology note,
*How the parameters were chosen*).

**Trimming is kept although the fixture data scores slightly better without it (88.7% vs 87.2%).**
The simulator's service times are Gaussian, with no 90-minute consultations or doctor called away,
so trimming there only removes genuine tail values. A real clinic has those outliers, and
`test_one_very_long_visit_does_not_distort_the_estimate` shows one such visit moving the upper bound
by 10 minutes or more without trimming and not at all with it. That is the criterion.

**Hour weighting is measured, and its gain here is a floor.** With weighting off, error rises from
6.20 to 6.41 minutes. The simulator keeps its service pace constant through the day, so this
understates the real benefit. `test_a_morning_estimate_is_built_from_the_morning_not_from_the_afternoon`
shows the effect on data that does change pace.

**The snapshot no longer has an average.** `site_queue_snapshot.average_wait_minutes` was only ever
`NULL`, and a column named "average wait" is an invitation to show one number. Migration `0020`
replaces it with `wait_low_minutes`, `wait_high_minutes`, `wait_confidence` and `wait_approximate`,
and Redis entries carry the same four values. The sweep refills them within a minute of deploying.

**The honest weak spot is the cold start.** It assumes one room, so a four-room queue's first
estimates run long (bias +11 min, 77% coverage) until eight visits finish. Erring long sends a
patient early rather than late. The note says so, with the setting a clinic can change.

**A CI-visible rule: no single-number waits.** `test_no_api_schema_gives_a_wait_as_a_single_number`
walks the application's OpenAPI document. Any property with "wait" in its name that is numeric and
not part of a low/high pair fails, while people counts (`waiting`, `waiting_ahead`) are allowed. A
fixture `estimated_wait_minutes: integer` proves it can fail.

**Guards.** `recent_samples` is listed in the site-scope guard: it reads samples for queues the caller
already narrowed, never a ticket. The cross-tenant suite lists `waittimesample` as pending, because
no route reads samples; only the ranges built from them are shown.

**Out of scope:** showing the range on the ticket page, board and dashboard (Issues 68, 56, 49 read
it from these fields) and the nightly statistics (Issue 88).

## Changes

- **`src/modules/queue/estimate.py`** (new): `estimate_wait`, `trim_outliers`, `WaitRange` (moved from
  `queues/live.py`), `VisitSample`, `WaitEstimate`, `EstimatorSettings`.
- **`src/modules/queue/waits.py`** (new): `record_visit`, `recent_samples` (one windowed query for
  many queues), `estimates_for`.
- **`src/database/models/wait_time_sample.py`**, **`alembic/versions/0020_wait_time_samples.py`**
  (new). **`site_queue_snapshot.py`:** the range columns.
- **`src/modules/queue/lifecycle.py`:** a sample is written on `done`. **`service.py`**, **`schemas.py`**,
  **`router.py`:** `JoinResult.wait`, `WaitOut`.
- **`src/modules/queue/snapshot.py`**, **`src/modules/queues/live.py`:** readings and snapshots carry
  a `WaitEstimate`; the direct read estimates for somebody joining now.
- **`src/modules/discovery/schemas.py`**, **`src/web/discover.py`:** `WaitRangeOut` gains
  `confidence`, `approximate` and `label`; the page label says "(approximate)".
- **`src/commons/enums.py`:** `EstimateConfidence`, `EstimateBasis`.
- **`contracts/discovery.yaml`** (0.6.0) and **`contracts/queue.yaml`:** the range shapes and
  examples.
- **`scripts/evaluate_wait_estimator.py`** (new); **`docs/PRODUCT/wait-estimate-methodology.md`**
  (new); **`docs/PRODUCT/03-booking-and-queue.md`:** the estimate paragraph and the sample table.
- **Tests (new):** `tests/unit/queue/test_wait_estimate.py` (9) and
  `tests/integration/queue/test_wait_samples.py` (6).
- **Tests (updated):** `test_discover_labels.py` (+1), `test_clinic_detail.py` and
  `test_nearby_search.py` (a range where they asserted `null`), `test_site_scoped_queries.py`,
  `test_cross_tenant.py`.
- **Docs:** the M6 Status row, the sprint 7 row (now in progress), the README Status block and the
  progress bars (`make milestone-progress ARGS='--assume-closed 42'`).

## Testing

- [x] `ruff check .` and `ruff format --check .` clean; `mypy src/` clean (237 files).
- [x] Full suite in UTC with PostgreSQL and Redis required: **1827 passed, 9 xfailed**.
- [x] `make milestone-progress-check ARGS='--assume-closed 42'`: up to date.
- [x] The Issue 42 tests:

```text
queue/test_wait_estimate.py::test_the_output_is_always_a_range_whatever_the_input PASSED
queue/test_wait_estimate.py::test_a_single_number_cannot_even_be_constructed PASSED
queue/test_wait_estimate.py::test_fewer_than_n_samples_is_low_confidence_and_labelled_approximate PASSED
queue/test_wait_estimate.py::test_enough_agreeing_samples_are_observed_and_high_confidence PASSED
queue/test_wait_estimate.py::test_one_very_long_visit_does_not_distort_the_estimate PASSED
queue/test_wait_estimate.py::test_a_morning_estimate_is_built_from_the_morning_not_from_the_afternoon PASSED
queue/test_wait_estimate.py::test_the_estimator_is_a_pure_function_of_its_inputs PASSED
queue/test_wait_estimate.py::test_negative_positions_and_expected_times_are_refused PASSED
queue/test_wait_estimate.py::test_accuracy_against_the_fixture_dataset_does_not_regress PASSED
queue/test_wait_samples.py::test_a_finished_visit_writes_one_sample_with_its_wait_service_and_interval PASSED
queue/test_wait_samples.py::test_a_visit_that_arrived_after_the_previous_call_measures_no_interval PASSED
queue/test_wait_samples.py::test_the_estimate_updates_as_tickets_complete_within_one_minute PASSED
queue/test_wait_samples.py::test_every_surface_shows_a_range_and_the_snapshot_keeps_one PASSED
queue/test_wait_samples.py::test_no_api_schema_gives_a_wait_as_a_single_number PASSED
queue/test_wait_samples.py::test_a_ticket_driven_to_done_through_the_factory_is_sampled_too PASSED
discovery/test_discover_labels.py::test_a_wait_is_a_range_or_nothing PASSED
discovery/test_discover_labels.py::test_a_wait_built_from_expected_minutes_says_it_is_approximate PASSED
```

- [x] **The estimator's error against the fixture dataset** (`python scripts/evaluate_wait_estimator.py
      --days 10`: every demo clinic and queue, ten weekdays). At each patient's join, the replay gave
      the estimator only the visits finished by then, the people not yet called, and the time:

| Group | Predictions | Actual inside the range | Mean abs. error (min) | Median abs. error | Bias | Mean width (min) | Naive `expected * ahead` mean abs. error |
|---|---:|---:|---:|---:|---:|---:|---:|
| all | 47,927 | 87.2% | 6.2 | 2.3 | -2.0 | 17.5 | 30.5 |
| basis=observed | 45,211 | 87.8% | 5.6 | 2.2 | -2.7 | 16.9 | 31.5 |
| basis=expected | 2,716 | 77.1% | 16.3 | 4.6 | +11.0 | 26.6 | 14.2 |
| confidence=high | 13,551 | 84.1% | 5.8 | 2.3 | -2.4 | 15.6 | 22.8 |
| confidence=medium | 31,660 | 89.3% | 5.5 | 2.2 | -2.9 | 17.5 | 35.2 |
| confidence=low | 2,716 | 77.1% | 16.3 | 4.6 | +11.0 | 26.6 | 14.2 |

- [x] **How the parameters were chosen**, the same replay with one setting changed at a time. This is
      the table the methodology note cites:

| Setting | Actual inside the range | Mean abs. error (min) | Median abs. error | Mean width (min) |
|---|---:|---:|---:|---:|
| **Chosen: N=60, minimum 8, b=2 h, z=1.28, fences 1.5** | **87.2%** | **6.20** | **2.27** | **17.5** |
| N=30 | 85.9% | 6.92 | 2.40 | 19.3 |
| N=120 | 87.6% | 5.94 | 2.21 | 16.4 |
| Minimum 5 samples | 87.3% | 6.07 | 2.25 | 17.3 |
| Minimum 15 samples | 86.8% | 6.53 | 2.30 | 17.9 |
| b=1 h | 86.3% | 6.99 | 2.80 | 19.5 |
| b=4 h | 87.0% | 6.21 | 2.25 | 17.0 |
| No hour weighting | 86.4% | 6.41 | 2.27 | 16.9 |
| No trimming | 88.7% | 5.96 | 2.25 | 18.0 |
| z=1.0 | 84.8% | 6.19 | 2.25 | 14.7 |

- [ ] Screenshot: no template, stylesheet or script changed. The discovery card's wait label is built
      in `src/web/discover.py` and now reads *Wait about …–… min (approximate)* on a queue with no
      history; the label tests assert it.

## Acceptance criteria

- [x] **Estimates are always a range, never a single number, on every surface.** Four checks:
      - `WaitRange` cannot hold one number;
      - 1,500 input combinations all return `high > low`;
      - the join response, the discovery API and profile, the web label and the snapshot row all
        carry a range and a confidence (tested);
      - an OpenAPI walk finds no wait given as one number, and its fixture shows it can fail.
- [x] **Fewer than N samples produces a low-confidence estimate clearly labelled as approximate.**
      With 7 samples the estimate is `basis=expected`, `confidence=low`, labelled "… (approximate)"
      and built from the expected minutes. Over HTTP, the first join on a new queue answers
      `approximate: true, confidence: "low"`.
- [x] **A single very long visit does not distort the estimate (outlier trimming works).** Thirty
      5-minute visits plus one 90-minute visit give exactly the range of the thirty alone. Without
      trimming, the same data moves the upper bound by 10 minutes or more.
- [x] **The estimate updates as tickets complete, within one minute.** The sample is written in the
      transaction that marks the ticket `done`, so no job is involved. In the test, a queue answering
      `approximate` stops being approximate on the next join after eleven visits finish, with the
      elapsed time asserted under 60 seconds (in practice a fraction of a second). The snapshot is
      written through by the same transition.
- [x] **Estimator accuracy is measured against the fixture dataset and reported in the PR.** Above:
      87.2% coverage, 6.2 minutes mean and 2.3 median absolute error over 47,927 predictions, broken
      down by basis and confidence, with a parameter comparison. A two-day replay runs in CI and
      fails below 80% coverage or at more than half the naive error. The data is synthetic: the note
      and this PR say so, and the pilot (Issue 108) must re-measure on real visits.
- [x] **The estimator is a pure function of its inputs and is unit-testable without a database.**
      Every test in `test_wait_estimate.py` runs without a database, identical inputs give identical
      output, and a guard allows the module to import only the stdlib and the enums.

## Risk and rollback

**Migration `0020`** adds `wait_time_sample` and swaps the snapshot's always-`NULL` average for
four range columns. The snapshot is a cache the sweep rebuilds within a minute, so no data of
consequence changes. It is reversible (`alembic downgrade 0019`). Every join, discovery read and
sweep now runs one extra windowed query per page of queues, on
`ix_clinicq_wait_time_sample_queue_recorded`. The API gains fields and loses none. Rollback is a
revert plus the downgrade.

**Follow-ups:** F reviews the methodology note (the draft is marked as such). Issue 88 reads
`wait_time_sample` for the nightly statistics. The pilot re-runs the evaluation on real samples. If
clinics report pessimistic first estimates, a per-queue "rooms" hint for the cold start is the
obvious next step.

Closes #42
