"""Judge kept clips through the command, with real media and a fake model."""

import io
import json
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from PIL import Image

from evals.regression.__main__ import main
from evals.regression.store import Store, write_json
from gateway.fake import FakeModel
from gateway.types import ModelRequest

from .conftest import picture


def answer(question: str, verdict: str = "pass") -> dict[str, Any]:
    result: dict[str, Any] = {
        "sees": "A real hand holds the mug.",
        "when": "0.2 s" if verdict == "fail" else "",
        "on_what": "hand" if verdict == "fail" else "nothing",
        "verdict": verdict,
    }
    if question == "C5":
        result.update(
            photo_sides=[{"photo": "photo 1", "side": "front", "parts": "handle on the right"}],
            shown_sides=[
                {
                    "side": "front",
                    "when": "0–6",
                    "parts": "handle on the right",
                    "same_side_photo": "photo 1",
                    "differences": "none",
                }
            ],
        )
    if question == "C13":
        result.pop("on_what")
        result["rows"] = [
            {
                "seconds": "0–6",
                "wrist_side": "left",
                "knuckles_side": "left",
                "fingertips_point": "right",
                "thumb": "above the handle",
                "possible": True,
            }
        ]
    return result


@pytest.fixture
def saved_clip(tmp_path: Path) -> Store:
    store = Store(tmp_path / "store")
    store.write_run(
        "baseline-2",
        {"name": "baseline-2", "scenarios": ["mug"], "finished_at": "2026-11-16T01:00:00Z"},
    )
    base = store.export_dir("baseline-2", "mug") / "mug"
    (base / "scene-1").mkdir(parents=True)
    subprocess.run(
        [
            settings.FFMPEG,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=lime:s=72x128:r=25",
            "-t",
            "6",
            "-pix_fmt",
            "yuv420p",
            str(base / "scene-1/kept.mp4"),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    (base / "page-photo-2.png").write_bytes(picture(40, 50, (180, 10, 160)))
    write_json(
        base / "regression.json",
        {
            "page_files": ["page-photo-2.png"],
            "scenes": [
                {
                    "number": 1,
                    "line": "Lift your coffee.",
                    "shows": "The mug in her hand.",
                    "clip": "scene-1/kept.mp4",
                }
            ],
        },
    )
    write_json(
        base / "grading-records.json",
        {
            "product": "Stoneware mug",
            "page_text": "A stoneware mug with a handle.",
            "library": [{"number": 2, "label": "The mug's front and handle."}],
            "producer": [{"instructions": "APP PROMPT"}],
            "warnings": ["APP VERDICT"],
        },
    )
    return store


def run_judge(store: Store, *args: str) -> int:
    return main(["--store", str(store.root), "judge", "baseline-2", *args])


def test_clip_questions_get_every_frame_under_the_picture_cap_and_keep_the_forms(
    saved_clip: Store,
    fake_model: FakeModel,
) -> None:
    for question in ("C5", "C6", "C7", "C8", "C9", "C10", "C13", "C14"):

        def passed(_: ModelRequest[Any], q: str = question) -> dict[str, Any]:
            return answer(q)

        fake_model.answer_unscripted(f"eval_judge_{question}", passed)
    fake_model.respond("eval_judge_C6", answer("C6", "fail"), answer("C6"), answer("C6", "fail"))

    assert run_judge(saved_clip) == 0

    results = saved_clip.read_judge_answers("baseline-2")["mug"]
    assert results["C6"]["s1"]["verdict"] == "fail"
    assert [ask["verdict"] for ask in results["C6"]["s1"]["asks"]] == ["fail", "pass", "fail"]
    assert results["C5"]["s1"]["asks"][0]["photo_sides"][0]["parts"] == "handle on the right"
    assert results["C5"]["s1"]["asks"][0]["shown_sides"][0]["differences"] == "none"
    assert results["C13"]["s1"]["asks"][0]["rows"][0]["wrist_side"] == "left"
    requests = {request.purpose: request for request in fake_model.asked}
    product = requests["eval_judge_C5"]
    assert len(product.images) == 31  # One shop photo, then 60 frames joined in pairs.
    assert "The mug's front and handle." in product.images[0].label
    assert Image.open(io.BytesIO(product.images[1].data)).size == (144, 128)
    frame_times = [
        float(value)
        for image in product.images[1:]
        for value in re.findall(r"\d+(?:\.\d+)?", image.label)
    ]
    assert frame_times == [n / 10 for n in range(60)]
    for question in ("C5", "C6", "C7", "C8", "C9", "C10"):
        request = requests[f"eval_judge_{question}"]
        assert [(image.label, image.data) for image in request.images] == [
            (image.label, image.data) for image in product.images
        ]
        before, after = request.handoff.around_images()
        assert "Lift your coffee." in before
        assert "APP PROMPT" not in before and "APP VERDICT" not in before
        assert after is not None and list(json.loads(after)) == ["question"]
    hand = requests["eval_judge_C13"]
    assert [(image.label, image.data) for image in hand.images] == [
        (image.label, image.data) for image in requests["eval_judge_C14"].images
    ]
    assert len(hand.images) == 30
    assert [float(re.findall(r"\d+(?:\.\d+)?", image.label)[0]) for image in hand.images] == [
        n / 5 for n in range(30)
    ]
    assert all(Image.open(io.BytesIO(image.data)).size == (72, 128) for image in hand.images)
    assert [request.purpose for request in fake_model.asked] == [
        "eval_judge_C5",
        "eval_judge_C6",
        "eval_judge_C6",
        "eval_judge_C6",
        "eval_judge_C7",
        "eval_judge_C8",
        "eval_judge_C9",
        "eval_judge_C10",
        "eval_judge_C13",
        "eval_judge_C14",
    ]


def test_no_holding_hand_is_saved_as_doesnt_apply(
    saved_clip: Store,
    fake_model: FakeModel,
) -> None:
    response = answer("C13", "doesnt_apply")
    response.update(rows=[], sees="No hand holds the product.")
    fake_model.respond("eval_judge_C13", response)

    assert run_judge(saved_clip, "--questions", "C13") == 0

    result = saved_clip.read_judge_answers("baseline-2")["mug"]["C13"]["s1"]
    assert result["verdict"] == "doesnt_apply"
    assert result["asks"][0]["rows"] == []
    assert result["asks"][0]["on_what"] == "hand"
    assert len(fake_model.asked) == 1


def test_resuming_a_clip_reasks_only_missing_votes_and_keeps_each_bill(
    saved_clip: Store,
    fake_model: FakeModel,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_model.respond("eval_judge_C6", answer("C6", "fail"), answer("C6"), answer("C6"))
    assert run_judge(saved_clip, "--questions", "C6") == 0
    first = saved_clip.read_judge_answers("baseline-2")
    result = first["mug"]["C6"]["s1"]
    assert result["verdict"] == "pass"
    assert all(ask["input_tokens"] == 1000 and ask["cost_usd"] > 0 for ask in result["asks"])

    assert run_judge(saved_clip, "--questions", "C6") == 0
    assert len(fake_model.asked) == 3
    assert "$0.000000 this command" in capsys.readouterr().out

    result["asks"] = result["asks"][:1]
    result.pop("verdict")
    saved_clip.write_judge_answers("baseline-2", first)
    fake_model.respond("eval_judge_C6", answer("C6", "fail"), answer("C6"))
    assert run_judge(saved_clip, "--questions", "C6") == 0
    resumed = saved_clip.read_judge_answers("baseline-2")["mug"]["C6"]["s1"]
    assert len(fake_model.asked) == 5
    assert resumed["verdict"] == "fail" and len(resumed["asks"]) == 3


def test_the_hand_question_can_answer_doesnt_apply_without_inventing_a_hand(
    saved_clip: Store,
    fake_model: FakeModel,
) -> None:
    fake_model.respond(
        "eval_judge_C13",
        {"rows": [], "sees": "No hand holds it.", "when": "", "verdict": "doesnt_apply"},
    )

    assert run_judge(saved_clip, "--questions", "C13") == 0

    result = saved_clip.read_judge_answers("baseline-2")["mug"]["C13"]["s1"]
    assert result["verdict"] == "doesnt_apply"
    assert len(result["asks"]) == 1
    assert result["asks"][0]["rows"] == []
    assert result["asks"][0]["on_what"] == "hand"


def test_different_clips_can_overlap_but_a_clips_questions_never_do(
    saved_clip: Store,
    fake_model: FakeModel,
) -> None:
    base = saved_clip.export_dir("baseline-2", "mug") / "mug"
    (base / "scene-2").mkdir()
    (base / "scene-2/kept.mp4").write_bytes((base / "scene-1/kept.mp4").read_bytes())
    record = json.loads((base / "regression.json").read_text())
    record["scenes"].append({"number": 2, "line": "Pour your coffee.", "clip": "scene-2/kept.mp4"})
    write_json(base / "regression.json", record)
    first_questions = threading.Barrier(2)
    lock = threading.Lock()
    active: set[str] = set()
    overlapping_clips = False

    def complete(request: ModelRequest[Any]) -> dict[str, Any]:
        nonlocal overlapping_clips
        line = request.handoff.model_dump()["spoken_line"]
        with lock:
            assert line not in active, "Two questions of the same clip were asked together"
            active.add(line)
            overlapping_clips |= len(active) == 2
        try:
            if request.purpose == "eval_judge_C5":
                first_questions.wait(timeout=10)
            return answer(request.purpose.removeprefix("eval_judge_"))
        finally:
            with lock:
                active.remove(line)

    questions = ("C5", "C6", "C7", "C8", "C9", "C10", "C13", "C14")
    for question in questions:
        fake_model.answer_unscripted(f"eval_judge_{question}", complete)

    assert run_judge(saved_clip) == 0
    assert overlapping_clips
    for line in ("Lift your coffee.", "Pour your coffee."):
        assert [
            request.purpose
            for request in fake_model.asked
            if request.handoff.model_dump()["spoken_line"] == line
        ] == [f"eval_judge_{question}" for question in questions]


def test_long_hand_and_action_inputs_join_frames_without_adding_page_photos(
    saved_clip: Store,
    fake_model: FakeModel,
) -> None:
    base = saved_clip.export_dir("baseline-2", "mug") / "mug"
    kept = base / "scene-1/long-kept.mp4"
    subprocess.run(
        [
            settings.FFMPEG,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=72x128:r=25",
            "-t",
            "11",
            "-pix_fmt",
            "yuv420p",
            str(kept),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    record = json.loads((base / "regression.json").read_text())
    record["scenes"][0]["clip"] = "scene-1/long-kept.mp4"
    write_json(base / "regression.json", record)
    fake_model.respond("eval_judge_C13", answer("C13"))
    fake_model.respond("eval_judge_C14", answer("C14"))

    assert run_judge(saved_clip, "--questions", "C13", "C14") == 0

    for request in fake_model.asked:
        assert len(request.images) == 28  # 55 frames, joined in pairs; the last is single.
        assert [
            float(value)
            for image in request.images
            for value in re.findall(r"\d+(?:\.\d+)?", image.label)
        ] == [n / 5 for n in range(55)]
        with Image.open(io.BytesIO(request.images[0].data)) as first:
            assert first.size == (144, 128)
            pixel = first.convert("RGB").getpixel((20, 20))
            assert isinstance(pixel, tuple)
            assert pixel[2] > 200  # The kept blue take.
