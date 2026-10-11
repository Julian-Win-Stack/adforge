"""Run the app's start-picture judge (`jobs.work.judge_starting_picture`: its prompt, model
and 2-of-3 asks, through the real gateway) on the pictures Julian graded, and print how it
agrees with him. The cases are in picture_check_cases.json: each picture with the shop photo
it was drawn from, "fail" for one he called wrong. As shipped (2026-10-11, $3.02 for every
case, the wrong ones 5 times each): 6 of the 10 wrong pictures caught all 5 times, the
other 4, all toilets, never (docs/limitations.md), and none of the 52 good ones failed.
Needs a migrated database. From backend/:

    uv run python -m evals.calibrate_picture_check [--runs N] [--out FILE] [<case id> ...]
"""

import argparse
import json
import os
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
    picture: Path
    shop_photo: Path


def load_cases(path: Path = CASES) -> list[PictureCase]:
    return [
        PictureCase(
            id=case["id"],
            label=case["label"],
            picture=Path(case["picture"]),
            shop_photo=Path(case["shop_photo"]),
        )
        for case in json.loads(path.read_text())["cases"]
    ]


Judge = Callable[[PictureCase], list[str]]


def judge_as_the_app_does(case: PictureCase) -> list[str]:
    """What the app's judge finds wrong with `case`'s picture: nothing when it passes."""
    picture = file_store.save(case.picture.name, case.picture.read_bytes())
    photo = file_store.save(case.shop_photo.name, case.shop_photo.read_bytes())
    try:
        return judge_starting_picture(None, picture, photo, about=case.id)
    finally:
        # Each thread's own connection, closed so a long run doesn't hold them all open.
        connection.close()


def report(cases: list[PictureCase], runs: int, judge: Judge) -> list[str]:
    """Each wrong picture judged `runs` times and each good one once, as lines saying how
    often each wrong one was caught and every good one failed, with why."""
    asks = [(case, run) for case in cases for run in range(runs if case.label == "fail" else 1)]
    with ThreadPoolExecutor(AT_ONCE) as pool:
        found = list(pool.map(lambda ask: judge(ask[0]), asks))
    caught: dict[str, int] = {}
    wrongly_failed = []
    for (case, _), problems in zip(asks, found, strict=True):
        if case.label == "fail":
            caught[case.id] = caught.get(case.id, 0) + bool(problems)
        elif problems:
            wrongly_failed.append(f"  WRONGLY FAILED {case.id}: {' / '.join(problems)}")
    good = [case for case in cases if case.label == "pass"]
    every_time = sum(n == runs for n in caught.values())
    return [
        f"wrong pictures caught every time: {every_time}/{len(caught)}",
        *(f"  {case_id}: caught {n}/{runs}" for case_id, n in caught.items()),
        f"good pictures failed: {len(wrongly_failed)}/{len(good)}",
        *wrongly_failed,
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
