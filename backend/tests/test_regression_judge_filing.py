"""Judge disagreements become Julian's lasting cases through file-grades (#679)."""

from pathlib import Path
from typing import Any

import pytest

from evals.cases import load
from evals.judge import trust
from evals.regression import filing
from evals.regression.reports import promote
from evals.regression.store import Store, read_json, write_json
from gateway.fake import FakeModel


@pytest.mark.parametrize(
    ("stage", "question", "doc_id"),
    [("clips", "C6", "bottle__s1__clip__C6"), ("ad", "A11", "bottle__ad__A11")],
)
def test_clip_and_finished_ad_taps_keep_their_stage_and_saved_scene_media(
    judged_run: Store, tmp_path: Path, stage: str, question: str, doc_id: str
) -> None:
    cases = tmp_path / "cases"
    base = judged_run.export_dir("baseline-2", "bottle") / "bottle"
    record = read_json(base / "regression.json")
    record["scenes"][0]["clip"] = "kept.mp4"
    write_json(base / "regression.json", record)
    (base / "kept.mp4").write_bytes(b"kept scene")
    (base / "ad.mp4").write_bytes(b"whole ad")
    counted = answer()
    counted["judge"] = f"{question}:version"
    judged_run.write_judge_answers(
        "baseline-2", {"bottle": {question: {"ad" if stage == "ad" else "s1": counted}}}
    )
    judged_run.write_judge_taps(
        "baseline-2", {doc_id: {"tap": "real_flaw_ship", "note": "my words", "at": "now"}}
    )

    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0

    case = load(cases / f"{question}.jsonl")[0]
    assert case.inputs["handoff"]["stage"] == stage
    assert case.inputs["files"][0].endswith("/kept.mp4")
    assert case.verdicts[0].judge == f"{question}:version"


def answer(verdict: str = "fail") -> dict[str, Any]:
    asks = [
        {
            "verdict": verdict,
            "sees": "The cap floats above the bottle."
            if verdict == "fail"
            else "The cap stays on.",
            "when": "picture 1" if verdict == "fail" else "",
            "on_what": "product" if verdict == "fail" else "nothing",
            "model": "fake-model",
            "at": "2026-11-16T01:00:00Z",
            "input_tokens": 10,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 5,
            "cost_usd": 0.01,
        }
    ]
    return {
        "judge": "P4:version",
        "verdict": verdict,
        "asks": asks * (3 if verdict == "fail" else 1),
    }


@pytest.fixture
def judged_run(tmp_path: Path) -> Store:
    store = Store(tmp_path / "store")
    store.write_run("baseline-2", {"name": "baseline-2", "scenarios": ["bottle"]})
    base = store.export_dir("baseline-2", "bottle") / "bottle"
    write_json(
        base / "regression.json",
        {
            "scenario": {"key": "bottle", "link": "https://shop.example/bottle"},
            "page_files": ["page-photo-1.png"],
            "scenes": [
                {
                    "number": 1,
                    "line": "Bring it along.",
                    "shows": "She holds it.",
                    "picture": "picture.png",
                }
            ],
        },
    )
    (base / "picture.png").write_bytes(b"starting picture")
    (base / "page-photo-1.png").write_bytes(b"real product")
    write_json(
        base / "grading-records.json",
        {
            "product": "Bottle",
            "page_text": "A sturdy bottle.",
            "library": [{"number": 1, "label": "The cap and bottle."}],
            "scenes": [{"number": 1, "kind": "broll"}],
        },
    )
    store.write_grades("baseline-2", {})
    store.write_judge_answers("baseline-2", {"bottle": {"P4": {"s1": answer()}}})
    return store


