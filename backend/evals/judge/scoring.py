"""Pure agreement math for the eval judge. A positive label means a real flaw."""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class TrustBar:
    recall: float = 0.8
    precision: float = 0.7
    kappa: float = 0.7


TRUST_BAR = TrustBar()


@dataclass(frozen=True)
class Score:
    caught: int = 0
    missed: int = 0
    false_alarms: int = 0
    both_passed: int = 0

    @property
    def total(self) -> int:
        return self.caught + self.missed + self.false_alarms + self.both_passed

    @property
    def agreed(self) -> int:
        return self.caught + self.both_passed

    @property
    def recall(self) -> float | None:
        fails = self.caught + self.missed
        return self.caught / fails if fails else None

    @property
    def precision(self) -> float | None:
        fails = self.caught + self.false_alarms
        return self.caught / fails if fails else None

    @property
    def kappa(self) -> float | None:
        if not self.total:
            return None
        # The expected agreements from each grader's marginal counts. Keeping this
        # in counts avoids rounding an undefined all-one-label score into 1.
        expected = (self.caught + self.missed) * (self.caught + self.false_alarms) + (
            self.both_passed + self.false_alarms
        ) * (self.both_passed + self.missed)
        denominator = self.total**2 - expected
        if not denominator:
            return None
        return (self.agreed * self.total - expected) / denominator

    @property
    def trusted(self) -> bool:
        recall, precision, kappa = self.recall, self.precision, self.kappa
        return (
            recall is not None
            and precision is not None
            and kappa is not None
            and recall >= TRUST_BAR.recall
            and precision >= TRUST_BAR.precision
            and kappa >= TRUST_BAR.kappa
        )


def score(pairs: Iterable[tuple[str, str]]) -> Score:
    """Count (Julian's label, judge's verdict) pairs, each pass or fail."""
    caught = missed = false_alarms = both_passed = 0
    for label, verdict in pairs:
        if label not in ("pass", "fail") or verdict not in ("pass", "fail"):
            raise ValueError("Agreement needs pass/fail pairs; leave unjudged/doesn't apply out")
        if label == "fail":
            if verdict == "fail":
                caught += 1
            else:
                missed += 1
        elif verdict == "fail":
            false_alarms += 1
        else:
            both_passed += 1
    return Score(caught, missed, false_alarms, both_passed)
