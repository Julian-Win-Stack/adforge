"""Run the app's start-picture judge (`jobs.work.judge_starting_picture`: its prompt, model
and 2-of-3 asks, through the real gateway) on the pictures Julian graded, and print how it
agrees with him. The cases are in picture_check_cases.json: each picture with the shop photo
it was drawn from, "fail" for one he called wrong. A "pass" whose clip failed for something
else, or was never graded ("no_picture_complaint"), was never passed by him: a fail there is
listed for Julian to look at, not counted against the judge. As shipped (2026-10-11, $3.02 for every
case, the wrong ones 5 times each): 6 of the 10 wrong pictures caught all 5 times, the
other 4, all toilets, never (docs/limitations.md), and none of the 52 good ones failed.
Needs a migrated database. From backend/:

    uv run python -m evals.calibrate_picture_check [--runs N] [--out FILE] [<case id> ...]
"""

import argparse
import json
import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
django.setup()

from django.db import connection  # noqa: E402

from adforge import file_store  # noqa: E402
from jobs.work import judge_starting_picture  # noqa: E402

CASES = Path(__file__).resolve().parent / "picture_check_cases.json"

# Each picture is judged by ASKS asks of an Azure vision model; 4 at once stays far under its
# limits (reference/rate-limits.md).
AT_ONCE = 4


@dataclass(frozen=True)
class PictureCase:
    id: str
    label: Literal["pass", "fail"]
    # Which of Julian's grades the label comes from (evals/picture-judge/build_set.py).
    graded_as: str
    picture: Path
    shop_photo: Path


def load_cases(path: Path = CASES) -> list[PictureCase]:
    return [
        PictureCase(
            id=case["id"],
            label=case["label"],
            graded_as=case["graded_as"],
            picture=Path(case["picture"]),
            shop_photo=Path(case["shop_photo"]),
        )
        for case in json.loads(path.read_text())["cases"]
    ]


@dataclass(frozen=True)
class Judged:
    # What the judge found wrong: nothing when it passed.
    problems: list[str]
    # Asks whose answer couldn't be had (blocked, unusable, the service down): the app counts
    # them as not checked, so a picture none of whose asks were answered "passes".
    unchecked: int = 0


Judge = Callable[[PictureCase], Judged]


class _CountUnchecked(logging.Handler):
    """Counts, per picture, the asks `judge_starting_picture` logs as unchecked."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        if "went unchecked" in record.msg and isinstance(record.args, tuple):
            with self._lock:
                about = str(record.args[1])
                self.counts[about] = self.counts.get(about, 0) + 1

    def take(self, about: str) -> int:
        with self._lock:
            return self.counts.pop(about, 0)


_unchecked = _CountUnchecked()
logging.getLogger("jobs.work").addHandler(_unchecked)


def judge_as_the_app_does(case: PictureCase) -> Judged:
    """What the app's judge finds wrong with `case`'s picture, and how many of its asks went
    unchecked. Each run's picture is named apart, so runs at once never share a count."""
    about = f"{case.id} #{threading.get_ident()}"
    picture = file_store.save(case.picture.name, case.picture.read_bytes())
    photo = file_store.save(case.shop_photo.name, case.shop_photo.read_bytes())
    try:
        problems = judge_starting_picture(None, picture, photo, about=about)
        return Judged(problems, _unchecked.take(about))
    finally:
        # Each thread's own connection, closed so a long run doesn't hold them all open.
        connection.close()


def report(cases: list[PictureCase], runs: int, judge: Judge) -> list[str]:
    """Each wrong picture judged `runs` times and each good one once, as lines saying how
    often each wrong one was caught, every good one failed, with why, every picture with no
    grade of its own the judge failed, for Julian, and every judgement that can't be trusted:
    one with an unchecked ask, or one that errored, so a run the service dropped never reads
    as a clean pass."""
    asks = [(case, run) for case in cases for run in range(runs if case.label == "fail" else 1)]

    def judge_or_error(case: PictureCase) -> Judged | Exception:
        # One picture's error never stops a whole paid run.
        try:
            return judge(case)
        except Exception as error:
            return error

    with ThreadPoolExecutor(AT_ONCE) as pool:
        found = list(pool.map(lambda ask: judge_or_error(ask[0]), asks))
    caught: dict[str, int] = {}
    wrongly_failed = []
    for_julian = []
    untrusted = []
    for (case, _), judged in zip(asks, found, strict=True):
        if isinstance(judged, Exception):
            untrusted.append(f"  ERROR {case.id}: {judged!r}")
            continue
        if judged.unchecked:
            untrusted.append(f"  {case.id}: {judged.unchecked} ask(s) unchecked")
        if case.label == "fail":
            caught[case.id] = caught.get(case.id, 0) + bool(judged.problems)
        elif judged.problems and case.graded_as == "no_picture_complaint":
            for_julian.append(f"  FOR JULIAN {case.id}: {' / '.join(judged.problems)}")
        elif judged.problems:
            wrongly_failed.append(f"  WRONGLY FAILED {case.id}: {' / '.join(judged.problems)}")
    good = [c for c in cases if c.label == "pass" and c.graded_as != "no_picture_complaint"]
    ungraded = [c for c in cases if c.label == "pass" and c.graded_as == "no_picture_complaint"]
    every_time = sum(n == runs for n in caught.values())
    return [
        f"wrong pictures caught every time: {every_time}/{len(caught)}",
        *(f"  {case_id}: caught {n}/{runs}" for case_id, n in caught.items()),
        f"good pictures failed: {len(wrongly_failed)}/{len(good)}",
        *wrongly_failed,
        f"pictures with no grade of their own failed: {len(for_julian)}/{len(ungraded)}",
        *for_julian,
        f"judgements not to trust: {len(untrusted)}",
        *untrusted,
    ]


def main(runs: int, out: Path | None, ids: list[str]) -> None:
    cases = [case for case in load_cases() if not ids or case.id in ids]
    lines = report(cases, runs, judge_as_the_app_does)
    print("\n".join(lines))
    if out is not None:
        out.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5, help="times each wrong picture is judged")
    parser.add_argument("--out", type=Path)
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args()
    main(args.runs, args.out, args.ids)
