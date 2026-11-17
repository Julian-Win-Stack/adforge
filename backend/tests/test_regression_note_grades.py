"""Note-run grades become cases, the per-case pass rule, and the note report (#699).

Fixture folders follow the saved note-run layout shared by #696-#699; no other
ticket's code is used to build them.
"""

from pathlib import Path
from typing import Any

import pytest

from evals.cases import load
from evals.judge import score as judge_score
from evals.regression import filing, reports
from evals.regression.note_grades import NO_WORDS, decide
from evals.regression.store import Store, write_json

CLIPS = ("C5", "C6", "C7", "C8", "C9", "C10", "C13", "C14")
ALL_TRUSTED = frozenset({"N1", *CLIPS})
PASSING = {"note_new_ad": "pass", "note_right_scenes": "pass", "note_untouched_same": "pass"}


def clip_answer(verdict: str = "pass") -> dict[str, Any]:
    ask = {
        "verdict": verdict,
        "sees": "The bottle cap floats." if verdict == "fail" else "The cap stays on.",
        "when": "1.2 s",
        "on_what": "product" if verdict == "fail" else "nothing",
        "model": "fake-model",
        "at": "2026-11-17T01:00:00Z",
        "input_tokens": 10,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 5,
        "cost_usd": 0.1,
    }
    return {"judge": "C6:v1", "verdict": verdict, "asks": [ask] * (3 if verdict == "fail" else 1)}


def n1_answer(verdict: str = "made") -> dict[str, Any]:
    ask = {
        "verdict": verdict,
        "sees": "Scene 3 of the new ad shows the wallet closed."
        if verdict == "made"
        else "Scene 3 still shows the cash falling out.",
        "model": "fake-model",
        "at": "2026-11-17T01:00:00Z",
        "input_tokens": 100,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 50,
        "cost_usd": 0.2,
    }
    return {"judge": "N1:v1", "verdict": verdict, "asks": [ask] * (1 if verdict == "made" else 3)}


def add_case(
    store: Store,
    run: str,
    case_id: str,
    *,
    new_ad: bool = True,
    changed: tuple[int, ...] = (3,),
    code: dict[str, str] | None = None,
    cost: float = 3.5,
    minutes: float | None = 6.0,
) -> None:
    folder = store.scenario_dir(run, case_id)
    base = store.export_dir(run, case_id) / case_id
    scenes = []
    for number in (1, 2, 3):
        (base / "note-1").mkdir(parents=True, exist_ok=True)
        (base / f"note-1/before-s{number}.mp4").write_bytes(b"before clip")
        (base / f"scene-{number}.mp4").write_bytes(b"clip")
        scenes.append(
            {
                "number": number,
                "line": f"Line {number}.",
                "shows": f"Scene {number} shows the wallet.",
                "clip": f"scene-{number}.mp4",
            }
        )
    (base / "note-1/before.mp4").write_bytes(b"before ad")
    note: dict[str, Any] = {
        "number": 1,
        "text": "The cash falls out in the wallet part.",
        "before": "note-1/before.mp4",
    }
    if new_ad:
        (base / "note-1/after.mp4").write_bytes(b"after ad")
        note["after"] = "note-1/after.mp4"
    (base / "page-photo-1.png").write_bytes(b"photo")
    write_json(
        base / "regression.json",
        {
            "scenario": {"key": "ridge-wallet"},
            "page_files": ["page-photo-1.png"],
            "scenes": scenes,
            "notes": [note],
            "note_case": {
                "id": case_id,
                "expected_scenes": [3],
                "scenes": [
                    {
                        "number": number,
                        "before_clip": f"note-1/before-s{number}.mp4",
                        "after_clip": f"scene-{number}.mp4" if new_ad else None,
                        "changed": number in changed,
                    }
                    for number in (1, 2, 3)
                ],
            },
        },
    )
    write_json(
        base / "grading-records.json",
        {
            "product": "Ridge Wallet",
            "page_text": "A metal wallet.",
            "library": [{"number": 1, "label": "The wallet, front."}],
            # App verdicts sit in rich records; filing must never copy them.
            "scenes": [{"number": 3, "kind": "broll", "judge_verdict": "pass"}],
        },
    )
    verdicts = code or (
        PASSING if new_ad else {**PASSING, "note_new_ad": "fail", "note_right_scenes": "fail"}
    )
    store.write_answers(
        run,
        case_id,
        {
            "answers": {
                name: {"verdict": verdict, "reason": "", "items": []}
                for name, verdict in verdicts.items()
            },
            "cost_usd": cost,
            "minutes": minutes,
        },
    )
    write_json(folder / "case.json", {"id": case_id, "scenario": "ridge-wallet", "scenes": [3]})
    record = store.read_run(run)
    record["cases"].append(case_id)
    record["results"][case_id] = {
        "status": "done",
        "why": None,
        "cost_usd": cost,
        "minutes": minutes,
    }
    store.write_run(run, record)


