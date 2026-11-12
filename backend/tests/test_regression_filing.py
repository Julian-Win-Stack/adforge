"""Julian's saved taps become repeatable ground-truth cases (issue #604)."""

import json
from pathlib import Path

import pytest

from evals.cases import half_for, load
from evals.regression import filing
from evals.regression.__main__ import main as regression_main
from evals.regression.cases import make_case
from evals.regression.reports import promote, report
from evals.regression.store import Store, read_json, write_json
from gateway.fake import FakeModel

from .regression_questions import HIS_QUESTIONS, all_filed, filed, his_questions


def test_filing_a_graded_plan_protects_it_against_a_later_complaint(
    saved_plan: Store, tmp_path: Path, fake_model: FakeModel
) -> None:
    cases = tmp_path / "pipeline-cases"
    scenario = "bottle"

    def finished_plan(run: str, started_at: str) -> None:
        saved_plan.write_run(
            run,
            {"name": run, "scenarios": [scenario], "to": "plans", "started_at": started_at},
        )
        saved_plan.write_answers(
            run,
            scenario,
            {
                "run": run,
                "scenario": scenario,
                "answers": {"plan_has_demo": {"verdict": "pass", "reason": "A demo", "items": []}},
                "first_try": {"scenes": 1, "first_passed": 0, "clips": 0},
                "cost_usd": 0.5,
            },
        )
        saved_plan.dump_path(run, scenario).write_bytes(b"dump")
        saved_plan.mark_done(run, scenario)

    finished_plan("after", "2026-11-12T01:00:00+00:00")
    assert promote(saved_plan, "after", cases_dir=cases) == 1
    assert saved_plan.read_baselines() == {}

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    assert promote(saved_plan, "after", cases_dir=cases) == 0
    assert saved_plan.read_baselines() == {scenario: "after"}

    next_run = "after-change"
    finished_plan(next_run, "2026-11-12T02:00:00+00:00")
    old_export = saved_plan.export_dir("after", scenario) / scenario / "regression.json"
    write_json(
        saved_plan.export_dir(next_run, scenario) / scenario / "regression.json",
        read_json(old_export),
    )
    saved_plan.write_grades(
        next_run,
        {
            "bottle__plan": {
                "grade": "fail",
                "note": "The script invents a leakproof claim.",
                "at": "2026-11-12T02:01:00+00:00",
            }
        },
    )
    # His first complaint names a question; the baseline's plan, graded with no complaint,
    # passes it, so the new run's fail shows as broke.
    fake_model.respond(
        "file_regression_complaint",
        {
            "questions": [],
            "new_question": {
                "id": "plan_no_invented_claims",
                "question": "Does the script claim only what the page says?",
            },
        },
    )
    assert filing.main([next_run, "--cases-dir", str(cases)], store=saved_plan) == 0
    baseline = load(cases / "plan_no_invented_claims.jsonl")
    assert {(case.source["round"], case.label) for case in baseline} == {
        ("after", "pass"),
        (next_run, "fail"),
    }
    result = report(saved_plan, next_run, cases_dir=cases)
    assert not result.passed
    assert "broke: plan_no_invented_claims" in result.text
    assert "The script invents a leakproof claim." in result.text
    assert promote(saved_plan, next_run, cases_dir=cases) == 1
    assert saved_plan.read_baselines() == {scenario: "after"}


