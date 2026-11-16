"""One source for which judge questions have earned Julian's trust.

The scoring ticket (#677) supplies the score here. Until then, every item stays on
Julian's grading page and every judge count is marked not trusted yet.
"""

from pathlib import Path

from evals.cases import FOLDER


def trusted_questions(*, cases_dir: Path = FOLDER, judge: str | None = None) -> frozenset[str]:
    return frozenset()