def note_run(store: Store, run: str, started_at: str) -> None:
    store.write_run(
        run,
        {
            "kind": "notes",
            "name": run,
            "app_commit": "abc",
            "cases": [],
            "estimate_usd": 10.0,
            "started_at": started_at,
            "results": {},
        },
    )


def tap(value: str, note: str = "") -> dict[str, str]:
    return {"tap": value, "note": note, "at": "2026-11-17T02:00:00Z"}


@pytest.fixture
def store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "store")
    note_run(store, "notes-2", "2026-11-17T00:00:00+00:00")
    return store


def file(store: Store, cases: Path, run: str = "notes-2") -> int:
    return filing.main([run, "--cases-dir", str(cases), "--media-place", "azure"], store=store)


def test_problem_gone_taps_become_n1_cases_with_the_change_checks_inputs(
    store: Store, tmp_path: Path
) -> None:
    for case_id in ("wallet-yes", "wallet-no", "wallet-silent"):
        add_case(store, "notes-2", case_id)
    store.write_judge_answers(
        "notes-2",
        {
            "wallet-yes": {"N1": {"note": n1_answer("made")}},
            "wallet-no": {"N1": {"note": n1_answer("not_made")}},
        },
    )
    store.write_grades(
        "notes-2",
        {
            "wallet-yes__note__gone": tap("yes", "fixed"),
            "wallet-no__note__gone": tap("no", "Cash still falls out."),
            "wallet-silent__note__gone": tap("no"),
        },
    )
    cases = tmp_path / "cases"

    assert file(store, cases) == 0

    filed = {case.source["ad"]: case for case in load(cases / "N1.jsonl")}
    assert {name: case.label for name, case in filed.items()} == {
        "wallet-yes": "pass",
        "wallet-no": "fail",
        "wallet-silent": "fail",
    }
    assert filed["wallet-no"].reason == "Cash still falls out."
    assert filed["wallet-silent"].reason == NO_WORDS
    yes = filed["wallet-yes"]
    assert yes.source["round"] == "notes-2"
    assert yes.source["graded_at"] == "2026-11-17T02:00:00Z"
    assert yes.source["from"].endswith("grades.json")
    handoff = yes.inputs["handoff"]
    assert handoff["note"] == "The cash falls out in the wallet part."
    assert handoff["product_name"] == "Ridge Wallet"
    assert handoff["page_photo_labels"] == ["The wallet, front."]
    assert [scene["number"] for scene in handoff["scenes"]] == [1, 2, 3]
    assert "expected_scenes" not in str(handoff) and "judge_verdict" not in str(handoff)
    assert [Path(path).name for path in yes.inputs["files"]] == [
        "before.mp4",
        "after.mp4",
        "page-photo-1.png",
    ]
    assert all(path.startswith("azure:") for path in yes.inputs["files"])
    # The change check's counted answer, as pass/fail like every case verdict.
    assert yes.verdicts[0].verdict == "pass" and yes.verdicts[0].judge == "N1:v1"
    assert filed["wallet-no"].verdicts[0].verdict == "fail"
    assert filed["wallet-silent"].verdicts == ()

    # Re-filing adds nothing.
    assert file(store, cases) == 0
    assert len(load(cases / "N1.jsonl")) == 3


def test_score_lists_n1_after_filing(
    store: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_case(store, "notes-2", "wallet")
    store.write_judge_answers("notes-2", {"wallet": {"N1": {"note": n1_answer("not_made")}}})
    store.write_grades("notes-2", {"wallet__note__gone": tap("no", "Still falls out.")})
    cases = tmp_path / "cases"
    assert file(store, cases) == 0
    capsys.readouterr()

    assert judge_score.main(["--cases", str(cases)]) == 0

    n1 = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("N1:"))
    assert "The change the owner asked for was made" in n1
    assert "recall caught 1 of 1" in n1


