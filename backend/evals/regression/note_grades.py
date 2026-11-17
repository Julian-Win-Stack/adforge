"""Note runs (#695): file Julian's note-run taps into cases, apply the per-case pass rule,
and report note fixes against the previous note run. No model is called.

Every input is the saved note-run layout shared by #696-#699: `run.json` with
`kind: "notes"` and `cases`, each case's export and `answers.json`, the run's
`judge-answers.json` and `grades.json`.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from evals.cases import FOLDER

from .items import export_base
from .questions import judge_questions, note_questions
from .store import Store, atomic_write_text, read_json, write_json

type Answer = Literal["yes", "no", "ungraded", "n/a"]
type Outcome = Literal["pass", "fail", "ungraded"]

CODE_ANSWERS = ("note_new_ad", "note_right_scenes", "note_untouched_same")
REPORT_QUESTIONS = (
    ("new_ad", "New ad"),
    ("right_scenes", "Right scenes"),
    ("problem_gone", "Problem gone"),
    ("nothing_new_broke", "Nothing new broke"),
)
TAPS = {"problem_gone": "gone", "nothing_new_broke": "no_new_break"}
NO_WORDS = "Julian tapped no without words."
N1 = note_questions()[0]


def is_note_run(record: Mapping[str, Any]) -> bool:
    return record.get("kind") == "notes"


def tap_id(case_id: str, which: str) -> str:
    return f"{case_id}__note__{which}"


def note_taps(grades: Mapping[str, Any], case_id: str) -> dict[str, dict[str, Any]]:
    """Julian's two taps on one case, keyed by question; a malformed tap is an error."""
    taps = {}
    for question, which in TAPS.items():
        tap = grades.get(tap_id(case_id, which))
        if tap is None:
            continue
        if (
            not isinstance(tap, dict)
            or tap.get("tap") not in ("yes", "no")
            or not isinstance(tap.get("note", ""), str)
            or not isinstance(tap.get("at", ""), str)
        ):
            raise ValueError(f"{tap_id(case_id, which)}: a note tap is yes or no, with words")
        taps[question] = tap
    return taps


def counted_n1(answer: Mapping[str, Any] | None) -> Literal["made", "not_made"] | None:
    if not answer or answer.get("error") or answer.get("verdict") not in ("made", "not_made"):
        return None
    return answer["verdict"]  # type: ignore[no-any-return]


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    answers: dict[str, Answer]
    sources: dict[str, str] = field(default_factory=dict)
    why: tuple[str, ...] = ()


def _yes_no(verdict: str | None) -> Answer:
    return {"pass": "yes", "fail": "no"}.get(verdict or "", "ungraded")  # type: ignore[return-value]


def decide(
    *,
    code: Mapping[str, str | None],
    taps: Mapping[str, Mapping[str, Any]],
    n1: Literal["made", "not_made"] | None,
    clips: Mapping[str, Mapping[str, str | None]],
    trusted: Set[str],
) -> Decision:
    """The pass rule. `code` holds the three code answers' verdicts; `clips` maps each
    remade scene to its clip questions' verdicts, Julian's judge taps already applied.

    Pass only when all three code answers pass and questions 3 and 4 are yes. Julian's
    tap always beats a check; a check answers only once it is trusted; ungraded is
    never a pass.
    """
    answers: dict[str, Answer] = {}
    sources: dict[str, str] = {}
    why: list[str] = []
    answers["new_ad"] = _yes_no(code.get("note_new_ad"))
    scenes = [_yes_no(code.get("note_right_scenes")), _yes_no(code.get("note_untouched_same"))]
    answers["right_scenes"] = (
        "no" if "no" in scenes else "yes" if scenes == ["yes", "yes"] else "ungraded"
    )
    for name in ("new_ad", "right_scenes"):
        sources[name] = "code"
        if answers[name] == "ungraded":
            why.append(f"{name.replace('_', ' ')}: no code answer yet")
    if answers["new_ad"] == "no":
        # Nothing to check or grade without a new ad.
        answers["problem_gone"] = answers["nothing_new_broke"] = "n/a"
    else:
        if "problem_gone" in taps:
            answers["problem_gone"] = "yes" if taps["problem_gone"]["tap"] == "yes" else "no"
            sources["problem_gone"] = "Julian"
        elif N1.id in trusted and n1 is not None:
            answers["problem_gone"] = "yes" if n1 == "made" else "no"
            sources["problem_gone"] = "change check"
        else:
            answers["problem_gone"] = "ungraded"
            why.append(
                "problem gone: waiting for Julian's tap"
                + ("" if N1.id in trusted else " (change check not trusted yet)")
            )
        if "nothing_new_broke" in taps:
            answers["nothing_new_broke"] = (
                "yes" if taps["nothing_new_broke"]["tap"] == "yes" else "no"
            )
            sources["nothing_new_broke"] = "Julian"
        else:
            answers["nothing_new_broke"] = "ungraded"
            clip_ids = [question.id for question in judge_questions("clips")]
            untrusted = [question for question in clip_ids if question not in trusted]
            flagged = [
                f"{scene} {question}"
                for scene, verdicts in sorted(clips.items())
                for question, verdict in verdicts.items()
                if verdict == "fail"
            ]
            unanswered = [
                f"{scene} {question}"
                for scene, verdicts in sorted(clips.items())
                for question in clip_ids
                if verdicts.get(question) not in ("pass", "fail", "doesnt_apply")
            ]
            if not clips:
                why.append("nothing new broke: no remade clips to judge")
            elif untrusted:
                why.append(
                    "nothing new broke: waiting for Julian's tap "
                    f"({', '.join(untrusted)} not trusted yet)"
                )
            elif flagged:
                why.append(
                    f"nothing new broke: judge flagged {', '.join(flagged)}; waiting for Julian"
                )
            elif unanswered:
                why.append(f"nothing new broke: not judged on {', '.join(unanswered)}")
            else:
                answers["nothing_new_broke"] = "yes"
                sources["nothing_new_broke"] = "judge"
    values = list(answers.values())
    outcome: Outcome = (
        "fail" if "no" in values else "pass" if all(v == "yes" for v in values) else "ungraded"
    )
    return Decision(outcome, answers, sources, tuple(why))


