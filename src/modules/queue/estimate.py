"""The wait estimator: recent visits in, an honest range out (Issue 42).

**A pure function.** :func:`estimate_wait` takes plain numbers and returns a :class:`WaitEstimate`.
It reads no database, no clock and no settings, so it is tested with a list of samples and nothing
else, and the same inputs always give the same range. Where the samples come from (the
``wait_time_sample`` table) is :mod:`src.modules.queue.waits`; how well it does on the fixture
dataset is ``scripts/evaluate_wait_estimator.py``. The method, and why each number below is what it
is, is written up in ``docs/PRODUCT/wait-estimate-methodology.md``.

**The output is always a range with a confidence level, never one number.** :class:`WaitRange`
refuses to exist with ``high <= low``, so no surface can be handed "12 minutes". An estimate that
slips damages trust more than no estimate, and a band says honestly how sure the queue is.

**What is measured.** Each sample is the **call interval** of one completed visit: the minutes
between that patient being called and the call before it, counted only when the patient was already
waiting at that earlier call (so the queue was busy and the gap is throughput, not an empty room).
An interval already accounts for a queue served by several rooms at once, and for no-shows between
calls, without anyone configuring how many rooms a clinic has.

**The estimate, for a patient with ``k`` people waiting ahead:**

1. Take the most recent :attr:`EstimatorSettings.window` samples (N).
2. **Trim outliers** with Tukey's fences: drop any interval outside ``[Q1 - 1.5*IQR, Q3 + 1.5*IQR]``,
   so one 90-minute consultation does not stretch every estimate after it.
3. **Weight by hour of day**: each sample counts ``exp(-d**2 / (2*b**2))``, where ``d`` is the hours between
   its call and the hour being estimated (round the clock) and ``b`` the bandwidth, so a 07:30
   estimate leans on the morning rush and not on a quiet 14:00.
4. The weighted mean ``m`` and standard deviation ``s`` of an interval, and the effective sample count
   ``n = sum(w)**2 / sum(w**2)`` (Kish), which is what the weighting leaves of the N samples.
5. The patient is called after the residual of the current interval (half of one, on average) and
   ``k`` whole ones, so the centre is ``c = (k + 0.5) * m``. Its variance combines the variation of
   that many intervals, ``(k + 0.5) * s**2``, with the uncertainty of ``m`` itself,
   ``((k + 0.5) * s)**2 / n``.
6. The band is the centre plus and minus ``z`` standard deviations, rounded outwards to whole five
   minutes and at least five minutes wide.

**Below the minimum** (``n`` under :attr:`EstimatorSettings.min_effective_samples`) the observed mean
is not trusted: the estimate falls back to the queue's expected minutes with a wide assumed spread,
is marked ``basis=expected`` and ``confidence=low``, and its label says **approximate**.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import quantiles
from typing import Final

from src.commons.enums import EstimateBasis, EstimateConfidence

#: Hours in a day, for the round-the-clock distance between two call times.
_DAY_HOURS: Final = 24


@dataclass(frozen=True, slots=True)
class WaitRange:
    """An expected wait, always as a range: "15–25 min", never "20 min" (Issues 35, 42).

    The invariant is checked on construction, so a surface handed one can render it without deciding
    what to do with a degenerate range.
    """

    low_minutes: int
    high_minutes: int

    def __post_init__(self) -> None:
        """Refuse a negative bound, and a "range" that is really one number."""
        if self.low_minutes < 0:
            raise ValueError("A wait cannot be negative.")
        if self.high_minutes <= self.low_minutes:
            raise ValueError(
                f"A wait is a range: {self.low_minutes}–{self.high_minutes} min is not one."
            )


@dataclass(frozen=True, slots=True)
class VisitSample:
    """One completed visit as the estimator sees it: its call interval, and the hour it was called."""

    interval_minutes: float
    #: The Johannesburg hour (0–23) the patient was called at.
    hour: int


@dataclass(frozen=True, slots=True)
class EstimatorSettings:
    """The estimator's parameters. The defaults are the ones measured in the methodology note."""

    #: N: how many of the most recent samples are considered.
    window: int = 60
    #: Below this effective sample count the estimate falls back to the expected minutes.
    min_effective_samples: float = 8.0
    #: At or above this effective count, with agreeing samples, confidence is high.
    high_confidence_samples: float = 20.0
    #: The coefficient of variation above which confidence is at most medium.
    high_confidence_max_cv: float = 0.6
    #: The hour-of-day weighting's bandwidth, in hours.
    bandwidth_hours: float = 2.0
    #: Tukey's fence multiplier; outliers beyond it are trimmed.
    fence: float = 1.5
    #: The fewest samples outlier trimming is attempted on.
    min_samples_to_trim: int = 4
    #: How many standard deviations either side of the centre the band spans.
    band_z: float = 1.28
    #: The spread assumed for the expected-minutes fallback, as a share of the mean.
    fallback_cv: float = 0.3
    #: Bounds are rounded outwards to this many minutes.
    rounding_minutes: int = 5
    #: How much of the interval in progress is still to run when the patient joins, on average.
    residual_intervals: float = 0.5


#: The settings every surface uses.
DEFAULT_SETTINGS: Final = EstimatorSettings()


