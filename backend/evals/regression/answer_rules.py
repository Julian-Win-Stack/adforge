"""Pure rules for the regression eval's code answers."""

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .store import Answer, AnswerItem, Verdict


@dataclass(frozen=True)
class HeardCut:
    item: str
    words: Sequence[Mapping[str, Any]]
    voice_start: float
    voice_end: float
    voice_clip_start: float
    voice_clip_end: float


def counted(passed: bool, reason: str, item: str) -> Answer:
    verdict: Verdict = "pass" if passed else "fail"
    return {
        "verdict": verdict,
        "reason": reason,
        "items": [{"item": item, "verdict": verdict, "reason": reason}],
    }


def plan_length(lines: list[str], target: int | None) -> Answer:
    from jobs import checks

    try:
        fit = (
            checks.default_length_fit(lines)
            if target is None
            else checks.target_length_fit(lines, target)
        )
    except ValueError as error:
        return counted(False, str(error), "plan")
    limit = (
        checks.DEFAULT_AD_MAX_SECONDS
        if target is None
        else target + checks.LENGTH_ALLOWANCE_SECONDS
    )
    return counted(
        fit.fits, f"The script is estimated at {fit.seconds:g} s; limit {limit:g} s.", "plan"
    )


def finished_length(seconds: float, target: int | None) -> Answer:
    from jobs import checks

    limit = (
        checks.DEFAULT_AD_MAX_SECONDS
        if target is None
        else target + checks.LENGTH_ALLOWANCE_SECONDS
    )
    return counted(
        math.isfinite(seconds) and 0 < seconds <= limit,
        f"The finished file measures {seconds:g} s; limit {limit:g} s.",
        "ad",
    )


def combine(items: Sequence[AnswerItem], empty_reason: str = "No items to check.") -> Answer:
    verdict: Verdict = "pass"
    priority: tuple[Verdict, ...] = ("fail", "needs_label", "not_reached")
    for possible in priority:
        if any(item["verdict"] == possible for item in items):
            verdict = possible
            break
    relevant = [item for item in items if item["verdict"] == verdict]
    return {
        "verdict": verdict,
        "reason": "; ".join(f"{item['item']}: {item['reason']}" for item in relevant)
        or empty_reason,
        "items": list(items),
    }


def photo_labels(photos: Sequence[tuple[str, bytes]], labels: Mapping[str, str]) -> Answer:
    items: list[AnswerItem] = []
    for file, data in photos:
        digest = sha256(data).hexdigest()
        label = labels.get(digest)
        verdict: Verdict
        if label == "this":
            verdict, reason = "pass", "Julian labelled this as the exact product."
        elif label == "not":
            verdict, reason = "fail", "Julian labelled this as not the exact product."
        elif label is None:
            verdict, reason = "needs_label", "This kept photo has not been labelled."
        else:
            raise ValueError(f"Unknown photo label for {digest}: {label!r}")
        items.append(
            {"item": f"photo-{digest}", "verdict": verdict, "reason": reason, "file": file}
        )
    return combine(items, "No photos are marked as the exact product.")


def dead_air(cuts: Sequence[HeardCut]) -> Answer:
    """Map heard words to the ad, clipping to each cut's audible range. Ignore the tail."""
    if not cuts:
        return counted(False, "The finished ad has no played words.", "ad")
    problems: dict[str, list[str]] = {cut.item: [] for cut in cuts}
    timeline: list[tuple[float, float, str]] = []
    for cut in cuts:
        shift = cut.voice_start - cut.voice_clip_start
        heard = 0
        for word in cut.words:
            start, end = word.get("start"), word.get("end")
            if (
                not isinstance(start, int | float)
                or not isinstance(end, int | float)
                or not math.isfinite(start)
                or not math.isfinite(end)
            ):
                problems[cut.item].append("A transcript word has no usable time.")
                continue
            start, end = max(cut.voice_start, start + shift), min(cut.voice_end, end + shift)
            if end > start:
                timeline.append((start, end, cut.item))
                heard += 1
        if not heard:
            problems[cut.item].append("No timed words in the played voice.")
    latest = 0.0
    for start, end, item in sorted(timeline):
        gap = start - latest
        if gap > 1.0 + 1e-9:
            problems[item].append(f"{gap:g} s silence before the word at {start:g} s.")
        latest = max(latest, end)
    return combine(
        [
            {
                "item": item,
                "verdict": "fail" if faults else "pass",
                "reason": " ".join(faults) if faults else "No silence longer than 1.0 s.",
            }
            for item, faults in problems.items()
        ]
    )


def untouched_clips(
    before: Mapping[str, str], after: Mapping[str, str], touched: Collection[str]
) -> Answer:
    return combine(
        [
            {
                "item": item,
                "verdict": "pass" if after.get(item) == file else "fail",
                "reason": (
                    "The untouched scene kept the same clip file."
                    if after.get(item) == file
                    else f"The untouched scene changed from {file!r} to {after.get(item)!r}."
                ),
            }
            for item, file in before.items()
            if item not in touched
        ],
        "The note touched every scene.",
    )


def last_words(item: str, words: Sequence[Mapping[str, Any]], voice_end: float) -> AnswerItem:
    end = words[-1].get("end") if words else None
    if not isinstance(end, int | float) or not math.isfinite(end):
        return {"item": item, "verdict": "fail", "reason": "No timed last word."}
    passed = end <= voice_end + 0.05 + 1e-9
    return {
        "item": item,
        "verdict": "pass" if passed else "fail",
        "reason": f"Last word ends at {end:g} s; voice is cut at {voice_end:g} s.",
    }


def missing_words(line: str, said: str) -> list[str]:
    """Words of the tidied line absent from the transcript's ordered subsequence."""
    from jobs.word_check import tidy

    heard = tidy(said).split()
    missing = []
    at = 0
    for word in tidy(line).split():
        try:
            found = heard.index(word, at)
        except ValueError:
            missing.append(word)
        else:
            at = found + 1
    return missing
