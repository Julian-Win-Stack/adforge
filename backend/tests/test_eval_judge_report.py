"""Judge flaw counts compare saved runs without turning every flawed ad into a failure."""

import json
from pathlib import Path
from typing import Any

import pytest

from evals.regression.cases import make_case
from evals.regression.reports import report
from evals.regression.store import Store


def answer(verdict: str, *, cost: float = 0.01, error: str = "") -> dict[str, Any]:
    value: dict[str, Any] = {
        "judge": "C6:test",
        "asks": [
            {
                "verdict": verdict if verdict != "unjudged" else "fail",
                "sees": "The strap crosses the target" if verdict == "fail" else "Clear",
                "when": "2 s",
                "on_what": "product",
                "model": "fake",
                "at": "2026-11-16T00:00:00Z",
                "input_tokens": 100,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": 10,
                "cost_usd": cost,
            }
        ],
    }
    if error:
        value["error"] = error
    else:
        value["verdict"] = verdict
    return value


@pytest.fixture
def saved_runs(tmp_path: Path) -> tuple[Store, Path]:
    store = Store(tmp_path / "store")
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    for run in ("baseline", "new", "discarded"):
        store.write_run(
            run,
            {
                "name": run,
                "started_at": "2026-11-16T00:00:00Z",
                "scenarios": ["strap", "lamp"],
            },
        )
        store.write_grades(run, {})
        for scenario in ("strap", "lamp"):
            store.write_answers(run, scenario, {"answers": {}, "cost_usd": 2.0})
    store.write_baselines({"strap": "baseline", "lamp": "baseline"})
    return store, cases_dir


def test_report_counts_flaws_per_question_and_names_fixed_new_and_remaining_scenes(
    saved_runs: tuple[Store, Path],
) -> None:
    store, cases_dir = saved_runs
    store.write_judge_answers(
        "baseline",
        {
            "strap": {
                "C6": {"s1": answer("fail"), "s2": answer("pass"), "s3": answer("fail")},
                "P1": {"s1": answer("pass")},
                "A11": {"ad": answer("pass")},
            },
            "lamp": {"C6": {"s1": answer("pass")}},
        },
    )
    store.write_judge_answers(
        "new",
        {
            "strap": {
                "C6": {"s1": answer("pass"), "s2": answer("fail"), "s3": answer("fail")},
                "P1": {"s1": answer("fail")},
                "A11": {"ad": answer("pass")},
            },
            "lamp": {"C6": {"s1": answer("pass")}},
        },
    )
    # The immediately preceding run was discarded and must never be the yardstick.
    store.write_judge_answers("discarded", {"strap": {"C6": {"s1": answer("pass")}}})
    result = report(store, "new", cases_dir=cases_dir)
    assert "C6 Nothing impossible: 2 of 4 clips, then 2 of 4" in result.text
    assert "P1 Real product: 0 of 1 pictures, then 1 of 1" in result.text
    assert "A11 Same product in every scene: 0 of 1 ads, then 0 of 1" in result.text
    assert "gone: strap s1 (judge)" in result.text
    assert "new: strap s2 (judge)" in result.text
    assert "still: strap s3 (judge)" in result.text
    assert "not trusted yet" in result.text
    assert "Judge cost this run: $0.06" in result.text
    assert result.passed
    assert (store.run_dir("new") / "report.md").read_text() == result.text


@pytest.mark.parametrize(
    ("tap", "flaws", "ships"),
    [("not_a_flaw", 0, True), ("real_flaw_ship", 1, True), ("real_flaw_dont_ship", 1, False)],
)
def test_julians_tap_overrides_the_judge_and_only_dont_ship_fails_the_ship_rule(
    saved_runs: tuple[Store, Path], tap: str, flaws: int, ships: bool
) -> None:
    store, cases_dir = saved_runs
    store.write_judge_answers("baseline", {"strap": {"P4": {"s1": answer("pass")}}})
    store.write_judge_answers("new", {"strap": {"P4": {"s1": answer("fail")}}})
    store.write_judge_taps(
        "new", {"strap__s1__picture__P4": {"tap": tap, "note": "My decision", "at": ""}}
    )
    # Filing a strict judge flaw must not enter the old all-or-nothing comparison.
    for run, label in (("baseline", "pass"), ("new", "fail")):
        case = make_case(
            run=run,
            scenario="strap",
            question="P4",
            item="s1",
            label=label,
            reason="My decision" if label == "fail" else "",
            handoff={"stage": "pictures", "item": "s1", "question": "Staged like real life"},
            files=[],
            graded_at="",
            source="grades.json",
        )
        with (cases_dir / "P4.jsonl").open("a") as file:
            file.write(json.dumps(case) + "\n")
    result = report(store, "new", cases_dir=cases_dir)
    assert f"P4 Staged like real life: 0 of 1 pictures, then {flaws} of 1 pictures" in result.text
    assert "broke: P4" not in result.text
    if flaws:
        assert "new: strap s1 (Julian)" in result.text
    assert result.passed is ships
    assert "Waiting for Julian's tap: none" in result.text


