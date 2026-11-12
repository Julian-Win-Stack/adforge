"""A graded regression run says what broke and only passing runs become baselines."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from evals.regression.__main__ import main
from evals.regression.cases import make_case
from evals.regression.reports import compare_items, promote, report
from evals.regression.store import Store


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (["pass", "pass"], ["pass", "fail", "pass"], "broke"),
        (["pass", "fail", "fail"], ["pass"], "got fixed"),
        (["pass"], ["pass", "pass"], "unchanged"),
        (["fail"], ["pass", "fail"], "unchanged"),
        (["pass"], ["needs_label"], "unresolved"),
        (["not_reached"], ["fail"], "unresolved"),
        ([], ["fail"], "unresolved"),
        (["fail"], [], "unresolved"),
    ],
)
def test_questions_compare_every_item_without_requiring_matching_scene_counts(
    old: list[str], new: list[str], expected: str
) -> None:
    assert compare_items(old, new) == expected


def answers(
    store: Store,
    run: str,
    scenario: str,
    verdicts: dict[str, str],
    *,
    cost: float = 2.0,
    first_passed: int = 1,
) -> None:
    store.write_answers(
        run,
        scenario,
        {
            "run": run,
            "scenario": scenario,
            "answers": {
                question: {"verdict": verdict, "reason": "", "items": []}
                for question, verdict in verdicts.items()
            },
            "first_try": {"scenes": 2, "first_passed": first_passed, "clips": 3},
            "cost_usd": cost,
        },
    )
    store.mark_done(run, scenario)


def case(
    run: str,
    scenario: str,
    question: str,
    item: str,
    label: str,
    reason: str = "",
    *,
    stage: str = "pictures",
) -> dict[str, Any]:
    return make_case(
        run=run,
        scenario=scenario,
        question=question,
        item=item,
        label=label,
        reason=reason,
        handoff={"stage": stage, "item": item, "question": "Is the picture possible?"},
        files=[],
        graded_at="2026-11-12T04:00:00Z",
        source=f"runs/{run}/grades.json",
    )


@pytest.fixture
def comparison(tmp_path: Path) -> tuple[Store, Path]:
    store = Store(tmp_path / "store")
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    keys = ["kettle", "chair", "lamp"]
    for run, started_at in (
        ("old", "2026-11-10T01:00:00Z"),
        ("new", "2026-11-12T01:00:00Z"),
    ):
        store.write_run(run, {"name": run, "started_at": started_at, "scenarios": keys})
        store.write_grades(run, {})
    store.write_baselines({"kettle": "old", "chair": "old"})
    answers(store, "old", "kettle", {"ad_end_card": "pass", "clip_words_match": "fail"})
    answers(store, "new", "kettle", {"ad_end_card": "fail", "clip_words_match": "pass"}, cost=3)
    answers(store, "old", "chair", {"plan_has_demo": "pass"}, first_passed=2)
    answers(store, "new", "chair", {"plan_has_demo": "pass"}, cost=4)
    answers(store, "new", "lamp", {"plan_has_demo": "fail"}, cost=1)
    store.write_intended(
        [
            {
                "scenario": "kettle",
                "question": "ad_end_card",
                "why": "This run deliberately measures a different ending.",
                "at": "2026-11-11T01:00:00Z",
            }
        ]
    )
    picture_cases = [
        case("old", "kettle", "picture_possible", "s1", "pass"),
        case("old", "kettle", "picture_possible", "s2", "pass"),
        case("new", "kettle", "picture_possible", "s1", "fail", "The cap floats."),
    ]
    (cases_dir / "picture_possible.jsonl").write_text(
        "".join(json.dumps(value) + "\n" for value in picture_cases)
    )
    store.write_grades(
        "new",
        {"kettle__s1__picture": {"grade": "fail", "note": "The cap floats.", "at": ""}},
    )
    return store, cases_dir


def test_a_report_compares_human_and_code_answers_and_keeps_intended_changes_apart(
    comparison: tuple[Store, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, cases_dir = comparison
    result = report(store, "new", cases_dir=cases_dir)
    printed = capsys.readouterr().out
    assert printed == (store.run_dir("new") / "report.md").read_text()
    assert "broke: picture_possible" in printed
    assert "old: pass" in printed and "new: fail" in printed
    assert "The cap floats." in printed
    assert "got fixed: clip_words_match" in printed
    assert "intended: ad_end_card" in printed
    assert "first run, becomes the baseline when promoted" in printed
    assert "old: 1/2 (50.0%)" in printed
    assert "clips per scene old: 1.50" in printed
    assert "cost old: $2.00; new: $3.00" in printed
    assert printed.endswith("SHIP RULE FAILED: kettle: picture_possible\n")
    assert not result.passed
    assert store.read_intended()[0]["used_by"] == "new"

    assert report(store, "new", cases_dir=cases_dir).text == result.text
    capsys.readouterr()
    intentions = store.read_intended()
    intentions.append(
        {
            "scenario": "kettle",
            "question": "picture_possible",
            "why": "The cap removal was the declared change.",
            "at": "2026-11-11T02:00:00Z",
        }
    )
    store.write_intended(intentions)
    assert report(store, "new", cases_dir=cases_dir).passed
    assert capsys.readouterr().out.endswith("SHIP RULE PASSED\n")


def test_promotion_protects_existing_baselines_but_keeps_a_first_graded_run(
    comparison: tuple[Store, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, cases_dir = comparison
    assert promote(store, "new", cases_dir=cases_dir) == 1
    assert store.read_baselines() == {"kettle": "old", "chair": "old", "lamp": "new"}
    output = capsys.readouterr().out
    assert "Promoted first baseline: lamp" in output
    assert "Refused promotion: kettle: picture_possible" in output

    intentions = store.read_intended()
    intentions.append(
        {
            "scenario": "kettle",
            "question": "picture_possible",
            "why": "Deliberate cap removal.",
            "at": "2026-11-11T02:00:00Z",
        }
    )
    store.write_intended(intentions)
    assert promote(store, "new", cases_dir=cases_dir) == 0
    assert store.read_baselines() == {"kettle": "new", "chair": "new", "lamp": "new"}


@pytest.mark.parametrize("missing", ["grades", "done", "answers"])
def test_an_ungraded_or_incomplete_first_run_cannot_be_promoted(
    tmp_path: Path, missing: str, capsys: pytest.CaptureFixture[str]
) -> None:
    store = Store(tmp_path)
    store.write_run(
        "first", {"name": "first", "started_at": "2026-11-12T01:00:00Z", "scenarios": ["lamp"]}
    )
    answers(store, "first", "lamp", {"plan_has_demo": "fail"})
    store.write_grades("first", {})
    path = {
        "grades": store.run_dir("first") / "grades.json",
        "done": store.scenario_dir("first", "lamp") / "done",
        "answers": store.scenario_dir("first", "lamp") / "answers.json",
    }[missing]
    path.unlink()
    assert promote(store, "first", cases_dir=tmp_path / "cases") == 1
    assert store.read_baselines() == {}
    assert "Refused promotion" in capsys.readouterr().out


def test_the_commands_record_an_intention_for_one_future_run_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = Store(tmp_path)
    assert (
        main(
            [
                "intend",
                "chair",
                "plan_has_demo",
                "Testing a talking-only plan.",
                "--store",
                str(tmp_path),
            ]
        )
        == 0
    )
    intention = store.read_intended()[0]
    assert intention["why"] == "Testing a talking-only plan."
    assert "used_by" not in intention
    future = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    for name in ("old", "new", "later"):
        store.write_run(name, {"name": name, "started_at": future, "scenarios": ["chair"]})
        store.write_grades(name, {})
        answers(store, name, "chair", {"plan_has_demo": "pass" if name == "old" else "fail"})
    store.write_baselines({"chair": "old"})
    assert main(["report", "new", "--store", str(tmp_path)]) == 0
    assert store.read_intended()[0]["used_by"] == "new"
    assert main(["report", "new", "--store", str(tmp_path)]) == 0
    assert main(["report", "later", "--store", str(tmp_path)]) == 1
    assert "SHIP RULE FAILED: chair: plan_has_demo" in capsys.readouterr().out
    assert main(["promote", "new", "--store", str(tmp_path)]) == 0


def test_a_failed_tap_without_a_filed_case_cannot_be_lost_even_on_a_first_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = Store(tmp_path / "store")
    store.write_run("first", {"started_at": "2026-11-12T01:00:00Z", "scenarios": ["lamp"]})
    answers(store, "first", "lamp", {"ad_finishes": "pass"})
    store.write_grades("first", {"lamp__s1__clip": {"grade": "fail", "note": "", "at": ""}})
    cases_dir = tmp_path / "cases"
    assert not report(store, "first", cases_dir=cases_dir).passed
    assert "unfiled grade s1 clip" in capsys.readouterr().out
    assert promote(store, "first", cases_dir=cases_dir) == 1
    assert store.read_baselines() == {}

    cases_dir.mkdir()
    (cases_dir / "clip_possible.jsonl").write_text(
        json.dumps(
            case("first", "lamp", "clip_possible", "s1", "fail", "The cap floats.", stage="clips")
        )
        + "\n"
    )
    store.write_grades(
        "first", {"lamp__s1__clip": {"grade": "fail", "note": "The cap floats.", "at": ""}}
    )
    assert promote(store, "first", cases_dir=cases_dir) == 0
    assert store.read_baselines() == {"lamp": "first"}


def test_an_intention_declared_after_the_run_cannot_hide_a_regression(
    comparison: tuple[Store, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, cases_dir = comparison
    intentions = store.read_intended()
    intentions.append(
        {
            "scenario": "kettle",
            "question": "picture_possible",
            "why": "Said only after seeing the failure.",
            "at": "2026-11-12T02:00:00Z",
        }
    )
    store.write_intended(intentions)
    assert not report(store, "new", cases_dir=cases_dir).passed
    assert "used_by" not in store.read_intended()[1]
    assert "SHIP RULE FAILED: kettle: picture_possible" in capsys.readouterr().out


def test_unreached_and_unlabelled_questions_are_listed_apart_from_regressions(
    comparison: tuple[Store, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, cases_dir = comparison
    answers(store, "old", "chair", {"page_photos_this_product": "pass", "ad_finishes": "pass"})
    answers(
        store,
        "new",
        "chair",
        {"page_photos_this_product": "needs_label", "ad_finishes": "not_reached"},
    )
    report(store, "new", cases_dir=cases_dir)
    output = capsys.readouterr().out
    assert "unresolved: page_photos_this_product" in output
    assert "unresolved: ad_finishes" in output
    assert "broke: page_photos_this_product" not in output
    assert output.endswith("SHIP RULE FAILED: kettle: picture_possible\n")


def test_a_no_worse_failure_blocks_shipping_even_when_questions_did_not_break(
    comparison: tuple[Store, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, cases_dir = comparison
    grades = store.read_grades("new")
    grades["chair__no-worse"] = {
        "grade": "fail",
        "note": "The old picture felt more natural.",
        "at": "",
    }
    store.write_grades("new", grades)
    result = report(store, "new", cases_dir=cases_dir)
    assert not result.passed
    output = capsys.readouterr().out
    assert "New is no worse: fail (The old picture felt more natural.)" in output
    assert output.endswith("SHIP RULE FAILED: kettle: picture_possible, chair: no worse\n")