@pytest.mark.parametrize("unresolved_first", [False, True])
def test_a_new_question_covers_earlier_items_but_never_turns_an_unfiled_fail_into_a_pass(
    saved_plan: Store, tmp_path: Path, fake_model: FakeModel, unresolved_first: bool
) -> None:
    cases = tmp_path / "pipeline-cases"
    saved_plan.write_run(
        "after",
        {
            "name": "after",
            "scenarios": ["bottle", "mug"],
            "to": "plans",
            "started_at": "2026-11-12T01:00:00+00:00",
        },
    )
    original = read_json(saved_plan.export_dir("after", "bottle") / "bottle" / "regression.json")
    original["scenario"].update(key="mug", situation="The owner should see how its controls work.")
    write_json(saved_plan.export_dir("after", "mug") / "mug" / "regression.json", original)
    for scenario in ("bottle", "mug"):
        saved_plan.write_answers(
            "after",
            scenario,
            {
                "run": "after",
                "scenario": scenario,
                "answers": {"plan_has_demo": {"verdict": "pass", "reason": "A demo", "items": []}},
                "first_try": {"scenes": 1, "first_passed": 0, "clips": 0},
                "cost_usd": 0.5,
            },
        )
        saved_plan.dump_path("after", scenario).write_bytes(b"dump")
        saved_plan.mark_done("after", scenario)
    complaint = "The shot never shows how the buttons work."
    saved_plan.write_grades(
        "after", {"mug__plan": {"grade": "fail", "note": complaint, "at": "later"}}
    )
    if unresolved_first:
        grades = saved_plan.read_grades("after")
        grades["bottle__plan"] = {"grade": "fail", "note": "", "at": "earlier"}
        saved_plan.write_grades("after", grades)
    fake_model.respond(
        "file_regression_complaint",
        {
            "questions": [],
            "new_question": {
                "id": "plan_controls_visible",
                "question": "Can the viewer see how its controls work?",
            },
        },
    )

    status = 1 if unresolved_first else 0
    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == status

    coverage = {case.source["ad"]: case for case in load(cases / "plan_controls_visible.jsonl")}
    assert coverage["mug"].label == "fail"
    assert coverage["mug"].reason == complaint
    if unresolved_first:
        assert set(coverage) == {"mug"}
        assert "bottle__plan" not in read_json(saved_plan.run_dir("after") / "filed-grades.json")
        assert promote(saved_plan, "after", cases_dir=cases) == 1
        assert saved_plan.read_baselines() == {}
    else:
        assert set(coverage) == {"bottle", "mug"}
        assert coverage["bottle"].label == "pass"
        assert coverage["bottle"].kind == "regression"
        assert promote(saved_plan, "after", cases_dir=cases) == 0
        assert saved_plan.read_baselines() == {"bottle": "after", "mug": "after"}
    filed = {path.name: path.read_bytes() for path in cases.glob("*.jsonl")}
    journal = (saved_plan.run_dir("after") / "filed-grades.json").read_bytes()
    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == status
    assert {path.name: path.read_bytes() for path in cases.glob("*.jsonl")} == filed
    assert (saved_plan.run_dir("after") / "filed-grades.json").read_bytes() == journal
    assert len(fake_model.asked) == 1


@pytest.mark.parametrize("existing_fail", [False, True])
def test_a_question_added_between_filings_fills_only_missing_cases_and_keeps_existing_labels(
    saved_plan: Store, tmp_path: Path, fake_model: FakeModel, existing_fail: bool
) -> None:
    cases = tmp_path / "pipeline-cases"
    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    previous = {path.name: path.read_bytes() for path in cases.glob("*.jsonl")}
    question = "plan_controls_visible"
    external = make_case(
        run="after" if existing_fail else "another-run",
        scenario="bottle" if existing_fail else "another-product",
        question=question,
        item="plan",
        label="fail" if existing_fail else "pass",
        reason="Keep these exact corrected words." if existing_fail else "",
        handoff={
            "stage": "plan",
            "item": "plan",
            "question": "Can the viewer see how its controls work?",
        },
        files=["project-files:external/plan.json"],
        graded_at="external-time",
        source="external-grades.json",
    )
    path = cases / f"{question}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(external) + "\n"
    path.write_text(original)

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0

    for name, data in previous.items():
        assert (cases / name).read_bytes() == data
    assert path.read_text().startswith(original)
    added = next(case for case in load(path) if case.source["round"] == "after")
    planned = read_json(saved_plan.run_dir("after") / "filed-grades.json")["bottle__plan"]["cases"][
        question
    ]
    if existing_fail:
        assert added.label == planned["label"] == "fail"
        assert added.reason == planned["reason"] == "Keep these exact corrected words."
        assert added.inputs["files"] == ["project-files:external/plan.json"]
    else:
        assert added.label == planned["label"] == "pass"
        assert added.inputs["handoff"]["situation"] == "The cap should stay on the bottle."
        assert added.inputs["files"] == [
            "project-files:"
            + str(saved_plan.export_dir("after", "bottle") / "bottle" / "regression.json")
        ]
        assert added.source["graded_at"] == "now"
    assert not fake_model.asked
    filed = {path.name: path.read_bytes() for path in cases.glob("*.jsonl")}
    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    assert {path.name: path.read_bytes() for path in cases.glob("*.jsonl")} == filed


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


