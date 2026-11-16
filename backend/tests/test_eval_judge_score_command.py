"""The free score command reads saved cases and shares its trust decision with callers."""

import json
from pathlib import Path
from typing import Any

import pytest

from evals.judge.score import main
from evals.judge.trust import trusted_questions
from evals.regression.cases import make_case
from evals.regression.questions import judge_questions


def write_case(
    folder: Path,
    question: str,
    *,
    scenario: str,
    label: str,
    verdict: str | None,
    run: str = "baseline-2",
    stage: str = "pictures",
    item: str = "s1",
    judge: str = "j1",
    **changes: Any,
) -> dict[str, Any]:
    row = make_case(
        run=run,
        scenario=scenario,
        question=question,
        item=item,
        label=label,
        reason="his complaint" if label == "fail" else "",
        handoff={
            "stage": stage,
            "item": item,
            "line": "It is easy to hold.",
            "shows": "The product is held up.",
            "product_name": "Mug",
            "product_text": "A ceramic mug.",
            "page_photo_labels": ["Front"],
        },
        files=["project-files:picture.png", "project-files:front.png"],
        graded_at="2026-11-16T00:00:00Z",
        source="grades.json",
    )
    if verdict is not None:
        row["verdicts"] = [
            {
                "verdict": verdict,
                "reason": "what it sees",
                "judge": judge,
                "model": "fake",
                "at": "2026-11-16T00:00:00Z",
            }
        ]
    row.update(changes)
    path = folder / f"{question}.jsonl"
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row) + "\n")
    return row


