"""Ask the change check and the judge on a saved note run, with real clips and a fake model."""

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from pytest_django import Settings

from evals.judge.prompts import question_text
from evals.regression.__main__ import main
from evals.regression.store import Store, write_json
from gateway.fake import FakeModel
from gateway.gateway import use_model
from gateway.types import BlockedBySafetyFilter, ModelRequest

from .conftest import picture

RUN = "notes-1"
CASE = "mug-s2"
NOTE = "In the part where she pours, the mug's handle is on the wrong side."
CLIP_QUESTIONS = ("C5", "C6", "C7", "C8", "C9", "C10", "C13", "C14")


def video(path: Path, colour: str, seconds: float, size: str = "72x128") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [settings.FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"color=c={colour}:s={size}:r=25"]
        + ["-t", str(seconds), "-pix_fmt", "yuv420p", str(path)],
        check=True,
        capture_output=True,
        timeout=60,
    )


def save_case(
    store: Store,
    case: str = CASE,
    *,
    seconds: float = 2,
    scenes: int = 2,
    new_ad: bool = True,
    size: str = "72x128",
) -> Path:
    """A finished note case in the shared layout: scene 2 remade, every other scene kept."""
    folder = store.scenario_dir(RUN, case)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "answers.json", {"answers": {}, "cost_usd": 0, "minutes": None})
    store.dump_path(RUN, case).write_bytes(b"dump")
    base = store.export_dir(RUN, case) / case
    colours = ["red", "blue", "yellow", "white", "black", "orange"]
    rows = []
    for number in range(1, scenes + 1):
        before = f"note-1/before-s{number}.mp4"
        video(base / before, colours[number - 1], seconds, size)
        changed = new_ad and number == 2
        after = f"note-1/after-s{number}.mp4"
        if changed:
            video(base / after, "lime", seconds, size)
        elif new_ad:
            (base / after).write_bytes((base / before).read_bytes())
        rows.append(
            {
                "number": number,
                "before_clip": before,
                "after_clip": after if new_ad else None,
                "changed": changed,
            }
        )
    (base / "page-photo-1.png").write_bytes(picture(40, 50, (180, 10, 160)))
    note: dict[str, Any] = {"number": 1, "text": NOTE, "before": "note-1/before.mp4"}
    if new_ad:
        note["after"] = "note-1/after.mp4"
    write_json(
        base / "regression.json",
        {
            "page_files": ["page-photo-1.png"],
            "scenes": [
                {
                    "number": row["number"],
                    "line": f"Line {row['number']}.",
                    "shows": f"Shows {row['number']}.",
                    "clip": row["after_clip"] or row["before_clip"],
                }
                for row in rows
            ],
            "notes": [note],
            "note_case": {"id": case, "expected_scenes": [2], "scenes": rows},
        },
    )
    write_json(
        base / "grading-records.json",
        {
            "product": "Stoneware mug",
            "page_text": "A stoneware mug with a handle.",
            "library": [{"number": 1, "label": "The mug's front and handle."}],
            "producer": [{"instructions": "APP PROMPT"}],
            "warnings": ["APP VERDICT"],
        },
    )
    (folder / "done").write_text("done\n")
    return base


@pytest.fixture
def note_run(tmp_path: Path) -> Store:
    store = Store(tmp_path / "store")
    store.write_run(RUN, {"kind": "notes", "name": RUN, "cases": [CASE]})
    save_case(store)
    return store


def judge_notes(store: Store, *args: str) -> int:
    return main(["--store", str(store.root), "judge-notes", RUN, *args])


def made(verdict: str = "made") -> dict[str, Any]:
    return {
        "unhappy_about": "The handle is on the wrong side of the mug.",
        "note_scenes": [
            {
                "scene": 2,
                "first_ad": "The handle is on the left while she pours.",
                "new_ad": "The handle is on the right.",
                "still_shows": verdict == "not_made",
            }
        ],
        "sees": "In the new ad's scene 2 the handle is on the right, as in the photos.",
        "verdict": verdict,
    }


def clip_answer(question: str) -> dict[str, Any]:
    result: dict[str, Any] = {"sees": "Fine.", "when": "", "verdict": "pass"}
    if question == "C5":
        result.update(photo_sides=[], shown_sides=[], on_what="nothing")
    elif question == "C13":
        result.update(rows=[])
    else:
        result["on_what"] = "nothing"
    return result


