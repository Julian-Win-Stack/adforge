"""The regression eval's public file formats and question catalogue (issue #600)."""

import errno
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from evals.cases import half_for, load
from evals.regression.__main__ import main
from evals.regression.cases import case_id, kind_for, make_case
from evals.regression.questions import all_questions, code_questions, for_stage, julian_questions
from evals.regression.store import Store, write_json


def test_the_command_lists_its_subcommands_without_a_store(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == 0
    help_text = capsys.readouterr().out
    for command in (
        "save-pages",
        "run",
        "answer",
        "grading-page",
        "file-grades",
        "intend",
        "report",
        "promote",
    ):
        assert command in help_text


def test_the_catalogue_has_only_code_questions_until_julian_names_his_own(
    tmp_path: Path,
) -> None:
    expected = {
        "page_photos_this_product": "page",
        "plan_has_demo": "plan",
        "plan_names_brand": "plan",
        "plan_fits_length": "plan",
        "clip_words_match": "clips",
        "ad_finishes": "ad",
        "ad_fits_length": "ad",
        "ad_end_card": "ad",
        "ad_brand_heard": "ad",
        "ad_last_words": "ad",
        "ad_no_dead_air": "ad",
        "note_untouched_same": "note",
    }
    catalogue = all_questions(cases_dir=tmp_path)
    assert {q.id: q.stage for q in catalogue} == expected
    assert all(q.answered_by == "code" for q in catalogue)
    assert julian_questions(cases_dir=tmp_path) == []
    assert [q.id for q in code_questions("clips", cases_dir=tmp_path)] == ["clip_words_match"]
    assert {q.id for q in for_stage("page", cases_dir=tmp_path)} == {"page_photos_this_product"}
    # Earlier evals' case files, Claude-worded and without a regression stage, are not his.
    (tmp_path / "clip_possible.jsonl").write_text(
        json.dumps(
            dict(
                make_case(
                    run="r9",
                    scenario="glow",
                    question="clip_possible",
                    item="s1",
                    label="pass",
                    reason="",
                    handoff={"question": "Does nothing bend?", "scene": 1},
                    files=[],
                    graded_at="",
                    source="round-9",
                )
            )
        )
        + "\n"
    )
    assert julian_questions(cases_dir=tmp_path) == []


def test_regression_case_ids_and_labels_round_trip_through_the_existing_loader(
    tmp_path: Path,
) -> None:
    path = tmp_path / "note_done.jsonl"
    made = [
        make_case(
            run="after-change",
            scenario="ridge-wallet",
            question="note_done",
            item=f"note{n}",
            label=label,
            reason=reason,
            handoff={"question": "Was the note done?", "stage": "note"},
            files=["project-files:runs/after-change/ridge-wallet/ad.mp4"],
            graded_at="2026-11-12T03:00:00Z",
            source="runs/after-change/grades.json",
        )
        for n, (label, reason) in enumerate([("pass", ""), ("fail", "still the same scene")], 1)
    ]
    path.write_text("".join(json.dumps(case) + "\n" for case in made))
    passed, failed = load(path)
    assert passed.id == "reg-after-change-ridge-wallet-note_done-note1"
    assert passed.half == half_for(passed.id)
    assert (passed.label, passed.kind) == ("pass", "regression")
    assert (failed.label, failed.kind, failed.reason) == (
        "fail",
        "capability",
        "still the same scene",
    )
    assert failed.source["round"] == "after-change"
    assert failed.source["ad"] == "ridge-wallet"
    assert failed.verdicts == ()
    assert (
        case_id("run1", "ridge", "clip_real_product", "s2") == "reg-run1-ridge-clip_real_product-s2"
    )
    with pytest.raises(ValueError, match="label"):
        kind_for("unsure")


def test_a_new_question_is_discovered_from_the_first_case_without_a_code_change(
    tmp_path: Path,
) -> None:
    made = make_case(
        run="new-look",
        scenario="ridge-wallet",
        question="hand_stays_natural",
        item="s1",
        label="fail",
        reason="the hand twists",
        handoff={"question": "Does the hand stay natural?", "stage": "clips"},
        files=[],
        graded_at="2026-11-12T03:00:00Z",
        source="runs/new-look/grades.json",
    )
    (tmp_path / "hand_stays_natural.jsonl").write_text(json.dumps(made) + "\n")
    # Existing eval files whose handoffs predate regression stages are not new questions.
    legacy = dict(made, id="legacy")
    legacy["half"] = half_for("legacy")
    legacy["inputs"] = {"handoff": {"scene": 1}, "files": []}
    (tmp_path / "old_question.jsonl").write_text(json.dumps(legacy) + "\n")
    # A static question's case file must never create a duplicate or override its definition.
    (tmp_path / "presenter_real_person.jsonl").write_text(json.dumps(made) + "\n")
    added = [
        q for q in julian_questions("clips", cases_dir=tmp_path) if q.id == "hand_stays_natural"
    ]
    assert len(added) == 1
    assert added[0].words == "Does the hand stay natural?"
    ids = [q.id for q in all_questions(cases_dir=tmp_path)]
    assert "old_question" not in ids
    assert ids.count("presenter_real_person") == 1


def test_store_files_round_trip_and_missing_files_are_empty(tmp_path: Path) -> None:
    store = Store(tmp_path)
    assert store.read_run("round1") == {}
    assert store.read_answers("round1", "ridge") == {}
    assert store.read_grades("round1") == {}
    assert store.read_grades_markdown("round1") == ""
    assert store.read_baselines() == {}
    assert store.read_intended() == []
    assert store.read_photo_labels() == {}

    run = {
        "name": "round1",
        "app_commit": "abc123",
        "to": "plans",
        "from": None,
        "started_at": "2026-11-12T03:00:00Z",
        "finished_at": None,
        "estimate_usd": 25.0,
        "spend_usd": 0.42,
        "scenarios": ["ridge"],
    }
    answers = {
        "scenario": "ridge",
        "run": "round1",
        "answers": {
            "plan_has_demo": {
                "verdict": "pass",
                "reason": "One demo scene",
                "items": [{"item": "plan", "verdict": "pass", "reason": "One demo scene"}],
            }
        },
        "first_try": {"scenes": 3, "first_passed": 0, "clips": 0},
        "cost_usd": 0.42,
    }
    grades = {"ridge__plan": {"grade": "pass", "note": "looks good", "at": "2026-11-12T03:00:00Z"}}
    intentions = [
        {
            "scenario": "ridge",
            "question": "plan_names_brand",
            "why": "New brand",
            "at": "2026-11-12T02:00:00Z",
        }
    ]
    labels = {"ridge": {"a" * 64: "this", "b" * 64: "not"}}
    store.write_run("round1", run)
    store.write_answers("round1", "ridge", answers)
    store.write_grades("round1", grades)
    store.write_grades_markdown("round1", "# Grades\n\nRidge: looks good\n")
    store.write_baselines({"ridge": "round0"})
    store.write_intended(intentions)
    store.write_photo_labels(labels)
    reread = Store(tmp_path)
    assert reread.read_run("round1") == run
    assert reread.read_answers("round1", "ridge") == answers
    assert reread.read_grades("round1") == grades
    assert reread.read_grades_markdown("round1") == "# Grades\n\nRidge: looks good\n"
    assert reread.read_baselines() == {"ridge": "round0"}
    assert reread.read_intended() == intentions
    assert reread.read_photo_labels() == labels
    assert store.page_dir("ridge") == tmp_path / "pages" / "ridge"
    assert store.scenario_dir("round1", "ridge") == tmp_path / "runs" / "round1" / "ridge"
    assert not list(tmp_path.rglob("*.tmp"))


def test_an_interrupted_save_keeps_the_previous_file_and_never_marks_the_scenario_done(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    store.write_answers("round1", "ridge", {"answers": {"ad_finishes": {"verdict": "pass"}}})
    folder = store.scenario_dir("round1", "ridge")
    store.dump_path("round1", "ridge").write_bytes(b"database dump")
    store.media_dir("round1", "ridge").mkdir()
    store.export_dir("round1", "ridge").mkdir()
    assert not store.scenario_done("round1", "ridge")
    with patch("evals.regression.store.os.replace", side_effect=OSError("disk stopped")):
        with pytest.raises(OSError, match="disk stopped"):
            store.write_answers("round1", "ridge", {"answers": {}})
    assert store.read_answers("round1", "ridge") == {
        "answers": {"ad_finishes": {"verdict": "pass"}}
    }
    assert not store.scenario_done("round1", "ridge")
    assert not list(folder.glob("*.tmp"))
    store.dump_path("round1", "ridge").write_bytes(b"dump")
    store.mark_done("round1", "ridge")
    assert Store(tmp_path).scenario_done("round1", "ridge")


def test_a_store_on_a_network_folder_that_cannot_flush_still_saves_every_file(
    tmp_path: Path,
) -> None:
    # Project files are a network folder whose fsync answers "operation not supported".
    store = Store(tmp_path)
    refused = OSError(errno.EOPNOTSUPP, "Operation not supported")
    with patch("evals.regression.store.os.fsync", side_effect=refused):
        store.write_answers("round1", "ridge", {"answers": {"ad_finishes": {"verdict": "pass"}}})
        store.dump_path("round1", "ridge").write_bytes(b"dump")
        store.mark_done("round1", "ridge")
    assert store.read_answers("round1", "ridge") == {
        "answers": {"ad_finishes": {"verdict": "pass"}}
    }
    assert store.scenario_done("round1", "ridge")


def test_filing_a_case_on_a_network_folder_still_appends_it(tmp_path: Path) -> None:
    from evals.regression.filing import append_case

    path = tmp_path / "cases" / "clip_possible.jsonl"
    refused = OSError(errno.EOPNOTSUPP, "Operation not supported")
    cases = [
        make_case(
            run="after-change",
            scenario="ridge-wallet",
            question="clip_possible",
            item=item,
            label="pass",
            reason="",
            handoff={"question": "Nothing bends or floats", "stage": "clips"},
            files=[],
            graded_at="2026-11-12T03:00:00Z",
            source="runs/after-change/grades.json",
        )
        for item in ("s1", "s2")
    ]
    with patch("evals.regression.store.os.fsync", side_effect=refused):
        assert [append_case(path, case) for case in cases] == [1, 1]
    assert [json.loads(line)["id"] for line in path.read_text().splitlines()] == [
        case["id"] for case in cases
    ]


def test_a_done_marker_that_arrived_before_the_answers_or_dump_is_not_finished(
    tmp_path: Path,
) -> None:
    # Project files upload closed files in the background, so the marker can land first.
    store = Store(tmp_path)
    store.mark_done("round1", "ridge")
    assert not store.scenario_done("round1", "ridge")
    store.write_answers("round1", "ridge", {"answers": {}})
    assert not store.scenario_done("round1", "ridge")
    store.dump_path("round1", "ridge").write_bytes(b"")
    assert not store.scenario_done("round1", "ridge")
    store.dump_path("round1", "ridge").write_bytes(b"database dump")
    assert store.scenario_done("round1", "ridge")


def test_a_real_flush_failure_still_stops_the_save(tmp_path: Path) -> None:
    store = Store(tmp_path)
    with patch("evals.regression.store.os.fsync", side_effect=OSError(errno.EIO, "I/O error")):
        with pytest.raises(OSError, match="I/O error"):
            store.write_answers("round1", "ridge", {"answers": {}})
    assert store.read_answers("round1", "ridge") == {}


def test_store_location_comes_from_the_argument_or_environment_and_bad_files_are_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVAL_STORE", str(tmp_path / "environment"))
    assert Store().root == tmp_path / "environment"
    assert Store(tmp_path / "explicit").root == tmp_path / "explicit"
    monkeypatch.delenv("EVAL_STORE")
    with pytest.raises(ValueError, match="EVAL_STORE"):
        Store()
    path = tmp_path / "baseline.json"
    path.write_text("{truncated")
    with pytest.raises(json.JSONDecodeError):
        Store(tmp_path).read_baselines()
    # Serialization errors cannot disturb a previously complete file either.
    with pytest.raises(TypeError):
        write_json(path, {"not_json": object()})
    assert path.read_text() == "{truncated"


@pytest.mark.parametrize("key", ["..", "/tmp/escaped", "nested/name", ""])
def test_store_keys_cannot_escape_the_store(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError, match="store key"):
        Store(tmp_path).scenario_dir("run1", key)
