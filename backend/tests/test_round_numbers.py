"""How a graded round's numbers are counted from Julian's taps and the run's files. Plain
checks kept apart from tests/test_round_results.py, whose exports need a database."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from evals import results

from .test_producer_auto_fix import FLOATS, SPINS, remade_once, remade_twice  # noqa: F401
from .test_producer_broll import checked, instructed  # noqa: F401 (fixtures)
from .test_round_results import GRADES, JUDGE, ROUND_4_LIKE, TAPS, _run, _try


def test_the_judge_is_scored_against_his_grades_on_first_clips(tmp_path: Path) -> None:
    report = results.report(_run(tmp_path, ROUND_4_LIKE, grades=GRADES, judge=JUDGE, taps=TAPS))

    assert (
        "- Clips he failed: 2. The judge caught 1 (8-cleaner s4), missed 1 (8-cleaner s2), "
        "couldn't check 0." in report
    )
    assert (
        "- Clips he passed: 2. The judge wrongly failed 1 (8-cleaner s3), agreed on 0, "
        "couldn't check 1 (9-chili s2)." in report
    )
    assert "Judge wording: w1." in report


def test_round_4_counts_its_taps_on_the_kept_remake_and_no_kept_grades(tmp_path: Path) -> None:
    report = results.report(_run(tmp_path, ROUND_4_LIKE, grades=GRADES, judge=JUDGE, taps=TAPS))

    assert "These ads hold the first clips (made without the auto-redo)." in report
    assert "- Ads graded: 2, good 1, bad 1." in report
    assert "- Ads where he passed every B-roll first clip: 1 of 2." in report
    # Kept remakes have no pass/fail grade in round 4, but 8's s2 failed and wasn't remade.
    assert "- Ads where he passed every B-roll final clip (after the auto-redo): 1 of 2." in report
    assert "the auto-redo made 3 remakes and kept a remake in 2 scenes" in report
    assert "- Kept remakes graded pass or fail: none in this run." in report
    # The tap on the remake the app kept: remake 2 of scene 4.
    assert "(his taps): yes 1, partly 1, no 0; not tapped 0." in report
    # s3: "not fixed" though he said fixed; s4: partly, and he agreed with its "fixed".
    assert "by his taps: right 1, wrong 1 (8-cleaner s3), not settled 0." in report
    assert (
        "- Clips he passed that the auto-redo swapped for a remake: 1 (8-cleaner s3); of those, "
        "kept remakes he failed: 0." in report
    )
    assert (
        "### First step that broke (his tap per failed clip)\n- Not recorded in this run." in report
    )
    assert "- No cost record in this run." in report
    assert "- Judge and remakes, all 4 B-roll scenes: $4.00." in report
    assert "fix-1" not in report


def test_his_cause_taps_are_counted_and_a_failed_clip_without_one_is_said(tmp_path: Path) -> None:
    grades = {
        "a__whole-ad": {"grade": "fail"},
        "a__scene-2": {"grade": "fail", "cause": "planner"},
        "a__scene-3": {"grade": "fail", "cause": "planner"},
        "a__scene-3__kept": {"grade": "fail", "cause": "motion_prompt"},
        "a__scene-4": {"grade": "fail", "cause": None},
    }
    judge = {
        "a:2": {"tries": [_try("fail")], "kept": {"remake": None}},
        "a:3": {"tries": [_try("fail"), _try("fail", 1)], "kept": {"remake": 1}},
        "a:4": {"tries": [_try("pass")], "kept": {"remake": None}},
    }
    meta = {"name": "R5", "ad_grade_is": "kept", "grades": "grades.json", "judge": "judge.json"}
    report = results.report(_run(tmp_path, meta, grades=grades, judge=judge))

    assert "- the plan asked for the wrong or an impossible thing: 2" in report
    assert "- the motion prompt: 1" in report
    assert "- the video model did the order badly: 0" in report
    assert "- Failed clips with no tap: 1" in report
    assert (
        "### Judge against his grades, kept remakes\n"
        "- Clips he failed: 1. The judge caught 1 (a s3)" in report
    )


def test_costs_are_listed_per_ad_with_their_source(tmp_path: Path) -> None:
    meta = {"name": "R", "grades": "grades.json", "costs": "costs.json"}
    costs = {"source": "the app", "ads": {"a": {"total_usd": 2.5}, "b": {"total_usd": 1.25}}}
    grades = {"a__whole-ad": {"grade": "pass"}, "b__whole-ad": {"grade": "fail"}}
    report = results.report(_run(tmp_path, meta, grades=grades, costs=costs))

    assert (
        "- a: $2.50\n- b: $1.25\n- All 2 ads (sum of the figures above): $3.75. Source: the app."
        in report
    )
    assert "- No judge verdicts in this run." in report


def test_the_command_prints_the_numbers_of_each_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(tmp_path, ROUND_4_LIKE, grades=GRADES, judge=JUDGE, taps=TAPS)
    results.main([str(tmp_path)])
    assert capsys.readouterr().out.startswith("## R: numbers")


def test_a_regrade_turns_a_pass_he_took_back_into_a_fail_before_counting(tmp_path: Path) -> None:
    meta = {**ROUND_4_LIKE, "regrades": "regrades.json"}
    regrades = {"8-cleaner__scene-3": {"now": "fail", "julian": "the judge was right"}}
    run = _run(tmp_path, meta, grades=GRADES, judge=JUDGE, taps=TAPS, regrades=regrades)

    report = results.report(run)

    assert "The judge caught 2 (8-cleaner s3, 8-cleaner s4)" in report
    assert "- Clips he passed: 1. The judge wrongly failed 0" in report


def test_an_ad_he_passed_whole_is_bad_when_a_clip_it_holds_failed(tmp_path: Path) -> None:
    grades = {
        "a__whole-ad": {"grade": "pass"},
        "a__scene-2": {"grade": "fail"},
        "a__scene-2__kept": {"grade": "pass"},
        "b__whole-ad": {"grade": "pass"},
        "b__scene-2": {"grade": "fail"},
        "b__scene-2__kept": {"grade": "fail"},
        "c__whole-ad": {"grade": "pass"},
        "c__scene-2": {"grade": "pass"},
        "c__scene-3": {"grade": "pass"},
    }
    judge = {
        f"{ad}:2": {"tries": [_try("fail"), _try("pass", 1)], "kept": {"remake": 1}}
        for ad in ("a", "b")
    } | {"c:2": {"tries": [_try("pass")], "kept": {"remake": None}}}
    meta = {"name": "R5", "ad_grade_is": "kept", "grades": "grades.json", "judge": "judge.json"}
    report = results.report(_run(tmp_path, meta, grades=grades, judge=judge))

    assert "- Ads graded: 3, good 2, bad 1." in report
    assert "as a clip it holds failed: 1 (b)." in report
    assert (
        "- Clips he failed that the auto-redo replaced and he graded again: 2; the kept remake "
        "passed in 1 (a s2)." in report
    )
    # c s3 was graded, but the judge never saw it.
    assert "- Graded but never judged: 1 (c s3)." in report


def test_a_tap_saves_the_words_typed_and_keeps_a_cause_only_on_a_fail(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node, "node runs the page's own save logic"
    logic = (Path(results.__file__).parent / "grading_page.js").read_text()
    script = (
        logic
        + """
const saved = { grade: "fail", cause: "planner", note: "old words", at: "t0" };
console.log(JSON.stringify([
  nextDoc(saved, { grade: "pass" }, "typed, not saved", "t1"),
  nextDoc(saved, { cause: "video_model" }, "old words", "t2"),
  nextDoc(undefined, { grade: "fail" }, "", "t3"),
]));
"""
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout

    assert json.loads(out) == [
        {"grade": "pass", "cause": None, "note": "typed, not saved", "at": "t1"},
        {"grade": "fail", "cause": "video_model", "note": "old words", "at": "t2"},
        {"grade": "fail", "cause": None, "note": "", "at": "t3"},
    ]
    page_script = (Path(results.__file__).parent / "grading_page.html").read_text()
    assert "nextDoc(state[id], patch, typed, new Date().toISOString())" in page_script