def test_a_complaint_under_a_judge_question_adds_a_flaw_to_the_count(
    saved_runs: tuple[Store, Path],
) -> None:
    store, cases_dir = saved_runs
    store.write_judge_answers("baseline", {"strap": {"C6": {"s1": answer("pass")}}})
    store.write_judge_answers("new", {"strap": {"C6": {"s1": answer("pass")}}})
    case = make_case(
        run="new",
        scenario="strap",
        question="C6",
        item="s1",
        label="fail",
        reason="The strap passes through her leg.",
        handoff={"stage": "clips", "item": "s1", "question": "Nothing impossible"},
        files=[],
        graded_at="",
        source="grades.json",
    )
    (cases_dir / "C6.jsonl").write_text(json.dumps(case) + "\n")
    result = report(store, "new", cases_dir=cases_dir)
    assert "C6 Nothing impossible: 0 of 1 clips, then 1 of 1 clips" in result.text
    assert "new: strap s1 (Julian)" in result.text
    assert result.passed


def test_a_question_missing_on_the_baseline_is_not_called_a_new_flaw(
    saved_runs: tuple[Store, Path],
) -> None:
    store, cases_dir = saved_runs
    store.write_judge_answers("new", {"strap": {"C13": {"s1": answer("fail")}}})
    result = report(store, "new", cases_dir=cases_dir)
    section = result.text.split("- C13 ", 1)[1].split("- C14 ", 1)[0]
    assert "not judged on baseline baseline" in section
    assert "python -m evals.regression judge baseline" in section
    assert "new: none" in section
    assert result.passed


@pytest.mark.parametrize("trusted", [True, False])
def test_only_trusted_count_changes_affect_totals_and_do_not_fail_whole_ads(
    saved_runs: tuple[Store, Path], monkeypatch: pytest.MonkeyPatch, trusted: bool
) -> None:
    from evals.judge import trust

    store, cases_dir = saved_runs
    monkeypatch.setattr(
        trust, "trusted_questions", lambda **_: frozenset({"C6"}) if trusted else frozenset()
    )
    store.write_judge_answers(
        "baseline",
        {
            "strap": {"C6": {"s1": answer("pass")}, "A11": {"ad": answer("pass")}},
            "lamp": {"C6": {"s1": answer("doesnt_apply")}},
        },
    )
    store.write_judge_answers(
        "new",
        {
            "strap": {"C6": {"s1": answer("fail")}, "A11": {"ad": answer("pass")}},
            "lamp": {"C6": {"s1": answer("doesnt_apply")}},
        },
    )
    result = report(store, "new", cases_dir=cases_dir)
    expected_status = "trusted" if trusted else "not trusted yet"
    assert (
        f"C6 Nothing impossible: 0 of 1 clips, then 1 of 1 clips ({expected_status})" in result.text
    )
    assert f"{int(trusted)} broke; 0 got fixed" in result.text
    assert "Waiting for Julian's tap: strap s1 C6" in result.text
    assert "A11 Same product in every scene: 0 of 1 ads, then 0 of 1 ads" in result.text
    assert result.passed