@dataclass(frozen=True)
class CaseResult:
    case: str
    decision: Decision
    cost_usd: float | None
    minutes: float | None
    check_cost_usd: float


def _clip_verdicts(
    case_id: str,
    scenes: Sequence[str],
    answers: Mapping[str, Any],
    taps: Mapping[str, Any],
) -> dict[str, dict[str, str | None]]:
    from evals.judge.records import tap_doc_id, tap_verdict

    clips: dict[str, dict[str, str | None]] = {}
    for scene in scenes:
        clips[scene] = {}
        for question in judge_questions("clips"):
            tap = taps.get(tap_doc_id(case_id, "clips", scene, question.id))
            answer = answers.get(question.id, {}).get(scene, {})
            if tap is not None:
                clips[scene][question.id] = tap_verdict(tap)
            elif answer and not answer.get("error"):
                clips[scene][question.id] = answer.get("verdict")
            else:
                clips[scene][question.id] = None
    return clips


def remade_scenes(store: Store, run: str, case_id: str) -> list[str]:
    record = read_json(export_base(store, run, case_id) / "regression.json")
    return [
        f"s{scene['number']}"
        for scene in record.get("note_case", {}).get("scenes", [])
        if scene.get("changed")
    ]


def case_results(store: Store, run: str, *, trusted: Set[str]) -> list[CaseResult]:
    from evals.judge.records import answer_cost

    record = store.read_run(run)
    grades = store.read_grades(run)
    judge_answers = store.read_judge_answers(run)
    judge_taps = store.read_judge_taps(run)
    results = []
    for case_id in record["cases"]:
        saved = store.read_answers(run, case_id)
        status = record.get("results", {}).get(case_id, {})
        code = {
            name: saved.get("answers", {}).get(name, {}).get("verdict") for name in CODE_ANSWERS
        }
        if not saved and status.get("status") in ("stopped", "failed"):
            # The case ended without a new ad: a failed fix, never a question for Julian.
            code["note_new_ad"] = "fail"
        case_answers = judge_answers.get(case_id, {})
        decision = decide(
            code=code,
            taps=note_taps(grades, case_id),
            n1=counted_n1(case_answers.get(N1.id, {}).get("note")),
            clips=_clip_verdicts(
                case_id,
                remade_scenes(store, run, case_id) if saved else [],
                case_answers,
                judge_taps,
            ),
            trusted=trusted,
        )
        if not saved:
            decision = Decision(
                decision.outcome,
                decision.answers,
                decision.sources,
                (
                    f"not finished: {status.get('status', 'not run')}"
                    + (f" ({status['why']})" if status.get("why") else ""),
                ),
            )
        cost = saved.get("cost_usd", status.get("cost_usd"))
        minutes = saved.get("minutes", status.get("minutes"))
        check_cost = sum(
            answer_cost(answer) for items in case_answers.values() for answer in items.values()
        )
        results.append(CaseResult(case_id, decision, cost, minutes, check_cost))
    return results


# --- Filing -----------------------------------------------------------------------------


