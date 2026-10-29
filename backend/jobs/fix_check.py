"""How the clip judge's answers about the shop owner's change settle whether a fix's clip
has it (#131): the judge is asked beside the clip they noted (jobs.clip_judge, Julian
2026-10-14), and its answers are settled here, for the fixing agent's tries."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .picture_check import ASKS, FAILS_TO_FAIL

Verdict = Literal["passed", "failed", "unchecked"]


@dataclass(frozen=True)
class Settled:
    """What the judge's answers settled: passed or failed, or unchecked when too few of its
    asks were answered to agree; why it failed; and each answer had, as kept."""

    verdict: Verdict
    reason: str
    answers: list[dict[str, Any]]


def settle_all(answers: Sequence[dict[str, Any] | None]) -> Settled:
    """`settle` over answers all asked at once, in ask order, keeping every answer had: each
    was paid for. Most of them must agree: the clip judge asks once (clip_judge.ASKS), so its
    one answer settles it; a ruling made while it asked 3 times settles on 2, as it did."""
    settled = settle(
        lambda number: answers[number - 1], asks=len(answers), needed=len(answers) // 2 + 1
    )
    return Settled(
        settled.verdict, settled.reason, [answer for answer in answers if answer is not None]
    )


def settle(
    ask: Callable[[int], dict[str, Any] | None],
    *,
    asks: int = ASKS,
    needed: int = FAILS_TO_FAIL,
) -> Settled:
    """Ask the judge with `ask`, from ask 1, until `needed` of its answers agree, at most
    `asks` times. An ask gives the judge's answer, or None when none could be had: the safety
    filter blocked it, or it couldn't be read. Too few answers that agree leave it
    unchecked: a fix is neither passed nor failed on a guess. A fail's reason is its first
    failing answer's."""
    answers: list[dict[str, Any]] = []
    for number in range(1, asks + 1):
        answer = ask(number)
        if answer is not None:
            answers.append(answer)
        fails = [answer for answer in answers if answer["verdict"] == "not fixed"]
        if len(fails) >= needed:
            return Settled("failed", fails[0]["still_wrong"], answers)
        if len(answers) - len(fails) >= needed:
            return Settled("passed", "", answers)
    return Settled("unchecked", "", answers)