def test_a_give_up_tap_is_named_not_filed(store: Store, tmp_path: Path) -> None:
    add_case(store, "notes-2", "wallet", new_ad=False)
    store.write_grades("notes-2", {"wallet__note__gone": tap("no", "no ad")})

    assert file(store, tmp_path / "cases") == 1
    assert not (tmp_path / "cases" / "N1.jsonl").exists()


def test_judge_taps_on_remade_clips_file_as_on_regression_runs(
    store: Store, tmp_path: Path
) -> None:
    add_case(store, "notes-2", "wallet-ok")
    add_case(store, "notes-2", "wallet-broke")
    store.write_judge_answers(
        "notes-2",
        {
            "wallet-ok": {"C6": {"s3": clip_answer("fail")}, "C5": {"s3": clip_answer("pass")}},
            "wallet-broke": {"C5": {"s3": clip_answer("pass")}},
        },
    )
    store.write_grades(
        "notes-2",
        {
            "wallet-ok__s3__clip__C6": {"tap": "real_flaw_ship", "note": "cap", "at": "t"},
            "wallet-ok__note__no_new_break": tap("yes"),
            "wallet-broke__note__no_new_break": tap("no", "The hand melts."),
        },
    )
    cases = tmp_path / "cases"

    assert file(store, cases) == 0

    c6 = load(cases / "C6.jsonl")
    assert [(c.source["ad"], c.inputs["handoff"]["item"], c.label) for c in c6] == [
        ("wallet-ok", "s3", "fail")
    ]
    assert c6[0].source["tap"] == "real_flaw_ship"
    assert c6[0].inputs["files"][0].endswith("/scene-3.mp4")
    # Only his explicit judge taps file: untapped passes never count toward trust.
    assert not (cases / "C5.jsonl").exists()


def test_an_untapped_judge_fail_on_a_remade_clip_waits_for_julian(
    store: Store, tmp_path: Path
) -> None:
    add_case(store, "notes-2", "wallet")
    store.write_judge_answers("notes-2", {"wallet": {"C6": {"s3": clip_answer("fail")}}})
    store.write_grades("notes-2", {"wallet__note__no_new_break": tap("yes")})

    assert file(store, tmp_path / "cases") == 1


# --- The pass rule ---------------------------------------------------------------------


def clips(verdict: str | None = "pass", **changes: str | None) -> dict[str, dict[str, Any]]:
    return {"s3": {question: changes.get(question, verdict) for question in CLIPS}}


@pytest.mark.parametrize(
    ("taps", "n1", "clip_verdicts", "trusted", "outcome", "gone", "broke"),
    [
        # Julian's taps decide.
        (
            {"problem_gone": tap("yes"), "nothing_new_broke": tap("yes")},
            None,
            {},
            set(),
            "pass",
            "yes",
            "yes",
        ),
        (
            {"problem_gone": tap("no"), "nothing_new_broke": tap("yes")},
            None,
            {},
            set(),
            "fail",
            "no",
            "yes",
        ),
        (
            {"problem_gone": tap("yes"), "nothing_new_broke": tap("no")},
            None,
            {},
            set(),
            "fail",
            "yes",
            "no",
        ),
        # His tap beats a trusted check.
        (
            {"problem_gone": tap("yes"), "nothing_new_broke": tap("yes")},
            "not_made",
            clips(C6="fail"),
            ALL_TRUSTED,
            "pass",
            "yes",
            "yes",
        ),
        # Untrusted checks never answer.
        ({}, "made", clips(), set(), "ungraded", "ungraded", "ungraded"),
        ({}, "made", clips(), ALL_TRUSTED - {"C13"}, "ungraded", "yes", "ungraded"),
        # Trusted checks answer.
        ({}, "made", clips(), ALL_TRUSTED, "pass", "yes", "yes"),
        ({}, "not_made", clips(), ALL_TRUSTED, "fail", "no", "yes"),
        ({}, "made", clips(C9="doesnt_apply"), ALL_TRUSTED, "pass", "yes", "yes"),
        # A trusted judge's flag is for Julian to look at; a missing answer is ungraded.
        ({}, "made", clips(C6="fail"), ALL_TRUSTED, "ungraded", "yes", "ungraded"),
        ({}, "made", clips(C6=None), ALL_TRUSTED, "ungraded", "yes", "ungraded"),
        ({}, None, clips(), ALL_TRUSTED, "ungraded", "ungraded", "yes"),
        # Nothing remade: nothing for the judge to say.
        ({}, "made", {}, ALL_TRUSTED, "ungraded", "yes", "ungraded"),
    ],
)
def test_pass_rule_tap_against_check(
    taps: dict[str, Any],
    n1: Any,
    clip_verdicts: dict[str, Any],
    trusted: set[str],
    outcome: str,
    gone: str,
    broke: str,
) -> None:
    decision = decide(code=PASSING, taps=taps, n1=n1, clips=clip_verdicts, trusted=trusted)

    assert (
        decision.outcome,
        decision.answers["problem_gone"],
        decision.answers["nothing_new_broke"],
    ) == (outcome, gone, broke)
    if outcome == "ungraded":
        assert decision.why