def _n1_verdict(answer: Mapping[str, Any]) -> dict[str, Any]:
    from evals.judge.filing import case_verdict

    label = {"made": "pass", "not_made": "fail"}
    verdict = case_verdict(dict(answer))
    verdict["verdict"] = label[verdict["verdict"]]
    verdict["asks"] = [{**ask, "verdict": label[ask["verdict"]]} for ask in verdict["asks"]]
    return verdict


def _n1_case(
    store: Store,
    run: str,
    case_id: str,
    tap: Mapping[str, Any],
    answer: Mapping[str, Any] | None,
    media_place: str,
) -> dict[str, Any]:
    from evals.judge.inputs import page_facts

    from .cases import make_case
    from .filing import media_reference

    base = export_base(store, run, case_id)
    record = read_json(base / "regression.json")
    notes = record.get("notes", [])
    if not notes or not notes[0].get("after"):
        raise ValueError("the app made no new ad, so there is nothing to grade")
    note = notes[0]
    ads = [base / note["before"], base / note["after"]]
    for ad in ads:
        if not ad.is_file():
            raise ValueError(f"saved ad is missing: {ad}")
    facts = page_facts(store, run, case_id)
    label = "pass" if tap["tap"] == "yes" else "fail"
    words = tap.get("note", "").strip()
    case = make_case(
        run=run,
        scenario=case_id,
        question=N1.id,
        item="note",
        label=label,
        reason=words or (NO_WORDS if label == "fail" else ""),
        # The change check's own inputs. No expected scenes, app verdicts or prompts.
        handoff={
            "stage": "note",
            "item": "note",
            "question": N1.words,
            "note": note.get("text", ""),
            "product_name": facts.product_name,
            "page_photo_labels": list(facts.page_photo_labels),
            "scenes": [
                {
                    "number": scene["number"],
                    "line": scene.get("line", ""),
                    "shows": scene.get("shows", ""),
                }
                for scene in record.get("scenes", [])
            ],
        },
        files=[media_reference(path, media_place) for path in (*ads, *facts.page_photos)],
        graded_at=tap.get("at", ""),
        source=str(store.run_dir(run) / "grades.json"),
    )
    if counted_n1(answer) is not None:
        assert answer is not None
        case["verdicts"] = [_n1_verdict(answer)]
    return case


def file_note_grades(
    store: Store, run: str, *, cases_dir: Path = FOLDER, media_place: str = "project-files"
) -> int:
    """File each problem-gone tap as an N1 case and judge taps as on regression runs."""
    from evals.judge.filing import file_judge_grades

    from .filing import append_case

    record = store.read_run(run)
    cases = list(record["cases"])
    grades = store.read_grades(run)
    judge_answers = store.read_judge_answers(run)
    journal_path = store.run_dir(run) / "filed-note-grades.json"
    journal = read_json(journal_path)
    added = unresolved = 0
    for case_id in cases:
        taps = note_taps(grades, case_id)
        gone = taps.get("problem_gone")
        if gone is None:
            continue
        doc_id = tap_id(case_id, TAPS["problem_gone"])
        if doc_id not in journal:
            try:
                journal[doc_id] = _n1_case(
                    store,
                    run,
                    case_id,
                    gone,
                    judge_answers.get(case_id, {}).get(N1.id, {}).get("note"),
                    media_place,
                )
            except ValueError as error:
                print(f"{doc_id} was not filed: {error}.", file=sys.stderr)
                unresolved += 1
                continue
            write_json(journal_path, journal)
        added += append_case(cases_dir / f"{N1.id}.jsonl", journal[doc_id])
    known = {tap_id(case_id, which) for case_id in cases for which in TAPS.values()}
    for doc_id in grades:
        if "__note__" in doc_id and doc_id not in known:
            print(f"{doc_id} names no case of this run; it was not filed.", file=sys.stderr)
            unresolved += 1
    judge_added, judge_unresolved = file_judge_grades(
        store,
        run,
        cases_dir=cases_dir,
        media_place=media_place,
        complaints={},
        scenarios=cases,
        # Julian grades the case, not each remade clip: only his explicit judge taps
        # become judge cases, so no untapped answer counts toward the judge's trust.
        item_grades={},
    )
    added += judge_added
    unresolved += judge_unresolved
    print(f"{added} cases added.")
    return 1 if unresolved else 0


# --- Report -----------------------------------------------------------------------------


def previous_note_run(store: Store, run: str) -> str | None:
    """The most recent note run started before this one."""
    from .reports import instant

    started = instant(store.read_run(run)["started_at"])
    earlier = []
    for path in sorted(store.root.glob("runs/*/run.json")):
        other = read_json(path)
        if path.parent.name == run or not is_note_run(other) or not other.get("started_at"):
            continue
        try:
            when = instant(other["started_at"])
        except ValueError as error:
            print(f"Skipped note run {path.parent.name}: {error}", file=sys.stderr)
            continue
        if when < started:
            earlier.append((when, path.parent.name))
    return max(earlier)[1] if earlier else None


