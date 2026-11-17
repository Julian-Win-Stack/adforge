"""The eval judge's public file and catalogue contracts (#675)."""

from pathlib import Path
from typing import Any

from evals.judge.trust import trusted_questions
from evals.regression.questions import all_questions, judge_questions


def test_the_catalogue_names_the_eleven_judge_questions_and_their_inputs(tmp_path: Path) -> None:
    questions = judge_questions()
    assert {q.id for q in questions} == {
        "P1",
        "P4",
        "C5",
        "C6",
        "C7",
        "C8",
        "C9",
        "C10",
        "C13",
        "C14",
        "A11",
    }
    assert all(q.words and q.unit for q in questions)
    # The note-fix change check (#695) is scored beside them but asked only on note runs.
    assert {q.id for q in all_questions(cases_dir=tmp_path) if q.answered_by == "judge"} == {
        q.id for q in questions
    } | {"N1"}
    assert {q.id for q in judge_questions("pictures")} == {"P1", "P4"}
    assert {q.id for q in judge_questions("ad")} == {"A11"}
    for question in judge_questions("clips"):
        if question.id in ("C13", "C14"):
            assert (question.frames_per_second, question.page_photos) == (5, False)
        else:
            assert (question.frames_per_second, question.page_photos) == (10, True)
        assert question.unit == "clips"
    assert trusted_questions(cases_dir=tmp_path) == frozenset()


def test_run_judge_answers_and_taps_round_trip_and_missing_files_are_empty(tmp_path: Path) -> None:
    from evals.judge.records import tap_doc_id
    from evals.regression.store import Store

    store = Store(tmp_path)
    assert store.read_judge_answers("run") == {}
    assert store.read_judge_taps("run") == {}
    answer = {
        "ridge": {
            "P4": {
                "s1": {
                    "judge": "P4:1234",
                    "verdict": "fail",
                    "asks": [
                        {
                            "verdict": "fail",
                            "sees": "wrong slot",
                            "when": "the picture",
                            "on_what": "product",
                            "model": "test",
                            "at": "2026-11-16T00:00:00Z",
                            "input_tokens": 100,
                            "cached_tokens": 20,
                            "cache_write_tokens": 0,
                            "output_tokens": 10,
                            "cost_usd": 0.01,
                        }
                    ],
                }
            }
        }
    }
    doc = tap_doc_id("ridge", "pictures", "s1", "P4")
    assert doc == "ridge__s1__picture__P4"
    taps = {doc: {"tap": "real_flaw_ship", "note": "small but real", "at": "2026-11-16T00:00:00Z"}}
    store.write_grades("run", {"ridge__ad": {"grade": "pass", "note": "", "at": ""}})
    store.write_judge_answers("run", answer)
    store.write_judge_taps("run", taps)
    assert Store(tmp_path).read_judge_answers("run") == answer
    assert Store(tmp_path).read_judge_taps("run") == taps
    assert Store(tmp_path).read_grades("run")["ridge__ad"]["grade"] == "pass"


def test_malformed_judge_store_files_raise_instead_of_becoming_empty(tmp_path: Path) -> None:
    import pytest

    from evals.regression.store import Store, write_json

    store = Store(tmp_path)
    path = store.run_dir("run") / "judge-answers.json"
    write_json(path, {"ridge": {"P4": {"s1": {"judge": "P4:1", "verdict": "maybe", "asks": []}}}})
    with pytest.raises(ValueError):
        store.read_judge_answers("run")
    write_json(path, [])
    with pytest.raises(ValueError):
        store.read_judge_answers("run")
    store.write_grades("run", {"ridge__s1__picture__P4": {"tap": "maybe", "note": "", "at": ""}})
    with pytest.raises(ValueError):
        store.read_judge_taps("run")