def every_clip_question_passes(fake_model: FakeModel) -> None:
    for question in CLIP_QUESTIONS:

        def passed(_: ModelRequest[Any], q: str = question) -> dict[str, Any]:
            return clip_answer(q)

        fake_model.answer_unscripted(f"eval_judge_{question}", passed)


def test_the_change_check_sees_the_note_and_both_whole_ads_and_only_remade_clips_are_judged(
    note_run: Store, fake_model: FakeModel, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_model.respond("eval_judge_N1", made())
    every_clip_question_passes(fake_model)

    assert judge_notes(note_run) == 0

    check = next(r for r in fake_model.asked if r.purpose == "eval_judge_N1")
    labels = [image.label for image in check.images]
    assert "The mug's front and handle." in labels[0]
    frames = []
    for label in labels[1:]:
        match = re.fullmatch(r"(First ad|New ad), scene (\d+): (.*)", label)
        assert match is not None, label
        side, scene, seconds = match.groups()
        frames += [(side, int(scene), float(s)) for s in re.findall(r"\d+(?:\.\d+)?", seconds)]
    # Every frame of every scene of both ads at 2 frames a second, in order, none skipped.
    assert frames == [
        (side, scene, n / 2)
        for side in ("First ad", "New ad")
        for scene in (1, 2)
        for n in range(4)
    ]
    before, after = check.handoff.around_images()
    told = before + (after or "") + check.instructions + " ".join(labels)
    assert NOTE in before and "Stoneware mug" in before
    assert after is not None and json.loads(after) == {"question": question_text("N1")}
    # Only the note, product name, photos and frames: no page text, plan or app decisions.
    for hidden in ("expected", "APP PROMPT", "APP VERDICT", "Line 2.", "stoneware mug with"):
        assert hidden not in told

    answers = note_run.read_judge_answers(RUN)[CASE]
    change = answers["N1"]["note"]
    assert change["verdict"] == "made" and len(change["asks"]) == 1
    ask = change["asks"][0]
    assert ask["sees"].startswith("In the new ad's scene 2")
    assert ask["unhappy_about"] and ask["note_scenes"][0]["scene"] == 2
    assert ask["model"] and ask["input_tokens"] == 1000 and ask["cost_usd"] > 0
    assert change["judge"].startswith("N1:")
    # Only the remade scene's new clip gets the eight clip questions.
    assert {question: list(items) for question, items in answers.items() if question != "N1"} == {
        question: ["s2"] for question in CLIP_QUESTIONS
    }
    clip_request = next(r for r in fake_model.asked if r.purpose == "eval_judge_C6")
    assert "Line 2." in clip_request.handoff.around_images()[0]
    assert sorted(r.purpose for r in fake_model.asked) == sorted(
        ["eval_judge_N1", *(f"eval_judge_{q}" for q in CLIP_QUESTIONS)]
    )
    assert "this command" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("replies", "counted"),
    [(["not_made", "made", "not_made"], "not_made"), (["not_made", "made", "made"], "made")],
)
def test_not_made_is_asked_twice_more_and_counts_at_two_of_three(
    note_run: Store, fake_model: FakeModel, replies: list[str], counted: str
) -> None:
    fake_model.respond("eval_judge_N1", *(made(reply) for reply in replies))
    every_clip_question_passes(fake_model)

    assert judge_notes(note_run) == 0

    change = note_run.read_judge_answers(RUN)[CASE]["N1"]["note"]
    assert [ask["verdict"] for ask in change["asks"]] == replies
    assert change["verdict"] == counted
    assert sum(r.purpose == "eval_judge_N1" for r in fake_model.asked) == 3


def test_every_ask_is_saved_before_the_next_and_a_rerun_resumes(
    note_run: Store, fake_model: FakeModel, capsys: pytest.CaptureFixture[str]
) -> None:
    every_clip_question_passes(fake_model)
    fake_model.respond(
        "eval_judge_N1",
        made("not_made"),
        BlockedBySafetyFilter("Refused", input_tokens=1000, output_tokens=10),
    )

    assert judge_notes(note_run) == 1

    change = note_run.read_judge_answers(RUN)[CASE]["N1"]["note"]
    assert [ask["verdict"] for ask in change["asks"]] == ["not_made"]
    assert "verdict" not in change and "Refused" in change["error"]
    assert change["failed_asks"][0]["cost_usd"] > 0
    assert f"{CASE} note N1: not judged" in capsys.readouterr().err

    fake_model.respond("eval_judge_N1", made("not_made"), made("not_made"))
    asked_before = len(fake_model.asked)
    assert judge_notes(note_run) == 0
    change = note_run.read_judge_answers(RUN)[CASE]["N1"]["note"]
    assert [ask["verdict"] for ask in change["asks"]] == ["not_made"] * 3
    assert change["verdict"] == "not_made"
    # Only the two missing re-asks were paid for; finished clip answers were not asked again.
    assert [r.purpose for r in fake_model.asked[asked_before:]] == ["eval_judge_N1"] * 2

    assert judge_notes(note_run) == 0
    assert len(fake_model.asked) == asked_before + 2
    assert "$0.000000 this command" in capsys.readouterr().out


