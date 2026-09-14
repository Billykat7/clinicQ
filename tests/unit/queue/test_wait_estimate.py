"""The wait estimator, as the pure function it is: samples in, a range out (Issue 42).

No database, no clock, no settings object from the environment: each test hands
:func:`~src.modules.queue.estimate.estimate_wait` a list of samples and reads the range back. The
criteria, one or more tests each:

* the output is **always a range**, never one number, for any input;
* **fewer than N** samples gives a low-confidence estimate **labelled approximate**;
* **one very long visit does not distort** the estimate (outlier trimming), and it would without;
* **hour-of-day weighting**: a 07:30 estimate is built from the morning, not from 14:00;
* the estimator is **a pure function of its inputs**: same inputs, same output, and a guard on its
  imports;
* its **accuracy against the fixture dataset** does not regress (a small replay of the evaluation
  the PR reports in full).
"""

from __future__ import annotations

import ast
import itertools
import random
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.evaluate_wait_estimator import evaluate
from src.commons.enums import EstimateBasis, EstimateConfidence
from src.modules.queue.estimate import (
    DEFAULT_SETTINGS,
    VisitSample,
    WaitRange,
    estimate_wait,
    trim_outliers,
)

_ESTIMATE_MODULE = (
    Path(__file__).resolve().parents[3] / "src" / "modules" / "queue" / "estimate.py"
)


def _steady(
    minutes: float, count: int, hour: int = 10, jitter: float = 0.5
) -> list[VisitSample]:
    """``count`` intervals around ``minutes``, deterministic, all called at ``hour``."""
    rng = random.Random(f"{minutes}-{count}-{hour}")
    return [
        VisitSample(minutes + rng.uniform(-jitter, jitter), hour) for _ in range(count)
    ]


def test_the_output_is_always_a_range_whatever_the_input() -> None:
    """Criterion 1 at the source: 1,500 combinations of samples, queue position and hour."""
    sample_sets = [
        [],
        _steady(4, 3),
        _steady(4, 12),
        _steady(12, 60),
        [VisitSample(0.0, 9)] * 30,
    ]
    for samples, ahead, hour, expected in itertools.product(
        sample_sets, range(0, 60, 2), (0.0, 7.5, 13.25), (1, 5, 30, 240)
    ):
        estimate = estimate_wait(
            samples, people_ahead=ahead, at_hour=hour, expected_service_minutes=expected
        )
        assert estimate.wait.high_minutes > estimate.wait.low_minutes >= 0
        assert estimate.label.startswith("~") and "–" in estimate.label


def test_a_single_number_cannot_even_be_constructed() -> None:
    """``WaitRange(20, 20)`` is refused, so no surface can be handed one."""
    with pytest.raises(ValueError, match="is not one"):
        WaitRange(20, 20)
    with pytest.raises(ValueError, match="negative"):
        WaitRange(-5, 10)


def test_fewer_than_n_samples_is_low_confidence_and_labelled_approximate() -> None:
    """Criterion 2: below the minimum the range comes from the expected minutes, and says so."""
    few = _steady(3, int(DEFAULT_SETTINGS.min_effective_samples) - 1)

    estimate = estimate_wait(
        few, people_ahead=4, at_hour=10, expected_service_minutes=12
    )

    assert estimate.basis is EstimateBasis.EXPECTED
    assert estimate.confidence is EstimateConfidence.LOW
    assert estimate.approximate is True
    assert estimate.label.endswith("(approximate)")
    # Built from 12 expected minutes (4.5 intervals ≈ 54 min), not from the 3-minute samples.
    assert estimate.wait.low_minutes <= 54 <= estimate.wait.high_minutes


def test_enough_agreeing_samples_are_observed_and_high_confidence() -> None:
    """Plenty of recent visits that agree: the range is measured, trusted, and not marked approximate."""
    estimate = estimate_wait(
        _steady(4, 50), people_ahead=5, at_hour=10, expected_service_minutes=12
    )
    assert estimate.basis is EstimateBasis.OBSERVED
    assert estimate.confidence is EstimateConfidence.HIGH
    assert not estimate.approximate and estimate.label == (
        f"~{estimate.wait.low_minutes}–{estimate.wait.high_minutes} min"
    )
    # 5.5 intervals of about 4 minutes: about 22 minutes, inside the range.
    assert estimate.wait.low_minutes <= 22 <= estimate.wait.high_minutes