@pytest.mark.parametrize(
    ("code", "new_ad", "right_scenes"),
    [
        ({**PASSING, "note_new_ad": "fail", "note_right_scenes": "fail"}, "no", "no"),
        ({**PASSING, "note_right_scenes": "fail"}, "yes", "no"),
        ({**PASSING, "note_untouched_same": "fail"}, "yes", "no"),
    ],
)
def test_a_give_up_or_wrong_scene_fails_whatever_julian_taps(
    code: dict[str, str], new_ad: str, right_scenes: str
) -> None:
    yes = {"problem_gone": tap("yes"), "nothing_new_broke": tap("yes")}

    decision = decide(code=code, taps=yes, n1="made", clips=clips(), trusted=ALL_TRUSTED)

    assert decision.outcome == "fail"
    assert decision.answers["new_ad"] == new_ad
    assert decision.answers["right_scenes"] == right_scenes


def test_a_missing_code_answer_is_never_a_pass() -> None:
    yes = {"problem_gone": tap("yes"), "nothing_new_broke": tap("yes")}
    code = {**PASSING, "note_untouched_same": "not_reached"}

    assert decide(code=code, taps=yes, n1=None, clips={}, trusted=set()).outcome == "ungraded"


# --- The report -------------------------------------------------------------------------


def report(store: Store, cases: Path, *args: str) -> str:
    from evals.regression import note_grades

    assert note_grades.report_main(["notes-2", *args], store=store, cases_dir=cases) == 0
    return (store.run_dir("notes-2") / "report.md").read_text(encoding="utf-8")


def graded(store: Store, run: str, outcomes: dict[str, str]) -> None:
    grades = {}
    for case_id, outcome in outcomes.items():
        if outcome == "give up":
            add_case(store, run, case_id, new_ad=False, cost=1.0, minutes=None)
            continue
        add_case(store, run, case_id, cost=2.0 if run == "notes-2" else 5.0, minutes=4.0)
        grades[f"{case_id}__note__gone"] = tap("yes" if outcome == "pass" else "no", "still")
        grades[f"{case_id}__note__no_new_break"] = tap("yes")
    store.write_grades(run, grades)


def test_report_counts_flips_cost_and_minutes_against_the_last_note_run(
    store: Store, tmp_path: Path
) -> None:
    note_run(store, "notes-0", "2026-11-15T00:00:00+00:00")
    note_run(store, "notes-1", "2026-11-16T00:00:00+00:00")
    note_run(store, "notes-later", "2026-11-18T00:00:00+00:00")
    graded(store, "notes-0", {"crocs": "pass", "owala": "pass", "snow": "pass"})
    graded(store, "notes-1", {"crocs": "fail", "owala": "pass", "snow": "fail"})
    graded(store, "notes-later", {"crocs": "fail", "owala": "fail", "snow": "fail"})
    graded(store, "notes-2", {"crocs": "pass", "owala": "pass", "snow": "give up"})
    cases = tmp_path / "cases"

    text = report(store, cases)

    assert "Against: notes-1" in text
    assert "Cases: 2 passed, 1 failed, 0 ungraded (before: 1 passed, 2 failed, 0 ungraded)" in text
    assert "- New ad: 2 yes, 1 no, 0 ungraded" in text
    assert "- Problem gone: 2 yes, 0 no, 0 ungraded, 1 no new ad" in text
    assert (
        "- crocs: pass; new ad yes (code); right scenes yes (code); problem gone yes (Julian)"
        in text
    )
    assert "- crocs: fail then pass" in text
    # Snow failed both times (one try gave up): not a flip.
    assert "snow: fail then" not in text
    assert "python -m evals.regression notes notes-2-again --only crocs" in text
    assert "Fixes: $5.00 over 3 cases ($1.67 a case)" in text
    assert "Minutes: 8.0 over 2 cases (4.0 a case)" in text
    assert "N1: The change the owner asked for was made" in text
    assert any(line.startswith("C6: ") for line in text.splitlines())


