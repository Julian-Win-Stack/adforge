"""How the rule evals' AI judges are measured against Julian's grades: on cases split so that
each rule's held-out half has some of its passes and some of its fails, and scored by how many
fails a judge catches and how many passes it lets through."""

import json
from pathlib import Path

import pytest

from adforge.retry import OutsideServiceDown
from evals.ai_checks import (
    Case,
    RuleVerdict,
    ask_majority,
    ask_model,
    ask_or_count_as,
    load_cases,
    score,
)
from evals.break_passes import broken
from gateway.fake import FakeModel
from gateway.types import UnusableReply


def cases_file(
    tmp_path: Path, labels: list[tuple[str, str]], splits: list[str] | None = None
) -> Path:
    """A case file with one case per (rule, label), numbered in order, each in the half
    `splits` names, or tuned on."""
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
            "split": splits[number] if splits else "tune",
        }
        for number, (rule, label) in enumerate(labels)
    ]
    path.write_text(json.dumps({"cases": cases}))
    return path


def test_a_case_is_held_out_when_its_file_says_so(tmp_path: Path) -> None:
    labels = [("A14", "fail"), ("A14", "fail"), ("A14", "pass")]
    cases = load_cases(cases_file(tmp_path, labels, ["tune", "held out", "held out"]))

    assert [case.id for case in cases if case.held_out] == ["A14-1", "A14-2"]


def test_a_half_with_no_fails_says_so_rather_than_scoring_them(tmp_path: Path) -> None:
    cases = load_cases(cases_file(tmp_path, [("A9", "pass")]))

    scores, _ = score(cases, lambda case: RuleVerdict(decision="pass", reason="It stands."))

    assert [s.line() for s in scores] == ["A9: no fails, kept 1/1 passes"]


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


def test_a_judge_that_refuses_twice_in_one_ask_is_asked_again(
    fake_model: FakeModel, tmp_path: Path
) -> None:
    (case,) = load_cases(cases_file(tmp_path, [("A14", "pass")]))
    fake_model.respond(
        "rule_eval",
        UnusableReply(REFUSED, input_tokens=900, output_tokens=10),
        UnusableReply(REFUSED, input_tokens=900, output_tokens=10),
        {"decision": "fail", "reason": "The camera pans."},
        {"decision": "pass", "reason": "The camera stays still."},
        {"decision": "pass", "reason": "Only the hand moves."},
    )

    # Counted as a vote, the refusal would have made it fail.
    assert ask_majority(case).output.decision == "pass"


def test_a_judge_that_never_answers_has_no_answer(fake_model: FakeModel, tmp_path: Path) -> None:
    (case,) = load_cases(cases_file(tmp_path, [("A14", "pass")]))
    # Each ask tries twice, and 4 asks without an answer is one more than VOTES: 8 refusals each time it is asked.
    fake_model.respond(
        "rule_eval", *[UnusableReply(REFUSED, input_tokens=900, output_tokens=10)] * 16
    )

    with pytest.raises(UnusableReply):
        ask_majority(case)
    assert ask_or_count_as(case, "gpt-5-mini", "fail").output.decision == "fail"


def test_a_judge_is_asked_again_while_the_provider_is_down(
    fake_model: FakeModel, tmp_path: Path
) -> None:
    (case,) = load_cases(cases_file(tmp_path, [("A14", "pass")]))
    fake_model.respond(
        "rule_eval",
        OutsideServiceDown("Azure timed out"),
        {"decision": "pass", "reason": "The camera stays still."},
    )

    assert ask_model(case).output.decision == "pass"


def test_a_judge_whose_provider_stays_down_counts_as_no_answer(
    fake_model: FakeModel, tmp_path: Path
) -> None:
    (case,) = load_cases(cases_file(tmp_path, [("A14", "pass")]))
    fake_model.respond("rule_eval", *[OutsideServiceDown("Azure timed out")] * 3)

    verdict = ask_or_count_as(case, "gpt-5-mini", "pass").output

    assert (verdict.decision, verdict.reason[:17]) == ("pass", "NO USABLE ANSWER:")


def test_a_broken_copy_edits_a_plan_scene(tmp_path: Path) -> None:
    path = cases_file(tmp_path, [("A15", "pass")])
    raw = json.loads(path.read_text())
    raw["cases"][0]["scene"] = None
    raw["cases"][0]["plan"] = [{"line": "Hi."}, {"line": "It seals with one click."}]
    path.write_text(json.dumps(raw))
    (case,) = load_cases(path)

    copy = broken(case, [{"field": "plan.1.line", "find": "one click", "replace": "a twist"}])

    assert copy.handoff.plan == [{"line": "Hi."}, {"line": "It seals with a twist."}]


def test_a_broken_copy_whose_text_is_missing_is_never_judged(tmp_path: Path) -> None:
    (case,) = load_cases(cases_file(tmp_path, [("A14", "pass")]))

    with pytest.raises(AssertionError, match="not in shows"):
        broken(case, [{"field": "shows", "find": "the camera pans", "replace": "x"}])