def test_a_passed_plan_passes_each_of_his_questions_at_its_stage(
    saved_plan: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = tmp_path / "cases"
    his_questions(cases, "plan_claims_backed", "plan_real_use", "viewer_knows_product")
    his_questions(cases, "clip_possible")

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0

    assert sorted({case.inputs["handoff"]["question"] for case in all_filed(cases)}) == sorted(
        HIS_QUESTIONS[q][1] for q in ("plan_claims_backed", "plan_real_use", "viewer_knows_product")
    )
    backed = filed(cases / "plan_claims_backed.jsonl")[0]
    assert backed.id == "reg-after-bottle-plan_claims_backed-plan"
    assert backed.label == "pass"
    assert backed.kind == "regression"
    assert backed.inputs["handoff"] == {
        "question": HIS_QUESTIONS["plan_claims_backed"][1],
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


def test_no_claude_written_question_is_his_so_a_pass_files_nothing_until_he_names_one(
    saved_plan: Store, tmp_path: Path, fake_model: FakeModel, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = tmp_path / "cases"

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0

    assert not list(cases.glob("*.jsonl"))
    assert not fake_model.asked
    assert "0 cases added" in capsys.readouterr().out


def test_a_journal_from_the_old_catalogue_replays_only_his_questions_and_still_gets_new_ones(
    saved_plan: Store, tmp_path: Path, fake_model: FakeModel
) -> None:
    # A journal filed when Claude-written questions were in the catalogue, before items
    # saved their filing inputs.
    old_cases = tmp_path / "old-cases"
    his_questions(old_cases, "plan_claims_backed")
    assert filing.main(["after", "--cases-dir", str(old_cases)], store=saved_plan) == 0
    journal_path = saved_plan.run_dir("after") / "filed-grades.json"
    journal = read_json(journal_path)
    del journal["bottle__plan"]["base"]
    write_json(journal_path, journal)
    cases = tmp_path / "cases"

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    assert not (cases / "plan_claims_backed.jsonl").exists()

    saved_plan.write_run("later", {"name": "later", "scenarios": ["bottle"]})
    write_json(
        saved_plan.export_dir("later", "bottle") / "bottle" / "regression.json",
        read_json(saved_plan.export_dir("after", "bottle") / "bottle" / "regression.json"),
    )
    saved_plan.write_grades(
        "later", {"bottle__plan": {"grade": "fail", "note": "She uses it upside down.", "at": "t"}}
    )
    fake_model.respond(
        "file_regression_complaint",
        {
            "questions": [],
            "new_question": {"id": "plan_right_way_up", "question": "Is it used the right way?"},
        },
    )
    assert filing.main(["later", "--cases-dir", str(cases)], store=saved_plan) == 0

    assert {(c.source["round"], c.label) for c in load(cases / "plan_right_way_up.jsonl")} == {
        ("after", "pass"),
        ("later", "fail"),
    }
    assert not (cases / "plan_claims_backed.jsonl").exists()


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
    cases = tmp_path / "cases"
    his_questions(
        cases, "clip_real_product", "clip_possible", "clip_substance_real", "clip_camera_natural"
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

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0

    impossible = filed(cases / "clip_possible.jsonl")[0]
    assert impossible.id == "reg-after-bottle-clip_possible-s1"
    assert impossible.label == "fail"
    assert impossible.reason == "the cap floats above the bottle"
    assert impossible.kind == "capability"
    assert impossible.source["graded_at"] == "clip-time"
    assert impossible.inputs["handoff"]["line"] == "Take your drink with you."
    assert impossible.inputs["handoff"]["shows"] == "She holds it."
    assert filed(cases / "clip_real_product.jsonl")[0].label == "pass"
    assert filed(cases / "clip_substance_real.jsonl")[0].label == "pass"
    new = filed(cases / "ad_music_fits_mood.jsonl")[0]
    assert new.inputs["handoff"]["question"] == "Does the music fit the mood of the ad?"
    assert new.inputs["handoff"]["stage"] == "ad"
    assert new.reason == "the music feels too frantic for this quiet drink"
    assert new.inputs["files"] == ["project-files:" + str(base / "ad.mp4")]
    # Four clip questions and the new ad one; the passed plan has none of his questions yet.
    assert len(all_filed(cases)) == 5
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
    his_questions(cases)

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0

    by_question = {path.stem: filed(path) for path in cases.glob("*.jsonl")}
    assert all(len(entries) == 1 for entries in by_question.values())
    assert set(by_question) == set(HIS_QUESTIONS)
    assert all(case.label == "pass" for entries in by_question.values() for case in entries)
    assert by_question["page_facts_this_product"][0].inputs["files"] == [
        "project-files:" + str(base / "page.txt"),
        "project-files:" + str(base / "page-photo.png"),
    ]
    assert by_question["picture_real_product"][0].inputs["handoff"]["item"] == "s1"
    note = by_question["note_done"][0]
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
    his_questions(cases, "plan_real_use")
    saved_plan.write_grades("after", {"bottle__plan": {"grade": "fail", "note": "", "at": "now"}})

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 1
    assert "bottle__plan needs words" in capsys.readouterr().err
    assert not all_filed(cases)
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
    assert filed(cases / "plan_real_use.jsonl")[0].reason == "That isn't how it opens."


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
    his_questions(cases, "plan_real_use")

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    assert filed(cases / "plan_real_use.jsonl")[0].label == "fail"


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
    his_questions(cases, "plan_real_use", "plan_claims_backed")
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
    destination = filed(cases / "plan_claims_backed.jsonl")
    assert len(destination) == 1
    moved = destination[0]
    assert moved.id == "reg-after-bottle-plan_claims_backed-plan"
    assert moved.half == half_for(moved.id)
    assert moved.label == "fail"
    assert moved.reason == "It claims too much."
    assert moved.inputs["handoff"]["question"] == HIS_QUESTIONS["plan_claims_backed"][1]
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
    his_questions(cases, "plan_claims_backed")
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
    assert filed(cases / "plan_claims_backed.jsonl")[0].label == "fail"
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
    his_questions(cases, "clip_real_product", "presenter_real_person", "plan_claims_backed")

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    assert filed(cases / "clip_real_product.jsonl")[0].inputs["files"] == [
        "project-files:" + str(scene / "remake-2.mp4")
    ]
    assert filed(cases / "presenter_real_person.jsonl")[0].inputs["files"] == [
        "project-files:" + str(base / "ad-latest.mp4")
    ]
    assert filed(cases / "plan_claims_backed.jsonl")[0].inputs["files"] == [
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
    his_questions(cases, "plan_claims_backed", "plan_real_use", "viewer_knows_product")

    assert filing.main(["after", "--cases-dir", str(cases)], store=saved_plan) == 0
    entries = all_filed(cases)
    assert len(entries) == 3
    assert all(case.label == "pass" and case.reason == "This plan is clear." for case in entries)


@pytest.fixture
def legacy_owner_note(saved_plan: Store) -> Store:
    base = saved_plan.export_dir("after", "bottle") / "bottle"
    (base / "regression.json").unlink()
    scene = base / "scene-1"
    scene.mkdir()
    (scene / "inputs.txt").write_text(
        "SCENE: 1\nLINE: Bring it along.\nSHOWS: She holds it.\nKIND: broll\n"
    )
    (scene / "first.mp4").write_bytes(b"kept clip")
    (scene / "start-picture.png").write_bytes(b"starting picture")
    (base / "ad.mp4").write_bytes(b"before the note")
    (base / "ad-latest.mp4").write_bytes(b"after the note")
    write_json(
        base / "ad.json", {"product_url": "https://shop.example/bottle", "finished_versions": 2}
    )
    write_json(
        base.parent / "results.json",
        {
            "bottle:1": {
                "ad_folder": "bottle",
                "scene": 1,
                "line": "Bring it along.",
                "shows": "She holds it.",
                "kept": {"remake": None},
            }
        },
    )
    write_json(
        base / "notes.json",
        [
            {
                "scene": 1,
                "note": "Make that opening calmer.",
                "fix": "Slow the opening movement.",
                "look": "The opening is rushed.",
                "cost_usd": 1.2,
            }
        ],
    )
    saved_plan.write_grades("after", {})
    return saved_plan


def test_a_legacy_owner_note_uses_its_original_and_latest_ads_without_needing_a_tap(
    legacy_owner_note: Store, tmp_path: Path
) -> None:
    cases = tmp_path / "cases"
    his_questions(cases, "note_done")

    assert filing.main(["after", "--cases-dir", str(cases)], store=legacy_owner_note) == 0

    note = filed(cases / "note_done.jsonl")[0]
    base = legacy_owner_note.export_dir("after", "bottle") / "bottle"
    assert note.label == "pass"
    assert note.inputs["handoff"]["item"] == "note1"
    assert note.inputs["handoff"]["note"] == "Make that opening calmer."
    assert note.inputs["files"] == [
        "project-files:" + str(base / "ad.mp4"),
        "project-files:" + str(base / "ad-latest.mp4"),
    ]


def test_legacy_notes_reuse_the_saved_intermediate_ad_as_the_next_notes_before(
    legacy_owner_note: Store, tmp_path: Path
) -> None:
    base = legacy_owner_note.export_dir("after", "bottle") / "bottle"
    (base / "after-first-note.mp4").write_bytes(b"after note one, before note two")
    write_json(
        base / "notes.json",
        [
            {"scene": 1, "note": "Make that opening calmer.", "after": "after-first-note.mp4"},
            {"scene": 2, "note": "Show the lid at the end."},
        ],
    )
    cases = tmp_path / "cases"
    his_questions(cases, "note_done")

    assert filing.main(["after", "--cases-dir", str(cases)], store=legacy_owner_note) == 0

    notes = filed(cases / "note_done.jsonl")
    assert [note.inputs["handoff"]["item"] for note in notes] == ["note1", "note2"]
    assert notes[0].inputs["files"] == [
        "project-files:" + str(base / "ad.mp4"),
        "project-files:" + str(base / "after-first-note.mp4"),
    ]
    assert notes[1].inputs["files"] == [
        "project-files:" + str(base / "after-first-note.mp4"),
        "project-files:" + str(base / "ad-latest.mp4"),
    ]


def test_legacy_notes_name_every_missing_intermediate_version_instead_of_guessing(
    legacy_owner_note: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = legacy_owner_note.export_dir("after", "bottle") / "bottle"
    write_json(
        base / "notes.json",
        [
            {"scene": 1, "note": "Make that opening calmer."},
            {"scene": 2, "note": "Show the lid at the end."},
            {"scene": 1, "note": "Make that first line shorter."},
        ],
    )
    cases = tmp_path / "cases"
    his_questions(cases, "note_done")

    assert filing.main(["after", "--cases-dir", str(cases)], store=legacy_owner_note) == 1
    error = capsys.readouterr().err
    assert "note1 after, note2 before, note2 after, note3 before" in error
    assert "add their before/after paths to notes.json" in error
    assert not filed(cases / "note_done.jsonl")
    assert not read_json(legacy_owner_note.run_dir("after") / "filed-grades.json")


def test_a_named_legacy_note_media_file_must_exist_even_without_a_tap(
    legacy_owner_note: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = legacy_owner_note.export_dir("after", "bottle") / "bottle"
    write_json(
        base / "notes.json",
        [{"scene": 1, "note": "Make that opening calmer.", "after": "missing-after.mp4"}],
    )
    cases = tmp_path / "cases"
    his_questions(cases, "note_done")

    assert filing.main(["after", "--cases-dir", str(cases)], store=legacy_owner_note) == 1
    error = capsys.readouterr().err
    assert "Saved graded output is missing" in error
    assert "missing-after.mp4" in error
    assert not filed(cases / "note_done.jsonl")