def test_report_against_a_named_run_and_the_checks_scores_for_this_run(
    store: Store, tmp_path: Path
) -> None:
    note_run(store, "notes-0", "2026-11-15T00:00:00+00:00")
    note_run(store, "notes-1", "2026-11-16T00:00:00+00:00")
    graded(store, "notes-0", {"crocs": "fail"})
    graded(store, "notes-1", {"crocs": "pass"})
    graded(store, "notes-2", {"crocs": "pass"})
    store.write_judge_answers("notes-2", {"crocs": {"N1": {"note": n1_answer("not_made")}}})
    store.write_judge_answers("notes-1", {"crocs": {"N1": {"note": n1_answer("made")}}})
    cases = tmp_path / "cases"
    assert file(store, cases, "notes-1") == 0
    assert file(store, cases) == 0

    text = report(store, cases, "--against", "notes-0")

    assert "Against: notes-0" in text
    assert "- crocs: fail then pass" in text
    # Only this run's filed case counts: the check called a fixed problem not made.
    n1 = next(line for line in text.splitlines() if line.startswith("N1:"))
    assert "precision real 0 of 1" in n1 and "agreement 0 of 1" in n1
    assert "Checks: $0.60" in text


def test_report_marks_ungraded_cases_with_why(store: Store, tmp_path: Path) -> None:
    add_case(store, "notes-2", "wallet")
    store.write_judge_answers("notes-2", {"wallet": {"N1": {"note": n1_answer("made")}}})

    text = report(store, tmp_path / "cases")

    assert "Against: none (first note run)" in text
    assert "Cases: 0 passed, 0 failed, 1 ungraded" in text
    assert "problem gone: waiting for Julian's tap (change check not trusted yet)" in text


def test_the_report_command_sends_note_runs_to_the_note_report(
    store: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_case(store, "notes-2", "wallet")

    assert reports.report_main(["notes-2"], store=store) == 0

    assert "# Note run report: notes-2" in capsys.readouterr().out


def test_against_must_name_a_note_run(store: Store, tmp_path: Path) -> None:
    add_case(store, "notes-2", "wallet")
    store.write_run("baseline-2", {"name": "baseline-2", "scenarios": ["x"]})

    assert reports.report_main(["notes-2", "--against", "baseline-2"], store=store) == 1


def test_a_case_that_failed_before_saving_answers_fails_without_asking_julian(
    store: Store, tmp_path: Path
) -> None:
    add_case(store, "notes-2", "wallet")
    store.scenario_dir("notes-2", "wallet").joinpath("answers.json").unlink()
    record = store.read_run("notes-2")
    record["results"]["wallet"] = {
        "status": "failed",
        "why": "worker died",
        "cost_usd": 1.0,
        "minutes": None,
    }
    store.write_run("notes-2", record)

    text = report(store, tmp_path / "cases")

    assert "Cases: 0 passed, 1 failed, 0 ungraded" in text
    assert "- wallet: fail; new ad no (code)" in text
    assert "not finished: failed (worker died)" in text


def test_a_note_tap_for_an_unknown_case_is_named_and_exits_1(
    store: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_case(store, "notes-2", "wallet")
    store.write_grades("notes-2", {"walet__note__gone": tap("yes")})

    assert file(store, tmp_path / "cases") == 1
    assert "walet__note__gone names no case of this run" in capsys.readouterr().err


@pytest.mark.parametrize(
    "answers",
    [
        {"wallet": {"N1": {"note": {**n1_answer(), "verdict": "pass"}}}},
        {
            "wallet": {
                "N1": {
                    "note": {
                        **n1_answer(),
                        "asks": [{k: v for k, v in n1_answer()["asks"][0].items() if k != "sees"}],
                    }
                }
            }
        },
        {"wallet": {"N1": {"s3": n1_answer()}}},
    ],
    ids=["pass verdict", "no sees", "scene item"],
)
def test_change_check_answers_keep_their_own_shape(store: Store, answers: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        store.write_judge_answers("notes-2", answers)


def test_a_note_run_without_a_timezone_is_skipped_with_a_warning(
    store: Store, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    note_run(store, "notes-old", "2026-11-15T00:00:00")
    add_case(store, "notes-2", "wallet")

    text = report(store, tmp_path / "cases")

    assert "Against: none (first note run)" in text
    assert "Skipped note run notes-old" in capsys.readouterr().err
