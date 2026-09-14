"""Measure the wait estimator against the fixture dataset (Issue 42).

An estimator nobody measured is a guess with a decimal point. This replays the demo dataset's
queues (``scripts/db/demo_dataset.py``: every clinic, every queue, a run of weekdays) as the
application would see them, and at the moment each patient joined asks
:func:`src.modules.queue.estimate.estimate_wait` how long they would wait, then compares the range
with the wait that patient actually had.

What the replay feeds the estimator is exactly what production feeds it, and nothing from the
future:

* **samples** are the completed visits (``done``) whose consultation had **finished before** the
  join, most recent first, each with its call interval by the recorder's rule (the gap since the
  previous call, counted only when the patient was already waiting at that call);
* **people ahead** are the tickets that had joined before and had not yet been called at that
  moment (cancelled tickets excluded: in the dataset they never leave the line otherwise);
* the **hour** is the join time in Johannesburg.

Reported, overall and by basis and confidence: how often the actual wait fell inside the range
(coverage), the error of the range's midpoint (mean and median absolute, and bias), and the average
width. The baseline is the number a naive system shows: expected minutes times people ahead.

Usage::

    python scripts/evaluate_wait_estimator.py            # the default settings, 10 weekdays
    python scripts/evaluate_wait_estimator.py --days 20 --window 40 --z 1.0

The fixture data is synthetic (a simulation with its own service times and arrival pattern), so
these numbers say the estimator behaves as designed on believable queues, not how it will score at
a real clinic. The pilot (Issue 108) is where it is measured for real.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Run as a script (``python scripts/evaluate_wait_estimator.py``): make ``src`` and ``scripts``
# importable from the repository root.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.db.demo_dataset import CLINICS, TicketStub, queues_for, ticket_history
from src.commons.enums import TicketStatus
from src.commons.time import APP_TIMEZONE
from src.modules.queue.estimate import (
    DEFAULT_SETTINGS,
    EstimatorSettings,
    VisitSample,
    WaitEstimate,
    estimate_wait,
)

#: The first weekday replayed. A Monday, so ``--days 5`` is one working week.
FIRST_DAY = date(2026, 8, 3)


@dataclass(frozen=True, slots=True)
class Prediction:
    """One patient: the estimate at their join, and what really happened."""

    estimate: WaitEstimate
    actual_minutes: float
    baseline_minutes: float

    @property
    def covered(self) -> bool:
        return (
            self.estimate.wait.low_minutes
            <= self.actual_minutes
            <= self.estimate.wait.high_minutes
        )

    @property
    def midpoint(self) -> float:
        return (self.estimate.wait.low_minutes + self.estimate.wait.high_minutes) / 2


@dataclass
class Scores:
    """Summary figures over a group of predictions."""

    label: str
    predictions: list[Prediction] = field(default_factory=list)

    def row(self) -> str:
        """One markdown table row."""
        p = self.predictions
        if not p:
            return f"| {self.label} | 0 | | | | | | |"
        errors = [x.midpoint - x.actual_minutes for x in p]
        absolute = [abs(e) for e in errors]
        widths = [x.estimate.wait.high_minutes - x.estimate.wait.low_minutes for x in p]
        naive = [abs(x.baseline_minutes - x.actual_minutes) for x in p]
        return (
            f"| {self.label} | {len(p):,} | {sum(x.covered for x in p) / len(p):.1%} "
            f"| {statistics.fmean(absolute):.1f} | {statistics.median(absolute):.1f} "
            f"| {statistics.fmean(errors):+.1f} | {statistics.fmean(widths):.1f} "
            f"| {statistics.fmean(naive):.1f} |"
        )


def _weekdays(count: int) -> Iterator[date]:
    day = FIRST_DAY
    while count:
        if day.weekday() < 5:
            yield day
            count -= 1
        day += timedelta(days=1)


def _samples_for_day(
    tickets: Sequence[TicketStub],
) -> list[tuple[datetime, VisitSample]]:
    """``(available from, sample)`` for each completed visit of one queue-day, by the recorder's rule."""
    called = sorted(
        (t for t in tickets if t.called_at is not None), key=lambda t: t.called_at
    )  # type: ignore[arg-type, return-value]
    out: list[tuple[datetime, VisitSample]] = []
    previous: TicketStub | None = None
    for ticket in called:
        assert ticket.called_at is not None
        if (
            ticket.status is TicketStatus.DONE
            and ticket.completed_at is not None
            and previous is not None
            and previous.called_at is not None
            and ticket.joined_at <= previous.called_at
        ):
            interval = (ticket.called_at - previous.called_at).total_seconds() / 60
            hour = ticket.called_at.astimezone(APP_TIMEZONE).hour
            out.append((ticket.completed_at, VisitSample(interval, hour)))
        previous = ticket
    return out


