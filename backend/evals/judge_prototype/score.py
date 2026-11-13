"""The scoring math, pure: judge verdicts against Julian's labels on fails (spec #644)."""

from dataclasses import dataclass

# Julian's temporary trust bar (spec #644): recall >= 4/5, precision >= 7/10, kappa >= 0.7.
RECALL_BAR = 0.8
PRECISION_BAR = 0.7
KAPPA_BAR = 0.7


@dataclass(frozen=True)
class Counts:
    caught: int = 0  # both fail
    missed: int = 0  # Julian fail, judge pass
    false_alarms: int = 0  # Julian pass, judge fail
    both_pass: int = 0

    @property
    def total(self) -> int:
        return self.caught + self.missed + self.false_alarms + self.both_pass

    @property
    def recall(self) -> float | None:
        fails = self.caught + self.missed
        return self.caught / fails if fails else None

    @property
    def precision(self) -> float | None:
        flagged = self.caught + self.false_alarms
        return self.caught / flagged if flagged else None

    @property
    def kappa(self) -> float | None:
        """Cohen's kappa on fail/pass. None when chance agreement is total (one label only)."""
        n = self.total
        if not n:
            return None
        seen = (self.caught + self.both_pass) / n
        julian_fail = (self.caught + self.missed) / n
        judge_fail = (self.caught + self.false_alarms) / n
        chance = julian_fail * judge_fail + (1 - julian_fail) * (1 - judge_fail)
        if chance == 1:
            return None
        return (seen - chance) / (1 - chance)

    @property
    def trusted(self) -> bool:
        r, p, k = self.recall, self.precision, self.kappa
        return (
            r is not None
            and p is not None
            and k is not None
            and r >= RECALL_BAR
            and p >= PRECISION_BAR
            and k >= KAPPA_BAR
        )


def counted(pairs: list[tuple[str, str]]) -> Counts:
    """Counts from (Julian's label, judge's verdict) pairs, each pass or fail."""
    return Counts(
        caught=sum(j == "fail" and v == "fail" for j, v in pairs),
        missed=sum(j == "fail" and v == "pass" for j, v in pairs),
        false_alarms=sum(j == "pass" and v == "fail" for j, v in pairs),
        both_pass=sum(j == "pass" and v == "pass" for j, v in pairs),
    )


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def line(name: str, c: Counts) -> str:
    kappa = "n/a" if c.kappa is None else f"{c.kappa:.2f}"
    return (
        f"{name:<8} caught {c.caught} of {c.caught + c.missed} fails, "
        f"{c.false_alarms} false alarms (of {c.false_alarms + c.both_pass} passes) | "
        f"recall {_pct(c.recall)} ({c.caught}/{c.caught + c.missed}), "
        f"precision {_pct(c.precision)} ({c.caught}/{c.caught + c.false_alarms}), "
        f"kappa {kappa} | {'TRUSTED' if c.trusted else 'not trusted yet'}"
    )
