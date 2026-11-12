"""Questions as filing names them from Julian's own complaints, for tests that need some."""

from pathlib import Path

from evals.cases import Case, load
from evals.regression.cases import make_case
from evals.regression.filing import append_case
from evals.regression.questions import Stage

# Each as a complaint of his once named it: its stage and its one-line question.
HIS_QUESTIONS: dict[str, tuple[Stage, str]] = {
    "page_facts_this_product": ("page", "Only this product's facts are kept"),
    "plan_claims_backed": ("plan", "Every claim comes from the page or the owner"),
    "plan_real_use": ("plan", "The product is used the way the page shows"),
    "viewer_knows_product": ("plan", "The viewer can tell what the product is"),
    "picture_real_product": ("pictures", "It's the real product"),
    "picture_possible": ("pictures", "Nothing in the picture is impossible"),
    "clip_real_product": ("clips", "It's the real product"),
    "clip_possible": ("clips", "Nothing bends or floats in an impossible way"),
    "clip_substance_real": ("clips", "Foam, cream or liquid behaves like the real thing"),
    "clip_camera_natural": ("clips", "The camera moves naturally"),
    "consistent_across_scenes": ("ad", "The product stays the same in every scene"),
    "presenter_real_person": ("ad", "The presenter feels like a real person"),
    "note_done": ("note", "The note was done"),
}


def his_questions(cases_dir: Path, *ids: str) -> None:
    """Start each of `ids` (default: all of HIS_QUESTIONS) as an earlier filing did: a case
    file whose first case names the question and its stage."""
    for question in ids or tuple(HIS_QUESTIONS):
        stage, words = HIS_QUESTIONS[question]
        item = {"pictures": "s1", "clips": "s1", "note": "note1"}.get(stage, stage)
        append_case(
            cases_dir / f"{question}.jsonl",
            make_case(
                run="earlier",
                scenario="seed",
                question=question,
                item=item,
                label="pass",
                reason="",
                handoff={"question": words, "stage": stage, "scenario": "seed", "item": item},
                files=[],
                graded_at="2026-11-01T00:00:00Z",
                source="earlier/grades.json",
            ),
        )


def filed(path: Path) -> list[Case]:
    """The cases in `path` filed from a run's grades, without the question's starting case."""
    return (
        [case for case in load(path) if case.source["round"] != "earlier"] if path.exists() else []
    )


def all_filed(cases_dir: Path) -> list[Case]:
    return [case for path in sorted(cases_dir.glob("*.jsonl")) for case in filed(path)]