def evaluate(
    settings: EstimatorSettings = DEFAULT_SETTINGS,
    *,
    days: int = 10,
    clinics: int | None = None,
) -> dict[str, Scores]:
    """Replay the dataset and score every prediction. Keys: ``all``, and per basis and confidence."""
    groups: dict[str, Scores] = defaultdict(lambda: Scores(""))
    for site in CLINICS[:clinics]:
        for queue in queues_for(site):
            history: list[tuple[datetime, VisitSample]] = []
            for day in _weekdays(days):
                end = datetime.combine(day, time(23, 59), tzinfo=APP_TIMEZONE)
                tickets = ticket_history(site, queue, day, end)
                today = _samples_for_day(tickets)
                for ticket in tickets:
                    if (
                        ticket.called_at is None
                        or ticket.status is TicketStatus.CANCELLED
                    ):
                        continue
                    moment = ticket.joined_at
                    known = sorted(
                        (s for s in (*history, *today) if s[0] <= moment),
                        key=lambda pair: pair[0],
                        reverse=True,
                    )
                    ahead = sum(
                        1
                        for other in tickets
                        if other.joined_at < moment
                        and other.status is not TicketStatus.CANCELLED
                        and (other.called_at is None or other.called_at > moment)
                    )
                    local = moment.astimezone(APP_TIMEZONE)
                    estimate = estimate_wait(
                        [sample for _, sample in known],
                        people_ahead=ahead,
                        at_hour=local.hour + local.minute / 60,
                        expected_service_minutes=queue.service_minutes,
                        settings=settings,
                    )
                    prediction = Prediction(
                        estimate,
                        actual_minutes=ticket.wait_minutes or 0.0,
                        baseline_minutes=float(ahead * queue.service_minutes),
                    )
                    for key in (
                        "all",
                        f"basis={estimate.basis.value}",
                        f"confidence={estimate.confidence.value}",
                    ):
                        groups[key].predictions.append(prediction)
                history.extend(today)
    for key, scores in groups.items():
        scores.label = key
    return dict(groups)


def report(groups: dict[str, Scores]) -> str:
    """The results as a markdown table."""
    order = [
        "all",
        "basis=observed",
        "basis=expected",
        "confidence=high",
        "confidence=medium",
        "confidence=low",
    ]
    lines = [
        "| Group | Predictions | Actual inside the range | Mean abs. error (min) | Median abs. error | Bias | Mean width (min) | Naive `expected * ahead` mean abs. error |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines += [groups[key].row() for key in order if key in groups]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the replay with the given settings and print the table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--window", type=int, default=DEFAULT_SETTINGS.window)
    parser.add_argument(
        "--min-samples", type=float, default=DEFAULT_SETTINGS.min_effective_samples
    )
    parser.add_argument(
        "--bandwidth", type=float, default=DEFAULT_SETTINGS.bandwidth_hours
    )
    parser.add_argument("--z", type=float, default=DEFAULT_SETTINGS.band_z)
    parser.add_argument("--fence", type=float, default=DEFAULT_SETTINGS.fence)
    parser.add_argument(
        "--fallback-cv", type=float, default=DEFAULT_SETTINGS.fallback_cv
    )
    args = parser.parse_args(argv)
    settings = replace(
        DEFAULT_SETTINGS,
        window=args.window,
        min_effective_samples=args.min_samples,
        bandwidth_hours=args.bandwidth,
        band_z=args.z,
        fence=args.fence,
        fallback_cv=args.fallback_cv,
    )
    print(settings)
    print(report(evaluate(settings, days=args.days)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