def test_unjudged_items_are_not_reported_as_fixes(
    saved_runs: tuple[Store, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals.judge import trust

    store, cases_dir = saved_runs
    monkeypatch.setattr(trust, "trusted_questions", lambda **_: frozenset({"C6"}))
    store.write_judge_answers(
        "baseline",
        {
            "strap": {"C6": {"s1": answer("fail")}},
            "lamp": {"C6": {"s1": answer("pass")}},
        },
    )
    store.write_judge_answers(
        "new",
        {
            "strap": {"C6": {"s1": answer("unjudged", error="model refused")}},
            "lamp": {"C6": {"s1": answer("pass")}},
        },
    )
    result = report(store, "new", cases_dir=cases_dir)
    section = result.text.split("- C6 ", 1)[1].split("- C7 ", 1)[0]
    assert "gone: none" in section
    assert "0 got fixed" in result.text
    assert "Not judged: strap s1 C6: model refused" in result.text
    assert result.passed


def test_a_question_not_asked_on_this_run_does_not_count_as_a_fix(
    saved_runs: tuple[Store, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals.judge import trust

    store, cases_dir = saved_runs
    monkeypatch.setattr(trust, "trusted_questions", lambda **_: frozenset({"C6"}))
    store.write_judge_answers(
        "baseline",
        {
            "strap": {"C6": {"s1": answer("fail")}},
            "lamp": {"C6": {"s1": answer("pass")}},
        },
    )
    store.write_judge_answers("new", {"strap": {"P1": {"s1": answer("pass")}}})
    result = report(store, "new", cases_dir=cases_dir)
    assert "0 got fixed" in result.text
    assert "C6 not judged on this run" in result.text
    assert result.passed


def test_the_baseline_is_reasked_when_its_question_wording_is_old(
    saved_runs: tuple[Store, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals.judge import trust

    store, cases_dir = saved_runs
    monkeypatch.setattr(trust, "trusted_questions", lambda **_: frozenset({"C6"}))
    old_answer = answer("fail")
    old_answer["judge"] = "C6:old-prompt"
    store.write_judge_answers(
        "baseline",
        {"strap": {"C6": {"s1": old_answer}}, "lamp": {"C6": {"s1": old_answer}}},
    )
    store.write_judge_answers(
        "new",
        {"strap": {"C6": {"s1": answer("pass")}}, "lamp": {"C6": {"s1": answer("pass")}}},
    )
    result = report(store, "new", cases_dir=cases_dir)
    section = result.text.split("- C6 ", 1)[1].split("- C7 ", 1)[0]
    assert "baseline baseline uses a different judge version" in section
    assert "python -m evals.regression judge baseline" in section
    assert "gone: none" in section
    assert "0 got fixed" in result.text
    assert result.passed


def test_judge_cost_keeps_refused_calls_and_older_prompt_versions(
    saved_runs: tuple[Store, Path],
) -> None:
    store, cases_dir = saved_runs
    current = answer("pass", cost=0.02)
    current["history"] = [answer("fail", cost=0.03)]
    current["failed_asks"] = [
        {
            "error": "model refused",
            "model": "fake",
            "at": "2026-11-16T00:00:00Z",
            "input_tokens": 100,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 10,
            "cost_usd": 0.04,
        }
    ]
    store.write_judge_answers("new", {"strap": {"C6": {"s1": current}}})
    result = report(store, "new", cases_dir=cases_dir)
    assert "Judge cost this run: $0.09" in result.text


def test_a_run_without_judge_answers_keeps_its_original_report(
    saved_runs: tuple[Store, Path],
) -> None:
    store, cases_dir = saved_runs
    original = report(store, "new", cases_dir=cases_dir)
    store.write_judge_answers("baseline", {"strap": {"C6": {"s1": answer("fail")}}})
    case = make_case(
        run="new",
        scenario="strap",
        question="C6",
        item="s1",
        label="fail",
        reason="The strap crosses her leg.",
        handoff={"stage": "clips", "item": "s1", "question": "Nothing impossible"},
        files=[],
        graded_at="",
        source="grades.json",
    )
    (cases_dir / "C6.jsonl").write_text(json.dumps(case) + "\n")
    assert report(store, "new", cases_dir=cases_dir).text == original.text
    assert original.passed
    assert "Eval judge" not in original.text


def test_a_gone_flaw_names_julian_when_his_tap_cleared_it(
    saved_runs: tuple[Store, Path],
) -> None:
    store, cases_dir = saved_runs
    for run in ("baseline", "new"):
        store.write_judge_answers(run, {"strap": {"C6": {"s1": answer("fail")}}})
    store.write_judge_taps(
        "new", {"strap__s1__clip__C6": {"tap": "not_a_flaw", "note": "It stays outside.", "at": ""}}
    )
    assert "gone: strap s1 (Julian)" in report(store, "new", cases_dir=cases_dir).text