@pytest.mark.parametrize(
    ("tap", "label", "words"),
    [
        ("not_a_flaw", "pass", "not a flaw"),
        ("real_flaw_ship", "fail", "real flaw, fine to ship"),
        ("real_flaw_dont_ship", "fail", "real flaw, don't ship"),
    ],
)
def test_each_judge_tap_files_its_label_and_all_asks_without_spending_again(
    judged_run: Store, tmp_path: Path, fake_model: FakeModel, tap: str, label: str, words: str
) -> None:
    cases = tmp_path / "cases"
    judged_run.write_judge_taps(
        "baseline-2",
        {"bottle__s1__picture__P4": {"tap": tap, "note": "my exact note", "at": "tap-time"}},
    )

    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0

    case = load(cases / "P4.jsonl")[0]
    assert case.label == label
    assert case.source["tap"] == tap
    assert case.source["graded_at"] == "tap-time"
    assert words in case.reason and "my exact note" in case.reason
    assert case.inputs["handoff"]["line"] == "Bring it along."
    assert case.inputs["handoff"]["product_name"] == "Bottle"
    assert case.inputs["handoff"]["page_photo_labels"] == ["The cap and bottle."]
    assert [Path(path.removeprefix("project-files:")).name for path in case.inputs["files"]] == [
        "picture.png",
        "page-photo-1.png",
    ]
    assert case.verdicts[0].verdict == "fail"
    assert case.verdicts[0].judge == "P4:version"
    assert case.verdicts[0].asks is not None and len(case.verdicts[0].asks) == 3
    original = (cases / "P4.jsonl").read_bytes()

    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0
    assert (cases / "P4.jsonl").read_bytes() == original
    assert not fake_model.asked


def test_a_complaint_on_a_judge_pass_files_a_miss_without_reclassifying_on_resume(
    judged_run: Store, tmp_path: Path, fake_model: FakeModel
) -> None:
    cases = tmp_path / "cases"
    judged_run.write_judge_answers("baseline-2", {"bottle": {"P4": {"s1": answer("pass")}}})
    words = "Her hand is holding the cap at the wrong end."
    judged_run.write_grades(
        "baseline-2", {"bottle__s1__picture": {"grade": "fail", "note": words, "at": "now"}}
    )
    fake_model.respond(
        "file_regression_complaint",
        {
            "questions": [],
            "new_question": {
                "id": "picture_hold_right_end",
                "question": "Is it held at the right end?",
            },
            "judge_questions": ["P4"],
        },
    )

    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0
    case = load(cases / "P4.jsonl")[0]
    assert case.label == "fail" and case.reason == words
    assert case.verdicts[0].verdict == "pass"
    assert load(cases / "picture_hold_right_end.jsonl")[0].reason == words
    assert {q["id"] for q in fake_model.asked[0].handoff.model_dump()["judge_questions"]} == {"P4"}
    original = {path.name: path.read_bytes() for path in cases.glob("*.jsonl")}

    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0
    assert {path.name: path.read_bytes() for path in cases.glob("*.jsonl")} == original
    assert len(fake_model.asked) == 1


@pytest.mark.parametrize("trusted", [False, True])
def test_only_shown_judge_passes_become_implicit_human_passes(
    judged_run: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trusted: bool
) -> None:
    cases = tmp_path / "cases"
    judged_run.write_judge_answers(
        "baseline-2",
        {"bottle": {"P1": {"s1": answer("pass")}, "P4": {"s1": answer("pass")}}},
    )
    if trusted:
        monkeypatch.setattr(trust, "trusted_questions", lambda **kwargs: frozenset({"P1", "P4"}))

    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0

    assert {path.stem for path in cases.glob("*.jsonl")} == (set() if trusted else {"P1", "P4"})
    if not trusted:
        assert all(case.label == "pass" for path in cases.glob("*.jsonl") for case in load(path))


