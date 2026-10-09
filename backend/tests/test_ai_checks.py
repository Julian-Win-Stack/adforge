"""How the rule evals' AI judges are measured against Julian's grades: on cases split so that
each rule's held-out half has some of its passes and some of its fails, and scored by how many
fails a judge catches and how many passes it lets through."""

import json
from pathlib import Path

import pytest

from evals.ai_checks import Case, RuleVerdict, ask_majority, ask_model, load_cases, score
from evals.break_passes import broken
from gateway.fake import FakeModel
from gateway.types import UnusableReply


def cases_file(tmp_path: Path, labels: list[tuple[str, str]]) -> Path:
    """A case file with one case per (rule, label), numbered in order."""
    path = tmp_path / "cases.json"
    cases = [
        {
            "id": f"{rule}-{number}",
            "rule": rule,
            "label": label,
            "source": "test",
            "scene": {"shows": f"case {number}"},
            "plan": None,
            "shop_answers": "",
        }
        for number, (rule, label) in enumerate(labels)
    ]
    path.write_text(json.dumps({"cases": cases}))
    return path


def test_half_of_each_rule_s_passes_and_fails_is_held_out(tmp_path: Path) -> None:
    labels = [("A14", "fail")] * 4 + [("A14", "pass")] * 2 + [("C1", "fail")]
    cases = load_cases(cases_file(tmp_path, labels))

    held = [(case.rule, case.label) for case in cases if case.held_out]

    assert sorted(held) == [("A14", "fail"), ("A14", "fail"), ("A14", "pass")]


def test_a_judge_is_scored_on_fails_caught_and_passes_kept(tmp_path: Path) -> None:
    labels = [("A14", "fail"), ("A14", "fail"), ("A14", "pass"), ("A14", "pass")]
    cases = load_cases(cases_file(tmp_path, labels))

    def always_fail(case: Case) -> RuleVerdict:
        return RuleVerdict(decision="fail", reason="It fails.")

    scores, wrong = score(cases, always_fail)

    assert [s.line() for s in scores] == ["A14: caught 2/2 fails, kept 0/2 passes"]
    assert sorted(case.id for case, _ in wrong) == ["A14-2", "A14-3"]


def test_a_scene_case_carries_the_start_picture_its_clip_started_from(tmp_path: Path) -> None:
    path = cases_file(tmp_path, [("A14", "fail"), ("A15", "fail")])
    raw = json.loads(path.read_text())
    raw["cases"][0]["start_picture"] = "/runs/12/scene-5/start-picture.png"
    raw["cases"][1]["scene"] = None
    path.write_text(json.dumps(raw))

    scene_case, plan_case = load_cases(path)

    assert scene_case.start_picture == Path("/runs/12/scene-5/start-picture.png")
    assert plan_case.start_picture is None


REFUSED = "gpt-6-luna's turn for rule_eval can't be used: it refused: I'm sorry, I cannot assist"


def test_a_judge_that_refuses_is_asked_once_more(fake_model: FakeModel, tmp_path: Path) -> None:
    (case,) = load_cases(cases_file(tmp_path, [("A14", "pass")]))
    fake_model.respond(
        "rule_eval",
        UnusableReply(REFUSED, input_tokens=900, output_tokens=10),
        {"decision": "pass", "reason": "The camera stays still."},
    )

    assert ask_model(case).output.decision == "pass"


def test_a_judge_that_refuses_twice_has_no_answer(fake_model: FakeModel, tmp_path: Path) -> None:
    (case,) = load_cases(cases_file(tmp_path, [("A14", "pass")]))
    fake_model.respond(
        "rule_eval",
        UnusableReply(REFUSED, input_tokens=900, output_tokens=10),
        UnusableReply(REFUSED, input_tokens=900, output_tokens=10),
    )

    with pytest.raises(UnusableReply):
        ask_model(case)


def test_a_judge_s_answer_is_the_one_most_of_three_asks_give(
    fake_model: FakeModel, tmp_path: Path
) -> None:
    (case,) = load_cases(cases_file(tmp_path, [("A14", "pass")]))
    fake_model.respond(
        "rule_eval",
        {"decision": "fail", "reason": "The camera pans."},
        {"decision": "pass", "reason": "The camera stays still."},
        {"decision": "pass", "reason": "Only the hand moves."},
    )

    verdict = ask_majority(case).output

    assert (verdict.decision, verdict.reason) == ("pass", "The camera stays still.")


def test_a_judge_whose_first_two_answers_agree_is_not_asked_a_third_time(
    fake_model: FakeModel, tmp_path: Path
) -> None:
    (case,) = load_cases(cases_file(tmp_path, [("A14", "pass")]))
    fake_model.respond(
        "rule_eval",
        {"decision": "fail", "reason": "The camera pans."},
        {"decision": "fail", "reason": "The camera zooms."},
        {"decision": "pass", "reason": "Never asked."},
    )

    assert ask_majority(case).output.reason == "The camera pans."
    assert ask_model(case).output.reason == "Never asked."


def test_a_broken_copy_can_start_from_another_real_picture(tmp_path: Path) -> None:
    path = cases_file(tmp_path, [("A8", "pass")])
    raw = json.loads(path.read_text())
    raw["cases"][0]["start_picture"] = "/runs/8/dirty-bowl.png"
    path.write_text(json.dumps(raw))
    (case,) = load_cases(path)

    copy = broken(case, [{"field": "start_picture", "value": "/runs/8/clean-bowl.png"}])

    assert (copy.start_picture, copy.label) == (Path("/runs/8/clean-bowl.png"), "fail")
