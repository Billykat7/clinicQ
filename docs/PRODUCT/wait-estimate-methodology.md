# Wait-estimate methodology

> **Status: draft for F's review.** The sprint plan gives this note to F in sprint 6 ("wait-estimate
> methodology"), to be read before Issue 42 chose its parameters. No such note was in the repository
> when Issue 42 started. So this one was written with the estimator, from a measurement rather than
> an assumption, and every parameter below can be re-measured with one command. F should review
> the method and the choices; changing a parameter is a one-line change plus a re-run.

**Code:** `src/modules/queue/estimate.py` (the pure function), `src/modules/queue/waits.py` (samples
and reads), `scripts/evaluate_wait_estimator.py` (the measurement). **Issue:**
[42](../GITHUB/ISSUES/M6/ISSUE_42_wait_time_estimation.md).

## What a patient is told, and why it is a range

A patient sees **a range with a confidence**: "~15–25 min", or "~30–60 min (approximate)" while a
queue has too little history. They never see "20 min". A single number reads as a promise, and a
promise that slips does more damage to trust than no estimate at all. The code enforces this:
`WaitRange` cannot be built with `high <= low`, and a test walks the whole API for a wait given as one
number.

## What is measured

Each completed visit (a ticket reaching `done`) writes one `wait_time_sample` with three durations:
the wait (joined to called), the service (started to done) and the **call interval**. The
estimator uses the interval.

**The call interval** is the minutes between this patient's call and the previous call in the same
queue on the same day. It counts **only if this patient was already waiting at that previous call**.
Only then was the queue busy the whole time, so the gap measures how fast the queue moved, not an
empty waiting room. It is also ignored above 180 minutes.

Why the interval rather than service minutes:

- **It already accounts for the number of rooms.** Four doctors seeing 12-minute consultations call
  a patient every 3 minutes. Nobody has to keep a "rooms open" setting correct.
- **It includes what really slows a queue**: no-shows between calls, handovers and breaks.

## The estimate

For a patient with `k` people waiting ahead of them, at time of day `t`:

1. **Window.** Take the queue's most recent **N = 60** samples with an interval, newest first. The
   history crosses days.
2. **Outlier trimming.** Drop intervals outside Tukey's fences, `[Q1 - 1.5 IQR, Q3 + 1.5 IQR]`, if at
   least 4 samples are left. One 90-minute consultation must not stretch every estimate after it.
3. **Hour-of-day weighting.** Each sample gets the weight `exp(-d^2 / (2 b^2))`, where `d` is the
   hours between its call and `t`, measured round the clock, and **b = 2 hours**. A 07:30 estimate
   leans on the morning rush, not on a quiet 14:00.
4. **Mean, spread, effective count.** Compute the weighted mean `m` and the unbiased weighted
   standard deviation `s` of the interval. The effective sample count is
   `n = (sum w)^2 / sum(w^2)` (Kish): how many samples the weighting really leaves.
5. **Centre.** The patient is called after the rest of the interval in progress (half of one, on
   average) and then `k` whole intervals, so `c = (k + 0.5) m`.
6. **Spread.** The variance combines the variation of `k + 0.5` intervals, `(k + 0.5) s^2`, with the
   uncertainty of `m` itself, `((k + 0.5) s)^2 / n`. A short history widens the band. So does a long
   queue, because an error in `m` is multiplied by every person ahead.
7. **Band.** Take `c ± z` standard deviations with **z = 1.28**, rounded outwards to whole five
   minutes, never negative and at least five minutes wide.

**Cold start.** If `n` is below **8**, the observed mean is not trusted. The centre uses the queue's
configured `expected_service_minutes` instead, with an assumed spread of 30% of it. The estimate is
marked `basis = expected`, `confidence = low`, and its label ends in "(approximate)".

**Confidence.**

| Level | When |
|---|---|
| `low` | The cold start (`basis = expected`). |
| `medium` | Measured from at least 8 effective samples. |
| `high` | Measured from at least 20 effective samples whose coefficient of variation is at most 0.6. |

## How the parameters were chosen

The estimator was replayed against the fixture dataset (`scripts/db/demo_dataset.py`). That covers
every demo clinic and queue over ten weekdays: 47,927 patients who were called. At the moment each
patient joined, the replay gave the estimator only what production would have had: the visits
finished by then, the people who had joined earlier and were not yet called, and the time of day.
It then compared the range with the wait the patient actually had.

