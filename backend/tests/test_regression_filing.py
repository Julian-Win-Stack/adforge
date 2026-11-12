"""Julian's saved taps become repeatable ground-truth cases (issue #604)."""

import json
from pathlib import Path

import pytest

from evals.cases import half_for, load
from evals.regression import filing
from evals.regression.__main__ import main as regression_main
from evals.regression.cases import make_case
from evals.regression.store import Store, read_json, write_json
from gateway.fake import FakeModel


@pytest.fixture
def saved_plan(tmp_path: Path) -> Store:
    store = Store(tmp_path / "project-files" / "regression")
    store.write_run("after", {"name": "after", "scenarios": ["bottle"]})
    base = store.export_dir("after", "bottle") / "bottle"
    write_json(
        base / "regression.json",
        {
            "scenario": {
                "key": "bottle",
                "kind": "full",
                "link": "https://shop.example/bottle",
                "situation": "The cap should stay on the bottle.",
            },
            "scenes": [
                {"number": 1, "line": "Take your drink with you.", "shows": "She holds it."}
            ],
            "notes": [],
        },
    )
    store.write_grades("after", {"bottle__plan": {"grade": "pass", "note": "", "at": "now"}})
    return store


def test_a_passed_plan_passes_every_julian_question_at_its_stage(
    saved_plan: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = tmp_path / "cases"

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0

    assert sorted(path.stem for path in cases.glob("*.jsonl")) == [
        "plan_claims_backed",
        "plan_real_use",
        "viewer_knows_product",
    ]
    backed = load(cases / "plan_claims_backed.jsonl")[0]
    assert backed.id == "reg-after-bottle-plan_claims_backed-plan"
    assert backed.label == "pass"
    assert backed.kind == "regression"
    assert backed.inputs["handoff"] == {
        "question": "Every claim, number and price comes from the page or the owner",
        "scenario": "bottle",
        "situation": "The cap should stay on the bottle.",
        "stage": "plan",
        "item": "plan",
    }
    assert backed.inputs["files"] == [
        "project-files:"
        + str(saved_plan.export_dir("after", "bottle") / "bottle" / "regression.json")
    ]
    assert backed.source["from"] == str(saved_plan.run_dir("after") / "grades.json")
    assert backed.verdicts == ()
    assert "3 cases added" in capsys.readouterr().out


def test_complaints_are_filed_under_the_stage_questions_or_start_one_from_his_words(
    saved_plan: Store,
    tmp_path: Path,
    fake_model: FakeModel,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = saved_plan.export_dir("after", "bottle") / "bottle"
    (base / "scene-1").mkdir()
    (base / "scene-1" / "first.mp4").write_bytes(b"kept clip")
    (base / "ad.mp4").write_bytes(b"finished ad")
    record = read_json(base / "regression.json")
    record["scenes"][0]["clip"] = "scene-1/first.mp4"
    write_json(base / "regression.json", record)
    saved_plan.write_grades(
        "after",
        {
            "bottle__plan": {"grade": "pass", "note": "", "at": "plan-time"},
            "bottle__s1__clip": {
                "grade": "fail",
                "note": "the cap floats above the bottle",
                "at": "clip-time",
            },
            "bottle__ad": {
                "grade": "fail",
                "note": "the music feels too frantic for this quiet drink",
                "at": "ad-time",
            },
            "bottle__no-worse": {"grade": "fail", "note": "worse", "at": "now"},
            "bottle__photo-abc": {"grade": "pass", "note": "this", "at": "now"},
        },
    )
    fake_model.respond(
        "file_regression_complaint",
        {"questions": ["clip_possible"], "new_question": None},
        {
            "questions": [],
            "new_question": {
                "id": "ad_music_fits_mood",
                "question": "Does the music fit the mood of the ad?",
            },
        },
    )
    cases = tmp_path / "cases"

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0

    impossible = load(cases / "clip_possible.jsonl")[0]
    assert impossible.id == "reg-after-bottle-clip_possible-s1"
    assert impossible.label == "fail"
    assert impossible.reason == "the cap floats above the bottle"
    assert impossible.kind == "capability"
    assert impossible.source["graded_at"] == "clip-time"
    assert impossible.inputs["handoff"]["line"] == "Take your drink with you."
    assert impossible.inputs["handoff"]["shows"] == "She holds it."
    assert load(cases / "clip_real_product.jsonl")[0].label == "pass"
    assert load(cases / "clip_substance_real.jsonl")[0].label == "pass"
    new = load(cases / "ad_music_fits_mood.jsonl")[0]
    assert new.inputs["handoff"]["question"] == "Does the music fit the mood of the ad?"
    assert new.inputs["handoff"]["stage"] == "ad"
    assert new.reason == "the music feels too frantic for this quiet drink"
    assert new.inputs["files"] == ["project-files:" + str(base / "ad.mp4")]
    assert len([case for path in cases.glob("*.jsonl") for case in load(path)]) == 10
    assert {call.purpose for call in fake_model.asked} == {"file_regression_complaint"}
    offered = fake_model.asked[0].handoff.model_dump()["questions"]
    assert {question["id"] for question in offered} == {
        "clip_real_product",
        "clip_possible",
        "clip_substance_real",
        "clip_camera_natural",
    }
    printed = capsys.readouterr().out
    assert '"the cap floats above the bottle" → clip_possible' in printed
    assert "ad_music_fits_mood (new)" in printed

    first_files = {path.name: path.read_bytes() for path in cases.glob("*.jsonl")}
    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    assert {path.name: path.read_bytes() for path in cases.glob("*.jsonl")} == first_files
    assert len(fake_model.asked) == 2
    assert "0 cases added" in capsys.readouterr().out


def test_every_reached_output_is_filed_even_when_it_has_no_tap(
    saved_plan: Store, tmp_path: Path
) -> None:
    base = saved_plan.export_dir("after", "bottle") / "bottle"
    record = read_json(base / "regression.json")
    record["page_files"] = ["page.txt", "page-photo.png"]
    record["scenes"][0].update(picture="scene-1/start-picture.png", clip="scene-1/first.mp4")
    record["notes"] = [
        {
            "number": 1,
            "text": "Make that opening calmer.",
            "before": "ad.mp4",
            "after": "note-1.mp4",
        }
    ]
    for name in (
        "page.txt",
        "page-photo.png",
        "scene-1/start-picture.png",
        "scene-1/first.mp4",
        "ad.mp4",
        "note-1.mp4",
    ):
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"saved output")
    write_json(base / "regression.json", record)
    saved_plan.write_grades("after", {})
    cases = tmp_path / "cases"

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0

    filed = {path.stem: load(path) for path in cases.glob("*.jsonl")}
    assert len(filed) == 14
    assert all(case.label == "pass" for entries in filed.values() for case in entries)
    assert filed["page_facts_this_product"][0].inputs["files"] == [
        "project-files:" + str(base / "page.txt"),
        "project-files:" + str(base / "page-photo.png"),
    ]
    assert filed["picture_real_product"][0].inputs["handoff"]["item"] == "s1"
    note = filed["note_done"][0]
    assert note.inputs["handoff"]["item"] == "note1"
    assert note.inputs["handoff"]["note"] == "Make that opening calmer."
    assert note.inputs["files"] == [
        "project-files:" + str(base / "ad.mp4"),
        "project-files:" + str(base / "note-1.mp4"),
    ]


def test_a_fail_without_words_stays_unfiled_until_the_owner_explains_it(
    saved_plan: Store,
    tmp_path: Path,
    fake_model: FakeModel,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cases = tmp_path / "cases"
    saved_plan.write_grades("after", {"bottle__plan": {"grade": "fail", "note": "", "at": "now"}})

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 1
    assert "bottle__plan needs words" in capsys.readouterr().err
    assert not list(cases.glob("*.jsonl"))
    assert "bottle__plan" not in read_json(saved_plan.run_dir("after") / "filed-grades.json")
    assert not fake_model.asked

    saved_plan.write_grades(
        "after",
        {"bottle__plan": {"grade": "fail", "note": "That isn't how it opens.", "at": "later"}},
    )
    fake_model.respond(
        "file_regression_complaint", {"questions": ["plan_real_use"], "new_question": None}
    )
    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    assert load(cases / "plan_real_use.jsonl")[0].reason == "That isn't how it opens."


@pytest.mark.parametrize("tap", [None, "pass"])
def test_a_written_complaint_is_not_lost_when_the_owner_did_not_tap_fail(
    saved_plan: Store, tmp_path: Path, fake_model: FakeModel, tap: str | None
) -> None:
    saved_plan.write_grades(
        "after",
        {"bottle__plan": {"grade": tap, "note": "That isn't how it opens.", "at": "now"}},
    )
    fake_model.respond(
        "file_regression_complaint", {"questions": ["plan_real_use"], "new_question": None}
    )
    cases = tmp_path / "cases"

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    assert load(cases / "plan_real_use.jsonl")[0].label == "fail"


def test_a_run_without_exported_grades_cannot_be_filed_as_all_passes(
    saved_plan: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (saved_plan.run_dir("after") / "grades.json").unlink()
    cases = tmp_path / "cases"

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 1
    assert "grades.json" in capsys.readouterr().err
    assert not list(cases.glob("*.jsonl"))


def test_moving_a_complaint_changes_the_two_labels_without_losing_other_cases(
    saved_plan: Store,
    tmp_path: Path,
    fake_model: FakeModel,
    capsys: pytest.CaptureFixture[str],
) -> None:
    saved_plan.write_grades(
        "after",
        {"bottle__plan": {"grade": "fail", "note": "It claims too much.", "at": "now"}},
    )
    fake_model.respond(
        "file_regression_complaint", {"questions": ["plan_real_use"], "new_question": None}
    )
    cases = tmp_path / "cases"
    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    original = "reg-after-bottle-plan_real_use-plan"
    source_path = cases / "plan_real_use.jsonl"
    other = read_json(saved_plan.run_dir("after") / "filed-grades.json")["bottle__plan"]["cases"][
        "plan_real_use"
    ].copy()
    other.update(id="older-unrelated", half=half_for("older-unrelated"))
    unrelated = "  " + json.dumps(other) + "  \n"
    with source_path.open("a") as file:
        file.write(unrelated)

    assert (
        regression_main(
            [
                "--store",
                str(saved_plan.root),
                "move-case",
                original,
                "plan_claims_backed",
                "--cases-dir",
                str(cases),
            ]
        )
        == 0
    )

    source = {case.id: case for case in load(source_path)}
    assert source[original].label == "pass"
    assert source[original].reason == ""
    assert source[original].kind == "regression"
    assert source_path.read_text().endswith(unrelated)
    destination = load(cases / "plan_claims_backed.jsonl")
    assert len(destination) == 1
    moved = destination[0]
    assert moved.id == "reg-after-bottle-plan_claims_backed-plan"
    assert moved.half == half_for(moved.id)
    assert moved.label == "fail"
    assert moved.reason == "It claims too much."
    assert moved.inputs["handoff"]["question"] == (
        "Every claim, number and price comes from the page or the owner"
    )
    printed = capsys.readouterr().out
    assert "plan_real_use → pass" in printed
    assert "plan_claims_backed → fail" in printed
    corrected_files = {path.name: path.read_bytes() for path in cases.glob("*.jsonl")}
    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    assert {path.name: path.read_bytes() for path in cases.glob("*.jsonl")} == corrected_files


def test_filing_cannot_overwrite_the_old_clip_product_cases_or_name_a_code_question(
    saved_plan: Store, tmp_path: Path, fake_model: FakeModel
) -> None:
    cases = tmp_path / "cases"
    cases.mkdir()
    old_path = cases / "clip_product.jsonl"
    raw = make_case(
        run="older",
        scenario="bottle",
        question="clip_product",
        item="s1",
        label="pass",
        reason="",
        handoff={},
        files=[],
        graded_at="before",
        source="older grades",
    )
    original = json.dumps(raw).encode()
    old_path.write_bytes(original)
    saved_plan.write_grades(
        "after",
        {"bottle__plan": {"grade": "fail", "note": "It claims too much.", "at": "now"}},
    )
    fake_model.respond(
        "file_regression_complaint",
        {"questions": ["plan_has_demo"], "new_question": None},
        {"questions": ["plan_claims_backed"], "new_question": None},
    )

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    assert load(cases / "plan_claims_backed.jsonl")[0].label == "fail"
    assert old_path.read_bytes() == original
    assert not (cases / "plan_has_demo.jsonl").exists()
    assert len(fake_model.asked) == 2


def test_existing_round_exports_use_the_kept_take_and_keep_the_plan_inputs(
    saved_plan: Store, tmp_path: Path
) -> None:
    export = saved_plan.export_dir("after", "bottle")
    base = export / "bottle"
    (base / "regression.json").unlink()
    scene = base / "scene-1"
    scene.mkdir()
    (scene / "inputs.txt").write_text(
        "SCENE: 1\nLINE: Bring it along.\nSHOWS: She holds it.\nKIND: broll\n"
    )
    (scene / "first.mp4").write_bytes(b"first attempt")
    (scene / "remake-2.mp4").write_bytes(b"kept attempt")
    (scene / "start-picture.png").write_bytes(b"starting picture")
    (base / "ad.mp4").write_bytes(b"first ad")
    (base / "ad-latest.mp4").write_bytes(b"kept ad")
    write_json(base / "ad.json", {"product_url": "https://shop.example/bottle"})
    write_json(
        export / "results.json",
        {
            "bottle:1": {
                "ad_folder": "bottle",
                "scene": 1,
                "line": "Bring it along.",
                "shows": "She holds it.",
                "kept": {"remake": 2},
            }
        },
    )
    cases = tmp_path / "cases"

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    assert load(cases / "clip_real_product.jsonl")[0].inputs["files"] == [
        "project-files:" + str(scene / "remake-2.mp4")
    ]
    assert load(cases / "presenter_real_person.jsonl")[0].inputs["files"] == [
        "project-files:" + str(base / "ad-latest.mp4")
    ]
    assert load(cases / "plan_claims_backed.jsonl")[0].inputs["files"] == [
        "project-files:" + str(scene / "inputs.txt")
    ]


def test_a_missing_saved_output_is_reported_instead_of_filed_as_a_pass(
    saved_plan: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = saved_plan.export_dir("after", "bottle") / "bottle"
    record = read_json(base / "regression.json")
    record["scenes"][0]["picture"] = "scene-1/gone.png"
    write_json(base / "regression.json", record)
    cases = tmp_path / "cases"

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 1
    assert "Saved graded output is missing" in capsys.readouterr().err
    assert not list(cases.glob("*.jsonl"))


def test_grades_for_missing_items_are_not_silently_discarded_but_other_takes_are_archived_only(
    saved_plan: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    saved_plan.write_grades(
        "after",
        {
            "bottle__s9__clip": {"grade": "fail", "note": "a missing scene", "at": "now"},
            "bottle__s1__take1": {"grade": "fail", "note": "old failed try", "at": "now"},
            "round__notes": {"grade": None, "note": "round thoughts", "at": "now"},
        },
    )
    cases = tmp_path / "cases"

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 1
    error = capsys.readouterr().err
    assert "bottle__s9__clip has no saved graded output" in error
    assert "take1" not in error
    assert "round__notes" not in error


def test_positive_words_are_kept_with_the_pass_and_do_not_start_a_question(
    saved_plan: Store, tmp_path: Path, fake_model: FakeModel
) -> None:
    saved_plan.write_grades(
        "after",
        {"bottle__plan": {"grade": "pass", "note": "This plan is clear.", "at": "now"}},
    )
    fake_model.respond("file_regression_complaint", {"questions": [], "new_question": None})
    cases = tmp_path / "cases"

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    entries = [case for path in cases.glob("*.jsonl") for case in load(path)]
    assert len(entries) == 3
    assert all(case.label == "pass" and case.reason == "This plan is clear." for case in entries)