def test_a_case_with_no_new_ad_is_skipped(tmp_path: Path, fake_model: FakeModel) -> None:
    store = Store(tmp_path / "store")
    store.write_run(RUN, {"kind": "notes", "name": RUN, "cases": ["gave-up"]})
    save_case(store, "gave-up", new_ad=False)

    assert judge_notes(store) == 0

    assert fake_model.asked == []
    assert store.read_judge_answers(RUN) == {}


def test_only_named_cases_are_judged_and_unfinished_ones_are_skipped(
    note_run: Store, fake_model: FakeModel, capsys: pytest.CaptureFixture[str]
) -> None:
    save_case(note_run, "other")
    (note_run.scenario_dir(RUN, "unfinished")).mkdir(parents=True)
    note_run.write_run(RUN, {"kind": "notes", "name": RUN, "cases": [CASE, "other", "unfinished"]})
    fake_model.respond("eval_judge_N1", made())
    every_clip_question_passes(fake_model)

    assert judge_notes(note_run, "--only", "other", "unfinished") == 0

    assert list(note_run.read_judge_answers(RUN)) == ["other"]
    assert "unfinished: not finished; skipped" in capsys.readouterr().out


def test_a_missing_azure_account_refuses_to_start(
    note_run: Store,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
    sentry_events: list[dict[str, Any]],
) -> None:
    settings.OPENAI_TEXT_ON_AZURE = False
    with use_model(None):
        assert judge_notes(note_run) == 1

    assert "Cannot run: AZUREE_OPENAI_KEY" in capsys.readouterr().out
    assert not (note_run.run_dir(RUN) / "judge-answers.json").exists()
    assert any(event["tags"]["kind"] == "eval_judge_unavailable" for event in sentry_events)


def test_an_unreadable_clip_is_an_error_naming_the_case(
    note_run: Store,
    fake_model: FakeModel,
    sentry_events: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = note_run.export_dir(RUN, CASE) / CASE
    (base / "note-1/before-s1.mp4").write_bytes(b"not a video")
    every_clip_question_passes(fake_model)

    assert judge_notes(note_run) == 1

    assert "error" in note_run.read_judge_answers(RUN)[CASE]["N1"]["note"]
    assert not any(r.purpose == "eval_judge_N1" for r in fake_model.asked)
    assert any(event["tags"]["kind"] == "eval_judge_unavailable" for event in sentry_events)
    assert f"{CASE} note N1: not judged" in capsys.readouterr().err


def test_a_35_second_ad_pair_fits_the_picture_cap_without_skipping_frames(
    tmp_path: Path, fake_model: FakeModel
) -> None:
    store = Store(tmp_path / "store")
    store.write_run(RUN, {"kind": "notes", "name": RUN, "cases": [CASE]})
    save_case(store, seconds=8.75, scenes=4, size="36x64")
    fake_model.respond("eval_judge_N1", made())
    every_clip_question_passes(fake_model)

    assert judge_notes(store) == 0

    check = next(r for r in fake_model.asked if r.purpose == "eval_judge_N1")
    assert len(check.images) <= 50
    times = [
        second
        for image in check.images[1:]
        for second in re.findall(r"\d+(?:\.\d+)?", image.label.split(": ", 1)[1])
    ]
    assert len(times) == 2 * 4 * 18  # 70 seconds at 2 frames a second, every frame kept.
    assert len(check.images) - 1 < len(times)  # So consecutive frames share a picture.


def test_a_change_check_that_says_nothing_it_sees_is_not_counted(
    note_run: Store, fake_model: FakeModel
) -> None:
    every_clip_question_passes(fake_model)
    blank = made()
    blank["sees"] = "   "
    fake_model.respond("eval_judge_N1", blank)

    assert judge_notes(note_run) == 1

    change = note_run.read_judge_answers(RUN)[CASE]["N1"]["note"]
    assert change["asks"] == [] and "verdict" not in change and change["error"]