def test_an_untapped_or_unfiled_judge_fail_prevents_promotion(
    judged_run: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = tmp_path / "cases"
    judged_run.write_run(
        "baseline-2",
        {
            "name": "baseline-2",
            "scenarios": ["bottle"],
            "to": "pictures",
            "started_at": "2026-11-16T00:00:00Z",
        },
    )
    judged_run.write_answers("baseline-2", "bottle", {"answers": {}})
    judged_run.dump_path("baseline-2", "bottle").write_bytes(b"dump")
    judged_run.mark_done("baseline-2", "bottle")

    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 1
    assert "bottle__s1__picture__P4 needs Julian's judge tap" in capsys.readouterr().err
    assert not (cases / "P4.jsonl").exists()
    assert promote(judged_run, "baseline-2", cases_dir=cases) == 1
    assert "bottle__s1__picture__P4: untapped judge fail" in capsys.readouterr().out
    judged_run.write_judge_taps(
        "baseline-2",
        {"bottle__s1__picture__P4": {"tap": "real_flaw_ship", "note": "", "at": "now"}},
    )
    assert promote(judged_run, "baseline-2", cases_dir=cases) == 1
    assert "bottle__s1__picture__P4: unfiled judge fail" in capsys.readouterr().out

    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0
    assert promote(judged_run, "baseline-2", cases_dir=cases) == 0
    assert judged_run.read_baselines() == {"bottle": "baseline-2"}


def test_a_written_grade_beats_a_trusted_judge_pass(
    judged_run: Store, tmp_path: Path, fake_model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = tmp_path / "cases"
    judged_run.write_judge_answers(
        "baseline-2", {"bottle": {"P1": {"s1": answer("pass")}, "P4": {"s1": answer("pass")}}}
    )
    monkeypatch.setattr(trust, "trusted_questions", lambda **kwargs: frozenset({"P1", "P4"}))
    words = "The lid is being held upside down."
    judged_run.write_grades(
        "baseline-2", {"bottle__s1__picture": {"grade": "fail", "note": words, "at": "now"}}
    )
    fake_model.respond(
        "file_regression_complaint",
        {
            "questions": [],
            "new_question": {
                "id": "picture_lid_right_way",
                "question": "Is the lid the right way up?",
            },
            "judge_questions": ["P4"],
        },
    )

    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0

    assert load(cases / "P4.jsonl")[0].reason == words
    assert load(cases / "P4.jsonl")[0].label == "fail"


def test_judging_an_already_filed_baseline_classifies_its_complaint_for_the_new_question_once(
    judged_run: Store, tmp_path: Path, fake_model: FakeModel
) -> None:
    cases = tmp_path / "cases"
    judged_run.write_judge_answers("baseline-2", {})
    words = "The lid is held upside down."
    judged_run.write_grades(
        "baseline-2", {"bottle__s1__picture": {"grade": "fail", "note": words, "at": "now"}}
    )
    fake_model.respond(
        "file_regression_complaint",
        {
            "questions": [],
            "new_question": {
                "id": "picture_lid_right_way",
                "question": "Is the lid the right way up?",
            },
        },
        {"questions": ["picture_lid_right_way"], "new_question": None, "judge_questions": ["P4"]},
    )
    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0
    original = (cases / "picture_lid_right_way.jsonl").read_bytes()
    judged_run.write_judge_answers("baseline-2", {"bottle": {"P4": {"s1": answer("pass")}}})

    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0

    assert load(cases / "P4.jsonl")[0].label == "fail"
    assert load(cases / "P4.jsonl")[0].reason == words
    assert (cases / "picture_lid_right_way.jsonl").read_bytes() == original
    assert len(fake_model.asked) == 2
    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0
    assert len(fake_model.asked) == 2


def test_moving_a_judge_complaint_changes_only_questions_at_the_same_stage_and_stays_moved(
    judged_run: Store, tmp_path: Path, fake_model: FakeModel
) -> None:
    cases = tmp_path / "cases"
    judged_run.write_judge_answers(
        "baseline-2", {"bottle": {"P1": {"s1": answer("pass")}, "P4": {"s1": answer()}}}
    )
    judged_run.write_judge_taps(
        "baseline-2",
        {"bottle__s1__picture__P4": {"tap": "real_flaw_ship", "note": "wrong cap", "at": "now"}},
    )
    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0
    identifier = load(cases / "P4.jsonl")[0].id
    options = [identifier, "C6", "--cases-dir", str(cases)]
    assert filing.move_main(options, store=judged_run) == 1
    assert load(cases / "P4.jsonl")[0].label == "fail"

    assert filing.move_main([identifier, "P1", "--cases-dir", str(cases)], store=judged_run) == 0

    assert load(cases / "P4.jsonl")[0].label == "pass"
    moved = load(cases / "P1.jsonl")[0]
    assert moved.label == "fail" and "wrong cap" in moved.reason
    original = {path.name: path.read_bytes() for path in cases.glob("*.jsonl")}
    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 0
    assert {path.name: path.read_bytes() for path in cases.glob("*.jsonl")} == original
    assert not fake_model.asked


def test_a_wordless_fail_never_becomes_implicit_passes_on_judge_questions(
    judged_run: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = tmp_path / "cases"
    judged_run.write_judge_answers("baseline-2", {"bottle": {"P4": {"s1": answer("pass")}}})
    judged_run.write_grades(
        "baseline-2", {"bottle__s1__picture": {"grade": "fail", "note": "", "at": "now"}}
    )

    assert filing.main(["baseline-2", "--cases-dir", str(cases)], store=judged_run) == 1

    assert "bottle__s1__picture needs words" in capsys.readouterr().err
    assert not (cases / "P4.jsonl").exists()
