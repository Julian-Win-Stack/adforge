"""The fact check eval: every case file in the eval home loads and counts, and the re-check
command asks today's fact check once per script, as the app does, and saves its verdict on
each case for the 4-box count."""

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import PIL.Image
import pytest

from evals import boxes, fact_check
from evals.cases import FOLDER, half_for, load, questions
from gateway.gateway import use_model
from gateway.types import ModelReply, ModelRequest


def load_one(raw: dict[str, Any]) -> Any:
    """One case, read as the case file reads it, with a verdict so it is listed."""
    import tempfile

    raw["verdicts"] = [
        {"verdict": "pass", "reason": "", "judge": "j", "model": "m", "at": "2026-11-08T00:00:00Z"}
    ]
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "fact_check.jsonl"
        path.write_text(json.dumps(raw) + "\n")
        return load(path)[0]


def test_every_case_file_in_the_eval_home_loads_and_its_boxes_print() -> None:
    for question in questions(FOLDER):
        assert load(FOLDER / f"{question}.jsonl"), question
    printed = io.StringIO()
    with redirect_stdout(printed):
        assert boxes.main([]) == 0
    for question in questions(FOLDER):
        assert question in printed.getvalue()


def script(planted: str) -> dict[str, Any]:
    """A fact check handoff of a two-scene script, as the app sends it; scene 2 shows."""
    return {
        "page_text": "Mug, $20. Keeps coffee hot for 3 hours.",
        "conversation": [{"by": "user", "text": "https://shop.example/mug"}],
        "product_colour": "black",
        "lines": [
            {"scene": 1, "line": planted, "shows": None, "usage": None, "result": None},
            {
                "scene": 2,
                "line": "Get it for $20.",
                "shows": "She sips from the mug.",
                "usage": None,
                "result": None,
            },
        ],
        "video_transcripts": [],
    }


def case(case_id: str, scene: int, planted: str, files: list[str]) -> dict[str, Any]:
    return {
        "id": case_id,
        "kind": "regression",
        "half": half_for(case_id),
        "inputs": {"handoff": script(planted), "scene": scene, "files": files},
        "label": "fail" if scene == 1 else "pass",
        "reason": "made-up hours: the page says 3" if scene == 1 else "",
        "source": {"round": "12", "ad": "mug", "graded_at": "", "from": "planted"},
        "verdicts": [],
    }


class FakeFactCheck:
    """Fails any line that says "12 hours"; records what each call was shown."""

    name = "fake"

    def __init__(self) -> None:
        self.asked: list[ModelRequest[Any]] = []

    def complete(self, request: ModelRequest[Any]) -> ModelReply[Any]:
        self.asked.append(request)
        lines = request.handoff.model_dump()["lines"]
        verdicts = [
            {
                "scene": line["scene"],
                "verdict": "wrong" if "12 hours" in line["line"] else "ok",
                "wrong": "line" if "12 hours" in line["line"] else None,
                "problem": "The page says 3 hours." if "12 hours" in line["line"] else None,
                "page_says": "Keeps coffee hot for 3 hours."
                if "12 hours" in line["line"]
                else None,
            }
            for line in lines
        ]
        answer = {"decision": "checked", "reason": "Checked.", "lines": verdicts}
        return ModelReply(request.output.model_validate(answer), 100, 10)


@pytest.fixture
def photo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(fact_check, "PROJECT_FILES", tmp_path)
    (tmp_path / "mug").mkdir()
    PIL.Image.new("RGB", (8, 8)).save(tmp_path / "mug" / "photo-0.png")
    return "project-files:mug/photo-0.png"


def test_the_recheck_asks_once_per_script_and_saves_each_scenes_verdict(
    tmp_path: Path, photo: str
) -> None:
    path = tmp_path / f"{fact_check.QUESTION}.jsonl"
    cases = [
        case("mug-planted-hours", 1, "Keeps coffee hot for 12 hours.", [photo]),
        case("mug-planted-hours-scene-2", 2, "Keeps coffee hot for 12 hours.", [photo]),
        case("mug-real", 1, "Keeps coffee hot for 3 hours.", [photo]),
    ]
    path.write_text("".join(json.dumps(each) + "\n" for each in cases))
    model = FakeFactCheck()

    with use_model(model):
        assert fact_check.main([], folder=tmp_path) == 0

    assert len(model.asked) == 2
    # The script shows something in scene 2, so its photos are shown, labelled as the app does.
    assert [image.label for image in model.asked[0].images] == ["Photo 1"]
    saved = {each.id: each.verdicts for each in load(path)}
    assert [v.verdict for v in saved["mug-planted-hours"]] == ["fail"]
    assert "3 hours" in saved["mug-planted-hours"][0].reason
    assert [v.verdict for v in saved["mug-planted-hours-scene-2"]] == ["pass"]
    assert [v.verdict for v in saved["mug-real"]] == ["pass"]
    assert saved["mug-real"][0].judge.startswith("fact_check:")

    with use_model(model):
        assert fact_check.main(["--ids", "mug-real"], folder=tmp_path) == 0

    again = {each.id: each.verdicts for each in load(path)}
    assert len(again["mug-real"]) == 2
    assert len(again["mug-planted-hours"]) == 1