def test_the_store_preserves_forms_partial_asks_and_unjudged_errors(tmp_path: Path) -> None:
    from evals.regression.store import Store

    store = Store(tmp_path)
    ask = {
        "verdict": "doesnt_apply",
        "sees": "no hand holds the product",
        "when": "",
        "on_what": "hand",
        "model": "test",
        "at": "2026-11-16T00:00:00Z",
        "input_tokens": 1,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 1,
        "cost_usd": 0.0,
        "rows": [],
    }
    answers = {
        "ridge": {
            "C13": {"s1": {"judge": "C13:x", "asks": [ask]}},
            "P1": {"s1": {"judge": "P1:x", "asks": [], "error": "refused"}},
        }
    }
    store.write_judge_answers("run", answers)
    assert store.read_judge_answers("run") == answers


def test_malformed_judge_answers_and_taps_raise(tmp_path: Path) -> None:
    import pytest

    from evals.regression.store import Store, write_json

    store = Store(tmp_path)
    malformed: Any
    for malformed in (
        [],
        {"ridge": {"P2": {"s1": {"judge": "x", "asks": []}}}},
        {"ridge": {"P1": {"ad": {"judge": "x", "asks": []}}}},
        {"ridge": {"P1": {"s1": {"judge": "x", "asks": [], "verdict": "maybe"}}}},
        {"ridge": {"P1": {"s1": {"judge": "x", "asks": [{}]}}}},
    ):
        write_json(store.run_dir("run") / "judge-answers.json", malformed)
        with pytest.raises(ValueError):
            store.read_judge_answers("run")
    write_json(store.run_dir("run") / "grades.json", {"ridge__s1__picture__P1": {"tap": "yes"}})
    with pytest.raises(ValueError):
        store.read_judge_taps("run")


def test_import_reads_judge_documents_and_keeps_stored_taps(tmp_path: Path) -> None:
    import json

    from evals.regression.__main__ import main
    from evals.regression.store import Store

    store = Store(tmp_path)
    store.write_run("new", {"name": "new", "scenarios": ["ridge"]})
    tap = {"tap": "real_flaw_ship", "note": "small but real", "at": ""}
    store.write_judge_taps("new", {"ridge__s1__picture__P1": tap})
    ordinary = {"grade": "pass", "note": "ready", "at": ""}
    source = tmp_path / "export.json"
    source.write_text(
        json.dumps(
            {
                "ridge__ad": ordinary,
                "ridge__s1__clip__C5": {"tap": "not_a_flaw", "note": "", "at": ""},
            }
        )
    )
    assert (
        main(["grading-page", "new", "--store", str(tmp_path), "--import-grades", str(source)]) == 0
    )
    assert store.read_grades("new") == {
        "ridge__ad": ordinary,
        "ridge__s1__picture__P1": tap,
        "ridge__s1__clip__C5": {"tap": "not_a_flaw", "note": "", "at": ""},
    }
    assert "ready" in store.read_grades_markdown("new")
    assert "real flaw, fine to ship" in store.read_grades_markdown("new")
    assert "not a flaw" in store.read_grades_markdown("new")


def test_the_old_filing_ignores_judge_taps_without_inventing_cases(tmp_path: Path) -> None:
    from evals.regression.__main__ import main
    from evals.regression.store import Store

    store = Store(tmp_path)
    store.write_run("new", {"name": "new", "scenarios": ["ridge"]})
    store.write_judge_taps(
        "new", {"ridge__s1__picture__P1": {"tap": "real_flaw_dont_ship", "note": "", "at": ""}}
    )
    cases = tmp_path / "cases"
    assert main(["file-grades", "new", "--store", str(tmp_path), "--cases-dir", str(cases)]) == 0
    assert not list(cases.glob("*.jsonl"))


def test_a_malformed_grades_collection_is_not_an_empty_tap_collection(tmp_path: Path) -> None:
    import pytest

    from evals.regression.store import Store, write_json

    store = Store(tmp_path)
    write_json(store.run_dir("new") / "grades.json", [])
    with pytest.raises(ValueError, match="grades.json"):
        store.read_judge_taps("new")
