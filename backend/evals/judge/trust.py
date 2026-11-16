"""One source for which judge questions have earned Julian's trust from saved grades."""

from pathlib import Path

from evals.cases import FOLDER
from evals.judge.score import read_scores


def trusted_questions(*, cases_dir: Path = FOLDER, judge: str | None = None) -> frozenset[str]:
    scores = read_scores(cases_dir=cases_dir, judge=judge)
    return frozenset(
        question for question, result in scores.questions.items() if result.score.trusted
    )