def test_a_script_where_the_person_talks_throughout_is_checked_without_photos(
    tmp_path: Path, photo: str
) -> None:
    talking = case("mug-talking", 1, "Keeps coffee hot for 3 hours.", [photo])
    talking["inputs"]["handoff"]["lines"][1]["shows"] = None
    path = tmp_path / f"{fact_check.QUESTION}.jsonl"
    path.write_text(json.dumps(talking) + "\n")
    model = FakeFactCheck()

    with use_model(model):
        assert fact_check.main([], folder=tmp_path) == 0

    assert model.asked[0].images == ()


def test_an_unknown_case_id_is_refused(tmp_path: Path, photo: str) -> None:
    path = tmp_path / f"{fact_check.QUESTION}.jsonl"
    path.write_text(json.dumps(case("mug-real", 1, "Hot for 3 hours.", [photo])) + "\n")

    assert fact_check.main(["--ids", "nope"], folder=tmp_path) == 1


def test_a_missed_planted_case_is_listed_as_planted_not_as_julians_grade(
    tmp_path: Path, photo: str
) -> None:
    path = tmp_path / f"{fact_check.QUESTION}.jsonl"
    path.write_text(json.dumps(case("mug-planted", 1, "Hot for 3 hours.", [photo])) + "\n")
    with use_model(FakeFactCheck()):
        fact_check.main([], folder=tmp_path)

    printed = boxes.report(fact_check.QUESTION, boxes.count(load(path)), None)

    assert "Planted: fail: made-up hours" in printed
    assert "Julian:" not in printed


def test_a_script_whose_check_fails_to_run_is_named_and_the_others_are_kept(
    tmp_path: Path, photo: str
) -> None:
    path = tmp_path / f"{fact_check.QUESTION}.jsonl"
    cases = [
        case("mug-planted-hours", 1, "Keeps coffee hot for 12 hours.", [photo]),
        case("mug-broken", 1, "Hot for 3 hours.", ["project-files:mug/missing.png"]),
    ]
    path.write_text("".join(json.dumps(each) + "\n" for each in cases))
    printed = io.StringIO()

    with use_model(FakeFactCheck()), redirect_stdout(printed):
        assert fact_check.main([], folder=tmp_path) == 1

    saved = {each.id: each.verdicts for each in load(path)}
    assert [v.verdict for v in saved["mug-planted-hours"]] == ["fail"]
    assert saved["mug-broken"] == ()


def test_the_unchanged_scenes_of_each_script_are_counted_so_a_too_strict_check_shows(
    tmp_path: Path, photo: str
) -> None:
    path = tmp_path / f"{fact_check.QUESTION}.jsonl"
    strict = case("mug-planted", 2, "Keeps coffee hot for 12 hours.", [photo])
    path.write_text(json.dumps(strict) + "\n")
    printed = io.StringIO()

    with use_model(FakeFactCheck()), redirect_stdout(printed):
        assert fact_check.main([], folder=tmp_path) == 0

    assert "Unchanged scenes in the same scripts (no label): 1 of 1 failed." in printed.getvalue()
    assert "mug-planted scene 1" in printed.getvalue()


def test_a_case_julian_graded_is_listed_as_his() -> None:
    graded = load_one(
        {
            **case("mug-real", 1, "Hot for 3 hours.", []),
            "label": "fail",
            "reason": "wrong",
            "source": {
                "round": "12",
                "ad": "mug",
                "graded_at": "2026-11-08T00:00:00Z",
                "from": "grades.md",
            },
        }
    )

    printed = boxes.report(fact_check.QUESTION, boxes.count([graded]), None)

    assert "Julian: fail: wrong" in printed
