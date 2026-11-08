"""Write `evals/cases/clip_product.jsonl`: the clip judge's product check (`check_clip_product`
in `jobs/clip_judge.py`) against Julian's labels, from rounds already run. Free: it reads saved
files only and calls no model.

    uv run python -m evals.clip_product --labels <labels.json> --feedback <ids.json> \\
        --round 9 2026-11-04T00:00:00Z <round 9 page data> --round 11 ...  [--out <file>]

A round's page data is the JSON a grading page was built from (`ads[].scenes[].clips[]`, each
clip with its `ruling` and what was `sent` to the video model). The labels file is a list of
Julian's labels, one per clip: `{"id", "label", "reason", "graded_at", "from"}`, where `label`
is `pass`, `fail` or `null` (his fail was for something other than the product, so the clip is
left out of this question). The feedback file lists the id of every clip he said anything about
(a tap, words, or a note naming its scene): each one needs a label, and the command refuses to
write the file while any is missing. The time given with a round is the day it ran: the saved
rulings carry no time of their own.

A clip with no label of his still gets one in a single case, by his rule (Oct 25) "no feedback
= pass": the judge passed it, it is in a finished ad he watched, and he said nothing about it.
A clip the judge passed but
that was later replaced gets no label, and neither does any clip the judge failed or was unsure
about: those need his answer.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from evals.cases import FOLDER, BadCase, half_for, load

QUESTION = "clip_product"
MODEL = "gpt-6.1-sol"
# `PRODUCT_CHECK`'s wording has been the same since #353 (Nov 4), so every round from 9 on was
# judged by it (round 9 with the older rule for when the check is skipped). sha256, first 12.
JUDGE = "PRODUCT_CHECK sha256:ba5257f30f47"
NO_FEEDBACK = "no feedback (his rule, Oct 25)"


def case_id(round_name: str, ad: str, pk: int) -> str:
    return f"r{round_name}-{ad}-clip-{pk}"


def build(
    rounds: dict[str, tuple[str, dict[str, Any]]],
    labels: list[dict[str, Any]],
    feedback: set[str],
) -> list[dict[str, Any]]:
    """One case per labelled B-roll clip, in round, ad and scene order. `rounds` maps a round
    name to (the day it ran, its page data); `feedback` is every clip he said anything about."""
    by_id = {each["id"]: each for each in labels}
    if waiting := sorted(feedback - set(by_id)):
        raise ValueError(f"clips he gave feedback on still need his label: {', '.join(waiting)}")
    cases = []
    seen: set[str] = set()
    for round_name, (day, data) in rounds.items():
        for ad, made in data["ads"].items():
            for scene in made["scenes"]:
                for clip in scene["clips"]:
                    if not clip.get("broll"):
                        continue
                    this = case_id(round_name, ad, clip["pk"])
                    seen.add(this)
                    verdict = _verdict(clip, day)
                    label = by_id.get(this)
                    if label is None and this not in feedback:
                        label = _no_feedback(clip, verdict, day)
                    if label is None or label["label"] is None:
                        continue
                    cases.append(_case(this, round_name, ad, scene, clip, label, verdict))
    if unknown := sorted((set(by_id) | feedback) - seen):
        raise ValueError(f"labels or feedback for clips not in any round: {', '.join(unknown)}")
    return cases


def _verdict(clip: dict[str, Any], day: str) -> dict[str, str] | None:
    """The product check's saved answer, or None when it never ran on this clip."""
    ruling = clip.get("ruling") or {}
    said = (ruling.get("checks") or {}).get("product")
    if said is None:
        return None
    answers = (ruling.get("answers") or {}).get("product") or []
    faults = [each["fault"] for each in answers if isinstance(each, dict) and each.get("fault")]
    return {
        "verdict": said,
        "reason": " ".join(faults),
        "judge": JUDGE,
        "model": MODEL,
        "at": day,
    }


def _no_feedback(
    clip: dict[str, Any], verdict: dict[str, str] | None, day: str
) -> dict[str, Any] | None:
    if verdict and verdict["verdict"] == "pass" and clip.get("inAd"):
        return {"label": "pass", "reason": "", "graded_at": day, "from": NO_FEEDBACK}
    return None


def _case(
    this: str,
    round_name: str,
    ad: str,
    scene: dict[str, Any],
    clip: dict[str, Any],
    label: dict[str, Any],
    verdict: dict[str, str] | None,
) -> dict[str, Any]:
    sent = clip.get("sent") or {}
    photos = [{"file": p["file"], "label": p.get("label", "")} for p in sent.get("photos") or []]
    files = [
        clip["file"],
        *([sent["start"]] if sent.get("start") else []),
        *(p["file"] for p in photos),
    ]
    return {
        "id": this,
        "kind": "capability" if label["label"] == "fail" else "regression",
        "half": half_for(this),
        "inputs": {
            # Part of what the product check is shown, as the round's page data saved it: the
            # clip, the starting picture and every product photo with its job line. The full
            # handoff (product notes, clip length...) is in the box's gateway.ModelCall rows.
            "handoff": {
                "clip": clip["file"],
                "line": scene.get("line", ""),
                "prompt": sent.get("prompt", ""),
                "mode": sent.get("mode"),
                "starting_picture": sent.get("start"),
                "photos": photos,
            },
            # Every file is in the box's media folder.
            "files": [f"azure:{file}" for file in files],
        },
        "label": label["label"],
        "reason": label["reason"],
        "source": {
            "round": round_name,
            "ad": ad,
            "graded_at": label["graded_at"],
            "from": label["from"],
        },
        "verdicts": [verdict] if verdict else [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.clip_product", description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--feedback", type=Path, required=True)
    parser.add_argument(
        "--round",
        action="append",
        required=True,
        nargs=3,
        metavar=("NAME", "UTC_TIME", "PATH"),
        dest="rounds",
    )
    parser.add_argument("--out", type=Path, default=FOLDER / f"{QUESTION}.jsonl")
    args = parser.parse_args(argv)
    rounds = {}
    for name, day, path in args.rounds:
        if name in rounds:
            print(f"round {name} is given twice", file=sys.stderr)
            return 1
        rounds[name] = (day, json.loads(Path(path).read_text(encoding="utf-8")))
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    feedback = set(json.loads(args.feedback.read_text(encoding="utf-8")))
    try:
        cases = build(rounds, labels, feedback)
    except ValueError as wrong:
        print(wrong, file=sys.stderr)
        return 1
    args.out.write_text(
        "".join(json.dumps(each, ensure_ascii=False) + "\n" for each in cases), encoding="utf-8"
    )
    try:
        load(args.out)  # the same checks the 4-box count makes, so a bad file is caught here
    except BadCase as wrong:
        print(wrong, file=sys.stderr)
        return 1
    print(f"wrote {len(cases)} cases to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