def test_one_very_long_visit_does_not_distort_the_estimate() -> None:
    """Criterion 3: thirty 5-minute visits and one 90-minute one give the range the thirty give.

    And the same data without trimming shows what the trimming prevented.
    """
    normal = _steady(5, 30)
    with_outlier = [VisitSample(90.0, 10), *normal]

    clean = estimate_wait(
        normal, people_ahead=6, at_hour=10, expected_service_minutes=5
    )
    trimmed = estimate_wait(
        with_outlier, people_ahead=6, at_hour=10, expected_service_minutes=5
    )
    untrimmed = estimate_wait(
        with_outlier,
        people_ahead=6,
        at_hour=10,
        expected_service_minutes=5,
        settings=replace(DEFAULT_SETTINGS, min_samples_to_trim=10**6),
    )

    assert trimmed.wait == clean.wait
    assert untrimmed.wait.high_minutes >= clean.wait.high_minutes + 10
    assert trim_outliers([5.0, 5.2, 4.9, 5.1, 90.0]) == [5.0, 5.2, 4.9, 5.1]


def test_a_morning_estimate_is_built_from_the_morning_not_from_the_afternoon() -> None:
    """Criterion 5: a fast morning and a slow afternoon in the same window give two different ranges."""
    samples = [*_steady(3, 30, hour=8), *_steady(10, 30, hour=14)]

    morning = estimate_wait(
        samples, people_ahead=6, at_hour=7.5, expected_service_minutes=6
    )
    afternoon = estimate_wait(
        samples, people_ahead=6, at_hour=14.5, expected_service_minutes=6
    )

    # 6.5 intervals: about 20 minutes in the morning, about 65 in the afternoon.
    assert morning.wait.low_minutes <= 20 <= morning.wait.high_minutes
    assert afternoon.wait.low_minutes <= 65 <= afternoon.wait.high_minutes
    assert morning.wait.high_minutes < afternoon.wait.low_minutes
    # Without the weighting the morning estimate is dragged towards the afternoon.
    flat = replace(DEFAULT_SETTINGS, bandwidth_hours=1000.0)
    unweighted = estimate_wait(
        samples, people_ahead=6, at_hour=7.5, expected_service_minutes=6, settings=flat
    )
    assert unweighted.wait.high_minutes > morning.wait.high_minutes


def test_the_estimator_is_a_pure_function_of_its_inputs() -> None:
    """Criterion 6: the same inputs give the same output, and the module imports nothing impure."""
    samples = _steady(6, 40)
    first = estimate_wait(
        samples, people_ahead=9, at_hour=11, expected_service_minutes=8
    )
    again = estimate_wait(
        list(samples), people_ahead=9, at_hour=11, expected_service_minutes=8
    )
    assert first == again

    tree = ast.parse(_ESTIMATE_MODULE.read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported <= {
        "math",
        "collections.abc",
        "dataclasses",
        "statistics",
        "typing",
        "src.commons.enums",
    }, imported


def test_negative_positions_and_expected_times_are_refused() -> None:
    """Programming errors, not patients' input: said loudly."""
    with pytest.raises(ValueError):
        estimate_wait([], people_ahead=-1, at_hour=9, expected_service_minutes=5)
    with pytest.raises(ValueError):
        estimate_wait([], people_ahead=0, at_hour=9, expected_service_minutes=0)


def test_accuracy_against_the_fixture_dataset_does_not_regress() -> None:
    """Criterion 5, kept honest in CI: two weekdays at three clinics, the full report is in the PR.

    The thresholds sit below the measured figures (87% coverage, 6.2 min mean absolute error over ten
    weekdays at every clinic), so this fails on a real regression rather than on noise.
    """
    groups = evaluate(days=2, clinics=3)
    everything = groups["all"].predictions
    covered = sum(prediction.covered for prediction in everything) / len(everything)
    error = sum(abs(p.midpoint - p.actual_minutes) for p in everything) / len(
        everything
    )
    naive = sum(abs(p.baseline_minutes - p.actual_minutes) for p in everything) / len(
        everything
    )
    assert len(everything) > 1_000
    assert covered >= 0.80
    assert error < naive / 2
