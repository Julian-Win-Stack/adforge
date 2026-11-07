"""The clip_product case export: which clips become cases, and with which label and verdict."""

import json
from pathlib import Path
from typing import Any

import pytest

from evals.cases import load
from evals.clip_product import JUDGE, NO_FEEDBACK, build, main


def clip(pk: int, said: str | None, in_ad: bool = True, fault: str = "") -> dict[str, Any]:
    ruling = None
    if said is not None:
        ruling = {"checks": {"product": said}, "answers": {"product": [{"fault": fault}]}}
    return {
        "pk": pk,
        "broll": True,
        "file": f"clip_{pk}.mp4",
        "inAd": in_ad,
        "why": "",
        "ruling": ruling,
        "sent": {
            "mode": "start_frame",
            "start": f"start_{pk}.png",
            "photos": [{"file": "jobs/x/photos/1.jpg", "label": "Image 1: the box front"}],
        },
    }


def rounds(*clips: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    talking = {"pk": 1, "broll": False, "file": "talk.mp4"}
    scene = {"n": 2, "line": "The line.", "clips": [talking, *clips]}
    return {"9": ("2026-11-04", {"ads": {"n01-box": {"scenes": [scene]}}})}


def label(pk: int, said: str | None, reason: str = "") -> dict[str, Any]:
    return {
        "id": f"r9-n01-box-clip-{pk}",
        "label": said,
        "reason": reason,
        "graded_at": "2026-11-08T00:00:00Z",
        "from": "runs/clip-product-eval/julian-grades/grades.json",
    }


def test_a_judge_pass_in_a_watched_ad_is_a_pass_by_his_rule() -> None:
    [case] = build(rounds(clip(10, "pass")), [])
    assert (case["label"], case["kind"], case["source"]["from"]) == (
        "pass",
        "regression",
        NO_FEEDBACK,
    )
    assert case["verdicts"][0] | {"reason": ""} == {
        "verdict": "pass",
        "reason": "",
        "judge": JUDGE,
        "model": "gpt-6.1-sol",
        "at": "2026-11-04",
    }


def test_clips_without_his_answer_are_left_out() -> None:
    replaced_pass = clip(10, "pass", in_ad=False)
    judge_fail = clip(11, "fail")
    judge_unsure = clip(12, "unsure")
    assert build(rounds(replaced_pass, judge_fail, judge_unsure), []) == []


def test_his_label_wins_and_a_fail_for_something_else_is_left_out() -> None:
    cases = build(
        rounds(clip(10, "pass"), clip(11, "fail", fault="The logo melts.")),
        [label(10, "fail", "the cap changes"), label(11, None)],
    )
    [case] = cases
    assert (case["id"], case["label"], case["kind"]) == ("r9-n01-box-clip-10", "fail", "capability")
    assert case["reason"] == "the cap changes"


def test_a_skipped_product_check_has_no_verdict() -> None:
    [case] = build(rounds(clip(10, None)), [label(10, "fail", "contents changed")])
    assert case["verdicts"] == []


def test_the_case_points_at_every_file_the_check_was_shown() -> None:
    [case] = build(rounds(clip(11, "fail", fault="The logo melts.")), [label(11, "pass")])
    assert case["inputs"]["files"] == [
        "azure:clip_11.mp4",
        "azure:start_11.png",
        "azure:jobs/x/photos/1.jpg",
    ]
    assert case["inputs"]["handoff"]["line"] == "The line."
    assert case["verdicts"][0]["reason"] == "The logo melts."


def test_a_label_for_an_unknown_clip_is_refused() -> None:
    with pytest.raises(ValueError, match="r9-n01-box-clip-99"):
        build(rounds(clip(10, "pass")), [label(99, "pass")])


def test_the_command_writes_a_case_file_the_boxes_can_load(tmp_path: Path) -> None:
    data = tmp_path / "r9.json"
    data.write_text(json.dumps(rounds(clip(10, "pass"), clip(11, "fail"))["9"][1]))
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([label(11, "fail", "[tap] really wrong")]))
    out = tmp_path / "clip_product.jsonl"

    assert (
        main(["--labels", str(labels), "--round", f"9:2026-11-04:{data}", "--out", str(out)]) == 0
    )
    assert [case.label for case in load(out)] == ["pass", "fail"]


def test_the_command_refuses_a_fail_without_his_words(tmp_path: Path) -> None:
    data = tmp_path / "r9.json"
    data.write_text(json.dumps(rounds(clip(11, "fail"))["9"][1]))
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps([label(11, "fail", "")]))

    out = tmp_path / "clip_product.jsonl"
    assert (
        main(["--labels", str(labels), "--round", f"9:2026-11-04:{data}", "--out", str(out)]) == 1
    )