@dataclass(frozen=True, slots=True)
class WaitEstimate:
    """A wait range, how much to trust it, and what it was built from."""

    wait: WaitRange
    confidence: EstimateConfidence
    basis: EstimateBasis
    #: The effective number of samples behind the range, after trimming and weighting: 0 for a
    #: fallback, ``None`` when the estimate was read back from the queue snapshot, which keeps the
    #: range, the confidence and the basis but not the count.
    effective_samples: float | None = None

    @property
    def approximate(self) -> bool:
        """Whether a surface must say the range is approximate: built without enough visits."""
        return self.basis is EstimateBasis.EXPECTED

    @property
    def label(self) -> str:
        """The range in words, as every surface shows it: ``~15–25 min``, marked when approximate."""
        text = f"~{self.wait.low_minutes}–{self.wait.high_minutes} min"
        return f"{text} (approximate)" if self.approximate else text


def trim_outliers(values: Sequence[float], *, fence: float = 1.5) -> list[float]:
    """Drop values outside Tukey's fences ``[Q1 - fence*IQR, Q3 + fence*IQR]``.

    Quartiles by the inclusive method, so the fences stay inside the data for a small sample.
    """
    if len(values) < 2:
        return list(values)
    first, _, third = quantiles(values, n=4, method="inclusive")
    spread = third - first
    low, high = first - fence * spread, third + fence * spread
    return [value for value in values if low <= value <= high]


def _hour_distance(a: int, b: float) -> float:
    """Hours between two times of day, the short way round the clock."""
    gap = abs(a - b) % _DAY_HOURS
    return min(gap, _DAY_HOURS - gap)


def _round_band(centre: float, spread: float, step: int) -> WaitRange:
    """``centre ± spread`` rounded outwards to ``step`` minutes, never negative, at least one step wide."""
    low = max(0, math.floor((centre - spread) / step) * step)
    high = math.ceil((centre + spread) / step) * step
    return WaitRange(low, max(high, low + step))


def estimate_wait(
    samples: Sequence[VisitSample],
    *,
    people_ahead: int,
    at_hour: float,
    expected_service_minutes: int,
    settings: EstimatorSettings = DEFAULT_SETTINGS,
) -> WaitEstimate:
    """Estimate how long a patient with ``people_ahead`` waiting before them will wait.

    Args:
        samples: The queue's recent completed visits, **most recent first**.
        people_ahead: Waiting tickets ahead of the patient (0 for the next to be called).
        at_hour: The Johannesburg time of day being estimated for, in hours (7.5 is 07:30).
        expected_service_minutes: The queue's configured minutes per patient: the fallback.
        settings: The parameters; the measured defaults unless a test or the evaluation varies them.

    Returns:
        The range, its confidence and its basis.

    Raises:
        ValueError: A negative number of people ahead, or an expected time that is not positive.
    """
    if people_ahead < 0:
        raise ValueError("people_ahead cannot be negative.")
    if expected_service_minutes <= 0:
        raise ValueError("expected_service_minutes must be positive.")
    # The rest of the interval in progress, then one whole interval per person ahead.
    calls = people_ahead + settings.residual_intervals

    recent = [
        sample for sample in samples[: settings.window] if sample.interval_minutes >= 0
    ]
    if len(recent) >= settings.min_samples_to_trim:
        kept = trim_outliers([s.interval_minutes for s in recent], fence=settings.fence)
        lowest, highest = (min(kept), max(kept)) if kept else (0.0, -1.0)
        recent = [s for s in recent if lowest <= s.interval_minutes <= highest]

    weights = [
        math.exp(
            -(_hour_distance(s.hour, at_hour) ** 2) / (2 * settings.bandwidth_hours**2)
        )
        for s in recent
    ]
    total = sum(weights)
    effective = (total**2 / sum(w * w for w in weights)) if total > 0 else 0.0

    if effective < settings.min_effective_samples:
        mean = float(expected_service_minutes)
        sd = settings.fallback_cv * mean
        spread = settings.band_z * math.sqrt(calls * sd**2 + (calls * sd) ** 2)
        return WaitEstimate(
            wait=_round_band(calls * mean, spread, settings.rounding_minutes),
            confidence=EstimateConfidence.LOW,
            basis=EstimateBasis.EXPECTED,
            effective_samples=0.0,
        )

    mean = (
        sum(w * s.interval_minutes for w, s in zip(weights, recent, strict=True))
        / total
    )
    variance = (
        sum(
            w * (s.interval_minutes - mean) ** 2
            for w, s in zip(weights, recent, strict=True)
        )
        / total
    )
    variance *= effective / (effective - 1)  # the unbiased weighted variance
    sd = math.sqrt(variance)
    spread = settings.band_z * math.sqrt(
        calls * variance + (calls * sd) ** 2 / effective
    )
    agreeing = mean > 0 and sd / mean <= settings.high_confidence_max_cv
    confidence = (
        EstimateConfidence.HIGH
        if effective >= settings.high_confidence_samples and agreeing
        else EstimateConfidence.MEDIUM
    )
    return WaitEstimate(
        wait=_round_band(calls * mean, spread, settings.rounding_minutes),
        confidence=confidence,
        basis=EstimateBasis.OBSERVED,
        effective_samples=round(effective, 1),
    )
