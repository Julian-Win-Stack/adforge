"""Question wording copied unchanged from the approved prototype (#671)."""

from pathlib import Path

from evals.regression.questions import judge_questions, note_questions

QUESTION_DIR = Path(__file__).parent / "questions"


def question_text(question: str) -> str:
    if question not in {q.id for q in [*judge_questions(), *note_questions()]}:
        raise ValueError(f"Unknown judge question: {question!r}")
    return (QUESTION_DIR / f"{question}.md").read_text(encoding="utf-8").strip()


def shared_instructions() -> str:
    return (QUESTION_DIR / "_judge.md").read_text(encoding="utf-8").strip()


def note_instructions() -> str:
    """The change check's own instructions: it answers made or not made, not pass or fail."""
    return (QUESTION_DIR / "_note.md").read_text(encoding="utf-8").strip()
