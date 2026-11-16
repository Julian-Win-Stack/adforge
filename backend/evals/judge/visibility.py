"""The grading page and filing agree on which items Julian was shown."""

from collections.abc import Mapping, Set
from typing import Any

from evals.regression.questions import Stage, judge_questions


def item_is_shown(
    answers: Mapping[str, Mapping[str, Mapping[str, Any]]],
    stage: Stage,
    item: str,
    trusted: Set[str],
    *,
    presenter: bool = False,
) -> bool:
    """Hide only media passed by every applicable, trusted question.

    Missing answers and errors stay visible. Whole-ad grading and presenters stay
    Julian's to watch even when the judge's questions have earned trust.
    """
    questions = judge_questions(stage)
    if stage not in ("pictures", "clips") or presenter or not questions:
        return True
    return not all(
        question.id in trusted
        and answers.get(question.id, {}).get(item, {}).get("verdict") in ("pass", "doesnt_apply")
        and not answers.get(question.id, {}).get(item, {}).get("error")
        for question in questions
    )