def test_command_scores_each_question_and_each_picture_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for scenario, label, verdict in [
        ("caught", "fail", "fail"),
        ("missed", "fail", "pass"),
        ("false-alarm", "pass", "fail"),
    ]:
        write_case(tmp_path, "P4", scenario=scenario, label=label, verdict=verdict)
        write_case(tmp_path, "P1", scenario=scenario, label="pass", verdict="pass")

    assert main(["--cases", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    for question in judge_questions():
        assert f"{question.id}:" in out
    p4 = next(line for line in out.splitlines() if line.startswith("P4:"))
    items = next(line for line in out.splitlines() if line.startswith("items:"))
    for line in (p4, items):
        assert "caught 1 of 2 (50.0%)" in line
        assert "real 1 of 2 (50.0%)" in line
        assert "agreement 1 of 3" in line
        assert "kappa -0.500" in line
    assert "not trusted yet" in p4


def test_command_and_shared_trust_use_all_current_labels_and_the_same_bar(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pairs = (
        [("fail", "fail")] * 8
        + [("fail", "pass")] * 2
        + [("pass", "fail")]
        + [("pass", "pass")] * 19
    )
    halves = set()
    for number, (label, verdict) in enumerate(pairs):
        row = write_case(tmp_path, "P4", scenario=f"product-{number}", label=label, verdict=verdict)
        halves.add(row["half"])

    assert halves == {"tune", "score"}
    assert main(["--cases", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    p4 = next(line for line in out.splitlines() if line.startswith("P4:"))
    assert "caught 8 of 10 (80.0%)" in p4
    assert "real 8 of 9 (88.9%)" in p4
    assert p4.endswith("; trusted")
    assert trusted_questions(cases_dir=tmp_path) == frozenset({"P4"})
    assert trusted_questions(cases_dir=tmp_path, judge="j1") == frozenset({"P4"})
    assert trusted_questions(cases_dir=tmp_path, judge="not-asked") == frozenset()


def test_old_rounds_and_non_binary_verdicts_never_improve_the_score(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for run in ("9", "round-10", "r11", "12", "13", "14", "15", "baseline-1"):
        write_case(tmp_path, "P4", scenario=run, label="fail", verdict="fail", run=run)
    for scenario, verdict in [
        ("not-applicable", "doesnt_apply"),
        ("unsure", "unsure"),
        ("missing", None),
    ]:
        write_case(tmp_path, "P4", scenario=scenario, label="fail", verdict=verdict)
    write_case(
        tmp_path, "P4", scenario="newer-run", label="fail", verdict="pass", run="regression-4"
    )

    assert main(["--cases", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    p4 = next(line for line in out.splitlines() if line.startswith("P4:"))
    assert "caught 0 of 1 (0.0%)" in p4
    assert "agreement 0 of 1" in p4
    assert "not judged 2" in p4
    assert "doesn't apply 1" in p4
    assert not trusted_questions(cases_dir=tmp_path)


def test_judge_filter_uses_one_versions_verdicts_and_leaves_the_rest_unjudged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    row = write_case(tmp_path, "P4", scenario="changed", label="fail", verdict="pass", judge="new")
    row["verdicts"].insert(0, {**row["verdicts"][0], "judge": "old", "verdict": "fail"})
    (tmp_path / "P4.jsonl").write_text(json.dumps(row) + "\n")
    write_case(tmp_path, "P4", scenario="new-only", label="pass", verdict="fail", judge="new")

    assert main(["--cases", str(tmp_path), "--judge", "old"]) == 0
    out = capsys.readouterr().out
    p4 = next(line for line in out.splitlines() if line.startswith("P4:"))
    assert "caught 1 of 1 (100.0%)" in p4
    assert "real 1 of 1 (100.0%)" in p4
    assert "not judged 1" in p4
    assert main(["--cases", str(tmp_path)]) == 0
    latest = capsys.readouterr().out
    p4 = next(line for line in latest.splitlines() if line.startswith("P4:"))
    assert "caught 0 of 1 (0.0%)" in p4
    assert "real 0 of 1 (0.0%)" in p4


def test_item_only_fails_use_the_question_verdicts_and_never_invent_a_question(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_case(tmp_path, "judge_picture_item", scenario="no-words", label="fail", verdict=None)
    write_case(tmp_path, "P1", scenario="no-words", label="pass", verdict="pass")
    write_case(tmp_path, "P4", scenario="no-words", label="pass", verdict="pass")

    assert main(["--cases", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    p4 = next(line for line in out.splitlines() if line.startswith("P4:"))
    items = next(line for line in out.splitlines() if line.startswith("items:"))
    assert "caught 0 of 0 (n/a)" in p4
    assert "caught 0 of 1 (0.0%)" in items
    assert "agreement 0 of 1" in items


def test_partial_item_answers_never_count_as_an_item_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_case(tmp_path, "P4", scenario="partial", label="fail", verdict="pass")
    write_case(tmp_path, "P4", scenario="missing-labels", label="pass", verdict="fail")

    assert main(["--cases", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    items = next(line for line in out.splitlines() if line.startswith("items:"))
    assert "agreement 0 of 0" in items
    assert "not judged 2" in items


def test_a_picture_and_clip_of_the_same_scene_are_distinct_items(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for question in ("P1", "P4"):
        write_case(tmp_path, question, scenario="mug", label="pass", verdict="pass")
    for question in ("C5", "C6", "C7", "C8", "C9", "C10", "C13", "C14"):
        label = "fail" if question == "C6" else "pass"
        verdict = "doesnt_apply" if question == "C8" else label
        write_case(tmp_path, question, scenario="mug", label=label, verdict=verdict, stage="clips")

    assert main(["--cases", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    items = next(line for line in out.splitlines() if line.startswith("items:"))
    assert "caught 1 of 1 (100.0%)" in items
    assert "agreement 2 of 2" in items


def test_malformed_cases_refuse_the_command_before_printing_any_scores(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_case(tmp_path, "P4", scenario="broken", label="pass", verdict="maybe")

    assert main(["--cases", str(tmp_path)]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"{tmp_path / 'P4.jsonl'}:1:" in captured.err
    assert "maybe" in captured.err


def test_both_real_flaw_taps_are_catches_even_when_the_ad_can_ship(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for tap, label in [
        ("real_flaw_ship", "fail"),
        ("real_flaw_dont_ship", "fail"),
        ("not_a_flaw", "pass"),
    ]:
        row = write_case(tmp_path, "P4", scenario=tap, label=label, verdict="fail")
        row["source"]["tap"] = tap
        path = tmp_path / "P4.jsonl"
        lines = path.read_text().splitlines()
        lines[-1] = json.dumps(row)
        path.write_text("\n".join(lines) + "\n")

    assert main(["--cases", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    p4 = next(line for line in out.splitlines() if line.startswith("P4:"))
    assert "caught 2 of 2 (100.0%)" in p4
    assert "real 2 of 3 (66.7%)" in p4


@pytest.mark.parametrize(
    ("other_verdict", "caught", "agreed"),
    [
        ("pass", "caught 0 of 1 (0.0%)", "agreement 0 of 1"),
        ("fail", "caught 1 of 1 (100.0%)", "agreement 1 of 1"),
    ],
)
def test_a_doesnt_apply_answer_cannot_erase_julians_item_fail(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    other_verdict: str,
    caught: str,
    agreed: str,
) -> None:
    write_case(tmp_path, "P1", scenario="mug", label="fail", verdict="doesnt_apply")
    write_case(tmp_path, "P4", scenario="mug", label="pass", verdict=other_verdict)

    assert main(["--cases", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    items = next(line for line in out.splitlines() if line.startswith("items:"))
    assert caught in items
    assert agreed in items


def test_an_item_with_no_applicable_judge_answers_stays_out_of_the_score(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_case(tmp_path, "P1", scenario="mug", label="fail", verdict="doesnt_apply")
    write_case(tmp_path, "P4", scenario="mug", label="pass", verdict="doesnt_apply")

    assert main(["--cases", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    items = next(line for line in out.splitlines() if line.startswith("items:"))
    assert "agreement 0 of 0" in items
    assert "doesn't apply 1" in items