| Setting | Actual inside the range | Mean abs. error of the midpoint (min) | Median abs. error | Mean width (min) |
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

The reasoning behind each choice:

- **N = 60.** N=30 is clearly worse. N=120 is slightly better on this dataset but reacts to a slow
  day twice as slowly. The dataset keeps the same pace every day, which flatters a long window, so
  60 is the cautious choice.
- **Minimum 8 effective samples.** 5 is marginally better here, but below 8 a mean and a standard
  deviation are too easily thrown by two odd visits in a real clinic. 15 is worse.
- **b = 2 hours.** 1 hour leaves too few samples per estimate. 4 hours and no weighting both blur the
  morning into the afternoon. The dataset's service pace does not change through the day, only its
  arrivals do, so the gain measured here (6.41 to 6.20 minutes) is a floor, not the real benefit.
- **z = 1.28.** The central 80% of a normal distribution. The replay covers 87%, because rounding
  outwards to five minutes adds margin. `z = 1.0` narrows the band by three minutes but lets one
  wait in seven fall outside it. An estimate that slips costs more trust than a slightly wider one.
- **Trimming kept, although "no trimming" scores better here.** The dataset has no real outliers,
  since its service times are Gaussian, so trimming only removes genuine tail values and costs a
  little. A real clinic has 90-minute consultations and a doctor called away. The unit test shows one
  such visit moving the upper bound by 10 minutes or more without trimming, and not at all with it.
- **Residual of half an interval.** Tried against 0.75 and 1.0: 0.5 has the lowest error (6.20
  against 6.25 and 6.44), and it is what an arrival at a random moment in an interval expects.

## Accuracy against the fixture dataset

Re-run with `python scripts/evaluate_wait_estimator.py --days 10`:

| Group | Predictions | Actual inside the range | Mean abs. error (min) | Median abs. error | Bias | Mean width (min) | Naive `expected * ahead` mean abs. error |
|---|---:|---:|---:|---:|---:|---:|---:|
| all | 47,927 | 87.2% | 6.2 | 2.3 | -2.0 | 17.5 | 30.5 |
| basis=observed | 45,211 | 87.8% | 5.6 | 2.2 | -2.7 | 16.9 | 31.5 |
| basis=expected | 2,716 | 77.1% | 16.3 | 4.6 | +11.0 | 26.6 | 14.2 |
| confidence=high | 13,551 | 84.1% | 5.8 | 2.3 | -2.4 | 15.6 | 22.8 |
| confidence=medium | 31,660 | 89.3% | 5.5 | 2.2 | -2.9 | 17.5 | 35.2 |
| confidence=low | 2,716 | 77.1% | 16.3 | 4.6 | +11.0 | 26.6 | 14.2 |

The naive baseline is what the product brief first described: expected minutes times position. It
misses by 30.5 minutes on average, because it ignores rooms. The estimator's midpoint misses by 6.2,
and the actual wait falls inside its range 87% of the time.

## Known limits, stated plainly

- **The dataset is synthetic.** It is a simulation with its own service times and arrival pattern.
  These figures show the estimator behaves as designed on believable queues. They are not a
  prediction of how it will score at a real clinic. The pilot (Issue 108) must re-measure it on real
  visits, with the same script pointed at real samples.
- **The cold start is pessimistic.** It treats `expected_service_minutes` as minutes per call, which
  assumes one room. A four-room queue's first few estimates are therefore too long (bias +11 minutes,
  77% coverage) until eight visits finish. Erring long sends a patient early rather than late. A
  clinic that wants better first estimates can set the queue's expected minutes per call rather than
  per consultation.
- **The high-confidence band covers 84%, below the medium one.** Its tighter samples produce narrower
  bands, and a queue that changes pace within a day escapes them more often. Confidence describes
  the evidence, not a promised hit rate.
- **A slight optimism** (bias about -2 minutes on observed estimates) comes from queues speeding up
  after the morning rush. The hour weighting reduces it but does not remove it.
- **Priority overrides and transfers** (Issues 45 and 46) put people ahead of a patient after the
  estimate was given. The estimate is recomputed on every read, so the next page load shows the
  change, but a range already given cannot foresee one.
