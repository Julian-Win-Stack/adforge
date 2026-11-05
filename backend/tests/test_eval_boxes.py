"""The eval home's case format and its 4-box count (issue #386): a case file is refused when
any case in it is malformed, and the count puts each case in one box by its newest verdict, or
by the verdict of the judge asked for."""

import json
from pathlib import Path
from typing import Any

import pytest

from evals.boxes import count, main
from evals.cases import BadCase, half_for, load


def case(case_id: str = "r9-mudbuster-clip-3936", **changes: Any) -> dict[str, Any]:
    """A well-formed case, with `changes` laid over it."""
    made: dict[str, Any] = {
        "id": case_id,
        "kind": "capability",
        "half": half_for(case_id),
        "inputs": {"handoff": {"scene": 3}, "files": ["project-files:runs/round-9/clip.mp4"]},
        "label": "fail",
        "reason": "invented use",
        "source": {
            "round": "9",
            "ad": "mudbuster",
            "graded_at": "2026-11-04T10:00:00Z",
            "from": "grades.md",
        },
        "verdicts": [],
    }
    made.update(changes)
    return made


def verdict(said: str, judge: str = "j1", reason: str = "looks fine") -> dict[str, str]:
    return {
        "verdict": said,
        "reason": reason,
        "judge": judge,
        "model": "m",
        "at": "2026-11-05T00:00:00Z",
    }


def write(folder: Path, question: str, cases: list[dict[str, Any]]) -> Path:
    path = folder / f"{question}.jsonl"
    path.write_text("".join(json.dumps(each) + "\n" for each in cases))
    return path


def ids_in(half: str) -> list[str]:
    """Case ids whose fixed half is `half`."""
    return [f"case-{n}" for n in range(200) if half_for(f"case-{n}") == half]


def test_the_half_is_fixed_by_the_id_and_splits_cases_between_both_halves() -> None:
    assert half_for("r9-mudbuster-clip-3936") == half_for("r9-mudbuster-clip-3936")
    assert ids_in("tune") and ids_in("score")


def test_a_well_formed_file_loads_and_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = write(tmp_path, "clip_product", [case(verdicts=[verdict("pass")])])
    path.write_text(path.read_text() + "\n\n")

    [loaded] = load(path)

    assert loaded.id == "r9-mudbuster-clip-3936"
    assert loaded.verdicts[0].verdict == "pass"


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"label": "unsure"}, "label"),
        ({"kind": "maybe"}, "kind"),
        ({"reason": None}, "reason"),
        ({"source": {"round": "9"}}, "source"),
        ({"inputs": {"handoff": {}, "files": ["dropbox:clip.mp4"]}}, "dropbox:clip.mp4"),
        ({"inputs": {"handoff": {}, "files": ["mac:"]}}, "mac:"),
        ({"inputs": {"files": []}}, "handoff"),
        ({"verdicts": [{"verdict": "pass"}]}, "verdict 1"),
        ({"verdicts": [verdict("maybe")]}, "verdict 1"),
        ({"labels": "fail"}, "labels"),
    ],
)
def test_a_malformed_case_is_refused_naming_the_file_and_line(
    tmp_path: Path, changes: dict[str, Any], problem: str
) -> None:
    path = write(tmp_path, "clip_product", [case("good-one"), case(**changes)])

    with pytest.raises(BadCase) as refused:
        load(path)

    assert f"{path}:2:" in str(refused.value)
    assert problem in str(refused.value)


def test_a_case_missing_a_field_is_refused(tmp_path: Path) -> None:
    broken = case()
    del broken["verdicts"]
    path = write(tmp_path, "clip_product", [broken])

    with pytest.raises(BadCase, match=r":1: .*verdicts"):
        load(path)


def test_a_half_picked_by_hand_is_refused(tmp_path: Path) -> None:
    wrong = "score" if half_for("r9-mudbuster-clip-3936") == "tune" else "tune"
    path = write(tmp_path, "clip_product", [case(half=wrong)])

    with pytest.raises(BadCase, match="half"):
        load(path)


def test_a_line_that_is_not_json_or_a_repeated_id_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "clip_product.jsonl"
    path.write_text("{not json\n")
    with pytest.raises(BadCase, match=":1: "):
        load(path)

    path = write(tmp_path, "clip_product", [case("same"), case("same")])
    with pytest.raises(BadCase, match=":2: .*same"):
        load(path)


def test_each_case_lands_in_one_box_by_its_newest_verdict(tmp_path: Path) -> None:
    [a, b, c, d, e, f] = ids_in("score")[:6]
    path = write(
        tmp_path,
        "clip_product",
        [
            case(a, label="pass", kind="regression", verdicts=[verdict("fail"), verdict("pass")]),
            case(b, label="fail", verdicts=[verdict("fail")]),
            case(
                c,
                label="pass",
                kind="regression",
                verdicts=[verdict("fail", reason="wrong colour")],
            ),
            case(d, label="fail", verdicts=[verdict("pass")]),
            case(e, label="fail", verdicts=[verdict("unsure")]),
            case(f, label="pass", kind="regression"),
        ],
    )

    boxes = count(load(path))

    assert boxes.halves["score"].as_dict() == {
        "both pass": 1,
        "both fail": 1,
        "false alarm": 1,
        "miss": 1,
        "unsure": 1,
        "no verdict": 1,
    }
    assert sum(boxes.halves["tune"].as_dict().values()) == 0
    assert [(seen.case.id, seen.box) for seen in boxes.disagreements] == [
        (c, "false alarm"),
        (d, "miss"),
        (e, "unsure"),
    ]
    assert boxes.kinds == {"capability": 3, "regression": 3}


def test_a_judge_asked_for_uses_its_newest_verdict_and_no_verdict_without_one(
    tmp_path: Path,
) -> None:
    [a, b] = ids_in("tune")[:2]
    path = write(
        tmp_path,
        "clip_product",
        [
            case(
                a,
                label="fail",
                verdicts=[verdict("pass", "old"), verdict("fail", "new"), verdict("pass", "newer")],
            ),
            case(b, label="fail", verdicts=[verdict("fail", "new")]),
        ],
    )
    cases = load(path)

    assert count(cases, judge="old").halves["tune"].as_dict()["miss"] == 1
    assert count(cases, judge="old").halves["tune"].as_dict()["no verdict"] == 1
    assert count(cases, judge="new").halves["tune"].as_dict()["both fail"] == 2


def test_the_command_prints_the_boxes_and_each_disagreement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    [a] = ids_in("score")[:1]
    write(
        tmp_path,
        "clip_product",
        [
            case(
                a,
                label="fail",
                reason="the bag has no clasp",
                verdicts=[verdict("pass", reason="same bag")],
            )
        ],
    )

    assert main(["clip_product", "--cases", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "clip_product" in out
    assert "judge passes his fail (miss)" in out
    assert a in out and "the bag has no clasp" in out and "same bag" in out


def test_the_command_refuses_a_malformed_file_and_an_unknown_question(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path, "clip_product", [case(label="meh")])

    assert main(["--cases", str(tmp_path)]) == 2
    assert "clip_product.jsonl:1:" in capsys.readouterr().err

    assert main(["no_such_question", "--cases", str(tmp_path)]) == 2
    assert "no_such_question" in capsys.readouterr().err