def _counts(results: Sequence[CaseResult], key: str | None = None) -> str:
    if key is None:
        tally = Counter(result.decision.outcome for result in results)
        return f"{tally['pass']} passed, {tally['fail']} failed, {tally['ungraded']} ungraded"
    answers = Counter(result.decision.answers[key] for result in results)
    text = f"{answers['yes']} yes, {answers['no']} no, {answers['ungraded']} ungraded"
    return text + (f", {answers['n/a']} no new ad" if answers["n/a"] else "")


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.2f}"


def _minutes(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} min"


def note_report(
    store: Store, run: str, *, against: str | None = None, cases_dir: Path = FOLDER
) -> str:
    from evals.judge import trust
    from evals.judge.score import line, read_scores

    record = store.read_run(run)
    if not is_note_run(record):
        raise ValueError(f"{run} is not a note run")
    if not record.get("cases"):
        raise ValueError(f"Note run {run!r} has no cases")
    if against is not None and not is_note_run(store.read_run(against)):
        raise ValueError(f"{against} is not a saved note run")
    previous = against if against is not None else previous_note_run(store, run)
    trusted = trust.trusted_questions(cases_dir=cases_dir)
    results = case_results(store, run, trusted=trusted)
    before = case_results(store, previous, trusted=trusted) if previous else []
    lines = [f"# Note run report: {run}", ""]
    lines.append(f"Against: {previous}" if previous else "Against: none (first note run)")
    lines.append(
        f"Cases: {_counts(results)}" + (f" (before: {_counts(before)})" if previous else "")
    )
    lines.extend(["", "## Questions", ""])
    for key, words in REPORT_QUESTIONS:
        lines.append(
            f"- {words}: {_counts(results, key)}"
            + (f" (before: {_counts(before, key)})" if previous else "")
        )
    lines.extend(["", "## Cases", ""])
    for result in results:
        decision = result.decision
        parts = [
            f"{words.lower()} {decision.answers[key]}"
            + (f" ({decision.sources[key]})" if key in decision.sources else "")
            for key, words in REPORT_QUESTIONS
        ]
        lines.append(
            f"- {result.case}: {decision.outcome}; {'; '.join(parts)}; "
            f"fix {_money(result.cost_usd)}, {_minutes(result.minutes)}"
        )
        lines.extend(f"  {why}" for why in decision.why)
    old = {result.case: result.decision.outcome for result in before}
    flipped = tuple(
        result.case
        for result in results
        if result.decision.outcome in ("pass", "fail")
        and old.get(result.case) in ("pass", "fail")
        and old[result.case] != result.decision.outcome
    )
    lines.extend(["", "## Flipped", ""])
    if flipped:
        lines.extend(
            f"- {case}: {old[case]} then {outcome}"
            for case, outcome in ((r.case, r.decision.outcome) for r in results)
            if case in flipped
        )
        lines.append(
            "One try can be luck; run them once more: "
            f"python -m evals.regression notes {run}-again --only {' '.join(flipped)}"
        )
    else:
        lines.append("none" if previous else "none (no earlier note run)")
    costs = [result.cost_usd for result in results if result.cost_usd is not None]
    minutes = [result.minutes for result in results if result.minutes is not None]
    lines.extend(
        [
            "",
            "## Cost and minutes",
            "",
            f"Fixes: ${sum(costs):.2f} over {len(costs)} cases"
            + (f" (${sum(costs) / len(costs):.2f} a case)" if costs else ""),
            f"Minutes: {sum(minutes):.1f} over {len(minutes)} cases"
            + (f" ({sum(minutes) / len(minutes):.1f} a case)" if minutes else ""),
            f"Checks: ${sum(result.check_cost_usd for result in results):.2f}",
            "",
            "## Checks against Julian (this run's cases)",
            "",
        ]
    )
    scores = read_scores(cases_dir=cases_dir, runs={run})
    for question in [*note_questions(), *judge_questions("clips")]:
        lines.append(
            line(question.id, scores.questions[question.id], trust=True, words=question.words)
        )
    text = "\n".join(lines) + "\n"
    atomic_write_text(store.run_dir(run) / "report.md", text)
    print(text, end="")
    return text


def report_main(argv: Sequence[str], *, store: Store, cases_dir: Path = FOLDER) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.regression report")
    parser.add_argument("run")
    parser.add_argument("--against", help="Compare a note run with this note run")
    args = parser.parse_args(argv)
    try:
        note_report(store, args.run, against=args.against, cases_dir=cases_dir)
    except (OSError, ValueError) as error:
        print(f"Cannot report {args.run}: {error}", file=sys.stderr)
        return 1
    return 0
